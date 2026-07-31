"""
Stock Outward service — dispatch / transfer stock out of the warehouse.

build: create a draft outward, resolving each scanned barcode / product to an
       inventory product (so we can show current stock and cost).
post:  append one negative StockMovement per line (kind='outward') and reduce
       each product's stock. Guards against dispatching more than on hand.
       Idempotent — an outward posts once.

Mirrors the app's Stock Outward screen (From company/location, Packed By, a To
destination, and a sent vs accepted qty per line). Accepted qty defaults to the
sent qty; a lower accepted qty is recorded as a transfer discrepancy.
"""
import datetime as dt
from .. import models


def _next_code(db):
    n = db.query(models.StockOutward).count() + 1
    return f"OUT-{n:05d}"


def resolve_product(db, barcode=None, product_id=None, description=None):
    if product_id:
        return db.get(models.Product, product_id)
    if barcode:
        p = db.query(models.Product).filter(models.Product.barcode == barcode).first()
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
            accepted_qty=ln.get("accepted_qty", qty), rate=prod.avg_cost or prod.last_rate or 0,
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
    if outward.status == "posted":
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
