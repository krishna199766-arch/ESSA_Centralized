"""
Purchase Return service (Warehouse / Purchase Return — debit note).

build_from_purchase(purchase): draft return pre-filled with what was RECEIVED on
    that invoice — one row per stock item, so a billed bundle that was broken
    down at GRN comes back as its variants (L / Red, M / Blue …) rather than as
    the bundle line, which never became stock and cannot be returned as itself.
    Return-qty defaults to 0; the user sets how many of each to send back.
post(ret): for each line with qty > 0, reverse stock (negative StockMovement,
    kind='return') and value the debit note. The debit total reduces the
    supplier's payable for the referenced invoice (see payments.invoice_outstanding).
Idempotent — a return posts once.

VALUATION — the rule this module exists to enforce
--------------------------------------------------
A debit note is valued at the PURCHASE / RECEIVED price: the GRN cost of the
exact item going back. Never the sale price, never the MRP. Two reasons, and both
bite in money:

  * A debit note settles a supplier account. The supplier invoiced us at their
    rate; we can only debit them back at that same rate, or the payable stops
    reconciling against their ledger and the difference surfaces as a dispute.
  * Valuing a return at the retail price would credit us the margin on goods we
    never sold, overstating both the settlement and the stock write-back.

`grn_cost()` below is the only place that decides the figure, and `post()`
re-derives every line from the GRN before valuing — so a draft raised before a
correction, or a rate that got stale in some other way, cannot reach a posted
debit note. Nothing in this module ever reads Product.sale_price or Product.mrp.
"""
import datetime as dt
from .. import models


def _next_code(db):
    n = db.query(models.PurchaseReturn).count() + 1
    return f"PR-{n:05d}"


def _effective_tax_rate(purchase):
    """tax / taxable from the reference purchase, so the debit note carries the
    same GST the invoice did."""
    if purchase and purchase.taxable_total:
        return (purchase.tax_total or 0) / purchase.taxable_total
    return 0.0


# ---------------------------------------------------------------------------
#  Cost basis
# ---------------------------------------------------------------------------
def grn_cost(line=None, split=None, product=None):
    """The unit price this item was RECEIVED at — the debit note's only basis.

    Preference order, most specific first:
      1. the variant's own rate from the GRN breakdown — a bundle billed as one
         rate can still have been broken down with a different rate per variant,
         and that is what this piece actually cost;
      2. the invoice line rate, i.e. what the supplier billed per unit;
      3. the product's weighted-average purchase cost, for a return line with no
         surviving GRN row behind it.

    Every one of those is a price we PAID. Product.sale_price and Product.mrp are
    deliberately absent: see the module docstring."""
    candidates = [
        split.effective_rate if split is not None else None,
        line.rate if line is not None else None,
        product.avg_cost if product is not None else None,
    ]
    for v in candidates:
        if v is not None and float(v) > 0:
            return round(float(v), 4)
    return 0.0


def cost_source(line=None, split=None, product=None):
    """Which of the three the rate came from — shown on screen so the basis of a
    debit note is visible, not just asserted."""
    if split is not None and split.rate is not None and float(split.rate) > 0:
        return "grn_variant_rate"
    if line is not None and line.rate and float(line.rate) > 0:
        return "invoice_line_rate"
    if product is not None and product.avg_cost and float(product.avg_cost) > 0:
        return "weighted_avg_cost"
    return "unknown"


def _receivers(purchase):
    """The rows of a GRN that actually became stock, as (line, split|None).

    A line that was broken down did NOT receive stock — its variants did — so
    returning "the line" would reverse a quantity against a product that never
    existed. This is what makes the returnable set the received set."""
    out = []
    for line in purchase.lines:
        if line.is_split:
            out.extend((line, sp) for sp in line.splits)
        else:
            out.append((line, None))
    return out


def source_of(db, l):
    """The GRN row a return line came back from: (purchase_line, split|None).

    Falls back to matching on the product for rows created before the link
    columns existed, so an old draft still re-values from the right receipt."""
    line = db.get(models.PurchaseLine, l.purchase_line_id) if l.purchase_line_id else None
    split = db.get(models.PurchaseLineSplit, l.split_id) if l.split_id else None
    if line or split:
        return (line or (split.line if split else None)), split
    purchase = l.ret.purchase if l.ret else None
    if not (purchase and l.product_id):
        return None, None
    for pl, sp in _receivers(purchase):
        holder = sp if sp is not None else pl
        if holder.product_id == l.product_id:
            return pl, sp
    return None, None


def _source_key(l):
    """Identifies the received row a return line consumes, for tallying how much
    of it has already gone back across several debit notes."""
    if l.split_id:
        return ("split", l.split_id)
    if l.purchase_line_id:
        return ("line", l.purchase_line_id)
    return ("product", l.product_id)


def returnable(db, ret, l):
    """{purchased, already_returned, available} for one return line.

    'Already returned' counts POSTED debit notes other than this one against the
    same invoice, so two partial returns of the same item can't quietly add up to
    more than was bought."""
    line, split = source_of(db, l)
    holder = split if split is not None else line
    purchased = float(getattr(holder, "qty", 0) or 0) if holder is not None else 0.0
    prior = 0.0
    if ret.purchase_id:
        rows = db.query(models.PurchaseReturnLine).join(
            models.PurchaseReturn,
            models.PurchaseReturnLine.return_id == models.PurchaseReturn.id).filter(
            models.PurchaseReturn.purchase_id == ret.purchase_id,
            models.PurchaseReturn.status == "posted",
            models.PurchaseReturn.id != ret.id).all()
        key = _source_key(l)
        prior = sum(float(r.qty or 0) for r in rows if _source_key(r) == key)
    return {"purchased": round(purchased, 3), "already_returned": round(prior, 3),
            "available": round(purchased - prior, 3)}


# ---------------------------------------------------------------------------
#  Build / edit / post
# ---------------------------------------------------------------------------
def build_from_purchase(db, purchase):
    existing = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.purchase_id == purchase.id,
        models.PurchaseReturn.status == "draft").first()
    if existing:
        return apply_grn_rates(db, existing)

    ret = models.PurchaseReturn(
        code=_next_code(db), supplier_id=purchase.supplier_id,
        purchase_id=purchase.id, invoice_number=purchase.invoice_number,
        date=None, status="draft",
    )
    db.add(ret)
    db.flush()
    for pl, sp in _receivers(purchase):
        holder = sp if sp is not None else pl
        product = db.get(models.Product, holder.product_id) if holder.product_id else None
        label = sp.variant_label if sp is not None else None
        db.add(models.PurchaseReturnLine(
            return_id=ret.id, product_id=holder.product_id,
            purchase_line_id=pl.id, split_id=sp.id if sp is not None else None,
            # our SKU when the item has one — that is the code on the goods; the
            # supplier's printed code only when they gave us one
            barcode=(product.sku or product.barcode) if product else pl.barcode,
            description=f"{pl.description} · {label}" if label else pl.description,
            hsn=pl.hsn, uom=pl.uom,
            qty=0.0, rate=grn_cost(pl, sp, product), amount=0.0,
        ))
    db.flush()
    return ret


def apply_grn_rates(db, ret):
    """Re-derive every line's rate from the GRN it came from.

    Run on open and again at post, so the rate a debit note is valued at is
    always the received price as the GRN records it TODAY — not whatever was
    copied onto the draft when it was raised."""
    for l in ret.lines:
        line, split = source_of(db, l)
        product = db.get(models.Product, l.product_id) if l.product_id else None
        rate = grn_cost(line, split, product)
        if rate and rate != l.rate:
            l.rate = rate
            l.amount = round(float(l.qty or 0) * rate, 2)
        if line is not None and not l.purchase_line_id:      # backfill the links
            l.purchase_line_id = line.id
        if split is not None and not l.split_id:
            l.split_id = split.id
    db.flush()
    return ret


def set_lines(db, ret, line_qtys):
    """line_qtys: {return_line_id: qty}. Recomputes each line amount.

    Quantities only — a rate is never accepted from the client. What an item cost
    is a fact of its GRN, so letting a screen post one would be letting it decide
    what the supplier is debited."""
    for l in ret.lines:
        if l.id in line_qtys:
            q = float(line_qtys[l.id] or 0)
            l.qty = q
            l.amount = round(q * (l.rate or 0), 2)
    db.flush()
    return ret


def post(db, ret, reason=None, date=None):
    if ret.status == "posted":
        return {"ok": False, "error": "already posted"}
    apply_grn_rates(db, ret)              # value at the GRN cost, always
    active = [l for l in ret.lines if (l.qty or 0) > 0]
    if not active:
        return {"ok": False, "error": "no lines to return (set a return qty > 0)"}

    # A debit note for more than the invoice delivered is not a settlement, it is
    # an overclaim — and it would reverse stock that this receipt never brought in.
    over = []
    for l in active:
        r = returnable(db, ret, l)
        if r["purchased"] and float(l.qty) > r["available"] + 1e-6:
            over.append(f"“{(l.description or '')[:40]}”: returning {float(l.qty):g} "
                        f"of {r['available']:g} available"
                        + (f" ({r['purchased']:g} received, {r['already_returned']:g} "
                           f"already returned)" if r["already_returned"] else ""))
    if over:
        return {"ok": False, "error": "more than was received — " + "; ".join(over)}

    taxable = 0.0
    for l in active:
        prod = db.get(models.Product, l.product_id) if l.product_id else None
        qty = float(l.qty or 0)
        l.amount = round(qty * (l.rate or 0), 2)
        if prod:
            prod.stock_qty = round((prod.stock_qty or 0) - qty, 3)
            db.add(models.StockMovement(
                product_id=prod.id, qty_delta=-qty, kind="return",
                ref_type="purchase_return", ref_id=ret.id,
                rate=l.rate or prod.avg_cost or 0, balance_after=prod.stock_qty,
                note=f"Purchase return {ret.code} → {ret.supplier.name if ret.supplier else ''}".strip(),
            ))
        taxable += l.amount or 0

    rate = _effective_tax_rate(ret.purchase)
    ret.taxable_total = round(taxable, 2)
    ret.tax_total = round(taxable * rate, 2)
    ret.total = round(ret.taxable_total + ret.tax_total, 2)
    ret.reason = reason or ret.reason
    ret.date = date or ret.date
    ret.status = "posted"
    ret.posted_at = dt.datetime.utcnow()
    db.flush()
    return {"ok": True, "return_id": ret.id, "debit_total": ret.total,
            "taxable_total": ret.taxable_total, "tax_total": ret.tax_total,
            "cost_basis": "grn", "lines": len(active)}


def returns_against_purchase(db, purchase_id):
    rows = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.purchase_id == purchase_id,
        models.PurchaseReturn.status == "posted").all()
    return round(sum(r.total or 0 for r in rows), 2)
