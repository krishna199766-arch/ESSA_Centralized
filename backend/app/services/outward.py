"""
Stock Outward / Stock Inward service — the two ends of one transfer.

build:    create a draft outward, resolving each scanned code / product to an
          inventory product (so the screen can show the full record — QR, name,
          size, colour, batch — before anything is packed).
post:     append one negative StockMovement per line (kind='outward') and reduce
          each product's stock. Guards against dispatching more than on hand.
          Idempotent — an outward posts once.
receive:  the STOCK INWARD side. The destination counts what turned up and
          accepts it, line by line, against the same document. Accepted qty
          defaults to the sent qty; anything less is a transfer discrepancy,
          recorded rather than silently absorbed.

Mirrors the app's pair of screens (Warehouse / Stock Outward → Store / Stock
Inward, which prints a Goods Transfer note): From company/location, Packed By, a
To destination, Received By, and a sent vs accepted qty per line.
"""
import datetime as dt
from .. import models
from . import barcode_svc


def _next_code(db):
    n = db.query(models.StockOutward).count() + 1
    return f"OUT-{n:05d}"


def resolve_product(db, barcode=None, product_id=None, description=None):
    """Find the product a dispatch line refers to.

    `barcode` is whatever was scanned or typed — a product QR payload, a
    per-piece garment label, our SKU, or the supplier's printed code. All of them
    go through barcode_svc.resolve, so the picker can scan the tag on the item
    itself instead of looking up a code by hand."""
    if product_id:
        return db.get(models.Product, product_id)
    if barcode:
        p = barcode_svc.resolve(db, barcode)
        if p:
            return p
    if description:
        return db.query(models.Product).filter(models.Product.description == description).first()
    return None


def create_outward(db, payload):
    """payload: {date, to_destination, packed_by, received_by, from_location,
                 lines:[{barcode|product_id, qty, accepted_qty}]}"""
    o = models.StockOutward(
        code=_next_code(db), date=payload.get("date"),
        to_destination=payload.get("to_destination"),
        packed_by=payload.get("packed_by"), received_by=payload.get("received_by"),
        from_location=payload.get("from_location", "WAREHOUSE"),
        status="draft",
    )
    db.add(o)
    db.flush()
    for ln in payload.get("lines", []):
        prod = resolve_product(db, ln.get("barcode"), ln.get("product_id"), ln.get("description"))
        if not prod:
            continue
        qty = float(ln.get("qty") or 0)
        db.add(models.StockOutwardLine(
            outward_id=o.id, product_id=prod.id, barcode=prod.barcode,
            description=prod.description, qty=qty,
            accepted_qty=ln.get("accepted_qty"),
            rate=prod.avg_cost or prod.last_rate or 0,
        ))
    db.flush()
    return o


def validate_stock(db, outward):
    """Return a list of lines that would go negative if posted."""
    problems = []
    for l in outward.lines:
        prod = db.get(models.Product, l.product_id)
        if prod and (l.qty or 0) > (prod.stock_qty or 0):
            problems.append({"product": prod.description, "requested": l.qty,
                             "on_hand": prod.stock_qty})
    return problems


def post_outward(db, outward, allow_negative=False):
    if outward.status != "draft":
        return {"ok": False, "error": "already posted"}
    problems = validate_stock(db, outward)
    if problems and not allow_negative:
        return {"ok": False, "error": "insufficient_stock", "problems": problems}

    for l in outward.lines:
        prod = db.get(models.Product, l.product_id)
        if not prod:
            continue
        qty = float(l.qty or 0)
        prod.stock_qty = round((prod.stock_qty or 0) - qty, 3)
        db.add(models.StockMovement(
            product_id=prod.id, qty_delta=-qty, kind="outward",
            ref_type="outward", ref_id=outward.id, rate=l.rate or prod.avg_cost or 0,
            balance_after=prod.stock_qty,
            note=f"Outward {outward.code} → {outward.to_destination or ''}".strip(),
        ))
    outward.status = "posted"
    outward.posted_at = dt.datetime.utcnow()
    db.flush()
    return {"ok": True, "outward_id": outward.id, "lines": len(outward.lines),
            "total_qty": outward.total_qty}


# ---------------------------------------------------------------------------
#  Stock Inward — the destination accepting a dispatch
# ---------------------------------------------------------------------------
def receive_outward(db, outward, accepted=None, received_by=None, date=None):
    """Record what the destination actually took in.

    `accepted`: {line_id: qty}. A line left out is accepted in full — the common
    case is that the whole box is right, and making someone re-key every line to
    say so invites the opposite error.

    Accepting fewer than were sent is recorded, NOT corrected: the stock has
    already left this warehouse, and where the missing pieces are (in transit,
    damaged, miscounted at one end) is a question for a human. The shortfall is
    reported so it can be settled deliberately — with a stock adjustment if the
    goods come back, or written off if they don't."""
    if outward.status == "draft":
        return {"ok": False, "error": "this outward hasn't been dispatched yet — "
                                      "post it before receiving it"}
    if outward.status == "received":
        return {"ok": False, "error": "already received"}

    accepted = {int(k): v for k, v in (accepted or {}).items()}
    for l in outward.lines:
        sent = float(l.qty or 0)
        if l.id in accepted:
            raw = accepted[l.id]
            try:
                q = float(raw if raw not in (None, "") else 0)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"“{l.description}”: accepted quantity "
                                              f"must be a number"}
            if q < 0:
                return {"ok": False,
                        "error": f"“{l.description}”: accepted quantity can't be negative"}
            if q > sent:
                return {"ok": False,
                        "error": f"“{l.description}”: {q:g} accepted but only {sent:g} "
                                 f"were sent — a transfer can't grow in transit"}
            l.accepted_qty = q
        else:
            l.accepted_qty = sent

    outward.received_by = received_by or outward.received_by
    outward.received_date = date or outward.received_date
    outward.received_at = dt.datetime.utcnow()
    outward.status = "received"
    db.flush()
    short = [{"line_id": l.id, "product_id": l.product_id,
              "description": l.description, "sent": l.qty,
              "accepted": l.accepted_qty, "short": l.short_qty}
             for l in outward.lines if l.short_qty > 0]
    return {"ok": True, "outward_id": outward.id, "lines": len(outward.lines),
            "total_qty": outward.total_qty, "accepted_qty": outward.total_accepted,
            "shortfall": outward.shortfall, "discrepancies": short}


def verify_code(db, outward, code):
    """Resolve a code scanned while checking a transfer in or out.

    Answers the question the person holding the garment is actually asking — "is
    this one of the items on this note?" — rather than just "what is this?". A
    scan that resolves to a product NOT on the document is the error worth
    catching, so it comes back as matched=False with the product still named."""
    product = barcode_svc.resolve(db, code)
    if not product:
        return {"ok": False, "error": f"nothing matches the code “{code}”"}
    line = next((l for l in outward.lines if l.product_id == product.id), None)
    return {"ok": True, "matched": line is not None,
            "line_id": line.id if line else None, "product_id": product.id}
