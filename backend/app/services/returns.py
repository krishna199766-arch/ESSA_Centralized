"""
Purchase Return service (Warehouse / Purchase Return — debit note).

build_from_purchase(purchase): draft return pre-filled with the invoice's lines,
    return-qty defaulting to 0 (returns are usually partial). The user sets how
    many of each line to send back.
post(ret): for each line with qty > 0, reverse stock (negative StockMovement,
    kind='return') and value the debit note. The debit total reduces the
    supplier's payable for the referenced invoice (see payments.invoice_outstanding).
Idempotent — a return posts once.
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


def build_from_purchase(db, purchase):
    existing = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.purchase_id == purchase.id,
        models.PurchaseReturn.status == "draft").first()
    if existing:
        return existing

    ret = models.PurchaseReturn(
        code=_next_code(db), supplier_id=purchase.supplier_id,
        purchase_id=purchase.id, invoice_number=purchase.invoice_number,
        date=None, status="draft",
    )
    db.add(ret)
    db.flush()
    for pl in purchase.lines:
        db.add(models.PurchaseReturnLine(
            return_id=ret.id, product_id=pl.product_id, barcode=pl.barcode,
            description=pl.description, hsn=pl.hsn, qty=0.0, rate=pl.rate,
            amount=0.0,
        ))
    db.flush()
    return ret


def set_lines(db, ret, line_qtys):
    """line_qtys: {return_line_id: qty}. Recomputes each line amount."""
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
    active = [l for l in ret.lines if (l.qty or 0) > 0]
    if not active:
        return {"ok": False, "error": "no lines to return (set a return qty > 0)"}

    taxable = 0.0
    for l in active:
        prod = db.get(models.Product, l.product_id) if l.product_id else None
        qty = float(l.qty or 0)
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
            "lines": len(active)}


def returns_against_purchase(db, purchase_id):
    rows = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.purchase_id == purchase_id,
        models.PurchaseReturn.status == "posted").all()
    return round(sum(r.total or 0 for r in rows), 2)
