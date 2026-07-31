"""
Reporting service — read-only views over the structured data, mirroring the
report catalogue in the reference app's Warehouse Reports screen.

Each report returns {"columns": [...], "rows": [...], "totals": {...}} so the UI
and CSV export can render any of them generically.
"""
from collections import defaultdict
from .. import models
from . import payments as pay


def _rep(columns, rows, totals=None):
    return {"columns": columns, "rows": rows, "totals": totals or {}}


def stock_report(db):
    cols = ["sku", "barcode", "description", "hsn", "supplier", "uom", "stock_qty", "avg_cost", "stock_value"]
    rows, tqty, tval = [], 0.0, 0.0
    for p in db.query(models.Product).order_by(models.Product.description).all():
        rows.append({"sku": p.sku, "barcode": p.barcode or "", "description": p.description,
                     "hsn": p.hsn or "", "supplier": p.primary_supplier.name if p.primary_supplier else "",
                     "uom": p.uom, "stock_qty": p.stock_qty, "avg_cost": round(p.avg_cost or 0, 2),
                     "stock_value": p.stock_value})
        tqty += p.stock_qty or 0; tval += p.stock_value
    return _rep(cols, rows, {"stock_qty": round(tqty, 2), "stock_value": round(tval, 2), "products": len(rows)})


def stock_movement(db, kind=None, product_id=None):
    cols = ["date", "sku", "description", "kind", "qty_delta", "rate", "balance_after", "ref", "note"]
    q = db.query(models.StockMovement)
    if kind:
        q = q.filter(models.StockMovement.kind == kind)
    if product_id:
        q = q.filter(models.StockMovement.product_id == product_id)
    rows = []
    for m in q.order_by(models.StockMovement.id).all():
        prod = m.product
        rows.append({"date": m.created_at.strftime("%Y-%m-%d") if m.created_at else "",
                     "sku": prod.sku if prod else "", "description": prod.description if prod else "",
                     "kind": m.kind, "qty_delta": m.qty_delta, "rate": round(m.rate or 0, 2),
                     "balance_after": m.balance_after, "ref": f"{m.ref_type or ''} {m.ref_id or ''}".strip(),
                     "note": m.note or ""})
    inward = sum(m["qty_delta"] for m in rows if m["qty_delta"] > 0)
    outward = sum(-m["qty_delta"] for m in rows if m["qty_delta"] < 0)
    return _rep(cols, rows, {"movements": len(rows), "total_in": round(inward, 2), "total_out": round(outward, 2)})


def purchase_register(db):
    cols = ["date", "supplier", "invoice_number", "taxable", "tax", "grand_total", "paid", "returns", "outstanding"]
    rows, tg, to = [], 0.0, 0.0
    for p in db.query(models.Purchase).filter(models.Purchase.status == "posted").order_by(models.Purchase.invoice_date).all():
        settled = pay.invoice_settled(db, p.id)
        rets = pay.invoice_returns(db, p.id)
        outstanding = pay.invoice_outstanding(db, p)
        rows.append({"date": p.invoice_date or "", "supplier": p.supplier.name if p.supplier else "",
                     "invoice_number": p.invoice_number, "taxable": round(p.taxable_total or 0, 2),
                     "tax": round(p.tax_total or 0, 2), "grand_total": round(p.grand_total or 0, 2),
                     "paid": settled, "returns": rets, "outstanding": outstanding})
        tg += p.grand_total or 0; to += outstanding
    return _rep(cols, rows, {"purchases": len(rows), "grand_total": round(tg, 2), "outstanding": round(to, 2)})


def purchase_return_register(db):
    cols = ["date", "code", "supplier", "invoice_number", "taxable", "tax", "total", "status"]
    rows, tt = [], 0.0
    for r in db.query(models.PurchaseReturn).order_by(models.PurchaseReturn.id).all():
        rows.append({"date": r.date or "", "code": r.code, "supplier": r.supplier.name if r.supplier else "",
                     "invoice_number": r.invoice_number or "", "taxable": round(r.taxable_total or 0, 2),
                     "tax": round(r.tax_total or 0, 2), "total": round(r.total or 0, 2), "status": r.status})
        if r.status == "posted":
            tt += r.total or 0
    return _rep(cols, rows, {"returns": len(rows), "total": round(tt, 2)})


def supplier_pending_bills(db):
    cols = ["supplier", "gstin", "pending_bills", "outstanding"]
    rows, tot = [], 0.0
    for s in db.query(models.Supplier).order_by(models.Supplier.name).all():
        bills = pay.pending_bills(db, s.id)
        if not bills:
            continue
        out = round(sum(b["outstanding"] for b in bills), 2)
        rows.append({"supplier": s.name, "gstin": s.gstin or "", "pending_bills": len(bills), "outstanding": out})
        tot += out
    return _rep(cols, rows, {"suppliers": len(rows), "outstanding": round(tot, 2)})


def payments_register(db):
    cols = ["date", "receipt_no", "supplier", "mode", "ref_no", "gross", "discount", "tds", "debit", "paid"]
    rows, tp = [], 0.0
    for p in db.query(models.Payment).order_by(models.Payment.id).all():
        rows.append({"date": p.date or "", "receipt_no": p.receipt_no,
                     "supplier": p.supplier.name if p.supplier else "", "mode": p.mode,
                     "ref_no": p.ref_no or "", "gross": round(p.gross_amount or 0, 2),
                     "discount": round(p.discount_total or 0, 2), "tds": round(p.tds_total or 0, 2),
                     "debit": round(p.debit_adjust_total or 0, 2), "paid": round(p.paid_amount or 0, 2)})
        tp += p.paid_amount or 0
    return _rep(cols, rows, {"payments": len(rows), "paid": round(tp, 2)})


def product_master(db):
    cols = ["sku", "barcode", "description", "hsn", "uom", "mrp", "avg_cost", "stock_qty", "supplier"]
    rows = [{"sku": p.sku, "barcode": p.barcode or "", "description": p.description, "hsn": p.hsn or "",
             "uom": p.uom, "mrp": p.mrp or "", "avg_cost": round(p.avg_cost or 0, 2), "stock_qty": p.stock_qty,
             "supplier": p.primary_supplier.name if p.primary_supplier else ""}
            for p in db.query(models.Product).order_by(models.Product.description).all()]
    return _rep(cols, rows, {"products": len(rows)})


def supplier_master(db):
    cols = ["name", "gstin", "state", "state_code", "phone", "email", "bank"]
    rows = []
    for s in db.query(models.Supplier).order_by(models.Supplier.name).all():
        bank = (s.bank or {}).get("name", "") if isinstance(s.bank, dict) else ""
        rows.append({"name": s.name, "gstin": s.gstin or "", "state": s.state or "",
                     "state_code": s.state_code or "", "phone": s.phone or "", "email": s.email or "",
                     "bank": bank})
    return _rep(cols, rows, {"suppliers": len(rows)})


def tax_master(db):
    """HSN-wise summary: how many products, current stock value under each HSN."""
    cols = ["hsn", "products", "stock_qty", "stock_value"]
    agg = defaultdict(lambda: {"products": 0, "stock_qty": 0.0, "stock_value": 0.0})
    for p in db.query(models.Product).all():
        h = p.hsn or "(none)"
        agg[h]["products"] += 1
        agg[h]["stock_qty"] += p.stock_qty or 0
        agg[h]["stock_value"] += p.stock_value
    rows = [{"hsn": h, "products": v["products"], "stock_qty": round(v["stock_qty"], 2),
             "stock_value": round(v["stock_value"], 2)} for h, v in sorted(agg.items())]
    return _rep(cols, rows, {"hsn_codes": len(rows)})


REPORTS = {
    "stock_report": ("Stock Report", "stock", stock_report),
    "stock_movement": ("Stock Movement", "stock", stock_movement),
    "purchase_register": ("Purchase Register", "purchase", purchase_register),
    "purchase_return_register": ("Purchase Return Register", "purchase", purchase_return_register),
    "supplier_pending_bills": ("Supplier Pending Bills", "purchase", supplier_pending_bills),
    "payments_register": ("Payments Register", "finance", payments_register),
    "product_master": ("Product Master", "master", product_master),
    "supplier_master": ("Supplier Master", "master", supplier_master),
    "tax_master": ("Tax / HSN Master", "master", tax_master),
}


def catalogue():
    return [{"key": k, "name": n, "group": g} for k, (n, g, _) in REPORTS.items()]


def run(db, key, **kw):
    if key not in REPORTS:
        return None
    fn = REPORTS[key][2]
    try:
        return fn(db, **kw)
    except TypeError:
        return fn(db)
