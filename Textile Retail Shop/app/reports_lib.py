"""The shop's reports, as data rather than as pages.

Each report knows what it counts and returns the same shape —
{columns, rows, totals, note} — so one renderer draws all of them and a question
asked in English or Tamil can be answered by picking one and filling in its
dates. `note` is where a report says the thing that would otherwise be assumed
wrong: that returns are netted off the seller, that stock value is at cost, that
a walk-in has no customer to name.

Adding a report here makes it answerable by the ask bar automatically — the
router's list of keys is built from REPORTS, so the two cannot drift apart.
"""
from datetime import date, timedelta

from sqlalchemy import func

from app import db
from app.models import (Alteration, CreditNote, Category, Customer, Invoice,
                        InvoiceItem, LoyaltyTxn, Product, User)


def _inv_in(start, end):
    return db.session.query(Invoice).filter(
        func.date(Invoice.invoice_date) >= start,
        func.date(Invoice.invoice_date) <= end)


def _money(v):
    return round(float(v or 0), 2)


# ---- the reports ----------------------------------------------------------

def sales_summary(start, end):
    rows, total, count = [], 0.0, 0
    for d, n, amt, tax in db.session.query(
            func.date(Invoice.invoice_date), func.count(Invoice.id),
            func.sum(Invoice.total), func.sum(Invoice.cgst + Invoice.sgst + Invoice.igst)
    ).filter(func.date(Invoice.invoice_date) >= start,
             func.date(Invoice.invoice_date) <= end
             ).group_by(func.date(Invoice.invoice_date)).order_by(func.date(Invoice.invoice_date)).all():
        rows.append([d, n, _money(amt), _money(tax)])
        total += _money(amt)
        count += n
    return {"columns": ["Date", "Bills", "Sales", "Tax"], "rows": rows,
            "totals": {"Bills": count, "Sales": _money(total)},
            "note": "Every bill raised in the period, including any later returned."}


def sales_by_product(start, end):
    rows = db.session.query(
        Product.sku, Product.name, func.sum(InvoiceItem.quantity),
        func.sum(InvoiceItem.line_total + InvoiceItem.tax_amount)
    ).join(InvoiceItem, InvoiceItem.product_id == Product.id
           ).join(Invoice, Invoice.id == InvoiceItem.invoice_id
                  ).filter(func.date(Invoice.invoice_date) >= start,
                           func.date(Invoice.invoice_date) <= end
                           ).group_by(Product.id).order_by(func.sum(
                               InvoiceItem.line_total + InvoiceItem.tax_amount).desc()).all()
    out = [[s, n, round(float(q), 2), _money(r)] for s, n, q, r in rows]
    return {"columns": ["SKU", "Item", "Qty sold", "Revenue"], "rows": out,
            "totals": {"Revenue": _money(sum(r[3] for r in out))},
            "note": "Revenue includes tax. Returns are not deducted here."}


def sales_by_category(start, end):
    rows = db.session.query(
        Category.name, func.sum(InvoiceItem.quantity),
        func.sum(InvoiceItem.line_total + InvoiceItem.tax_amount)
    ).join(Product, Product.category_id == Category.id
           ).join(InvoiceItem, InvoiceItem.product_id == Product.id
                  ).join(Invoice, Invoice.id == InvoiceItem.invoice_id
                         ).filter(func.date(Invoice.invoice_date) >= start,
                                  func.date(Invoice.invoice_date) <= end
                                  ).group_by(Category.id).order_by(func.sum(
                                      InvoiceItem.line_total + InvoiceItem.tax_amount).desc()).all()
    out = [[c, round(float(q), 2), _money(r)] for c, q, r in rows]
    return {"columns": ["Category", "Qty sold", "Revenue"], "rows": out,
            "totals": {"Revenue": _money(sum(r[2] for r in out))},
            "note": "Categories are the warehouse's master codes."}


def sales_by_staff(start, end):
    out = []
    for u in User.query.order_by(User.full_name).all():
        served = db.or_(Invoice.staff_id == u.id,
                        db.and_(Invoice.staff_id.is_(None), Invoice.cashier_id == u.id))
        sold = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
            served, func.date(Invoice.invoice_date) >= start,
            func.date(Invoice.invoice_date) <= end).scalar() or 0
        back = db.session.query(func.coalesce(func.sum(CreditNote.total), 0)).join(
            Invoice, CreditNote.invoice_id == Invoice.id).filter(
            served, func.date(CreditNote.created_at) >= start,
            func.date(CreditNote.created_at) <= end).scalar() or 0
        if not sold and not back:
            continue
        net = _money(sold - back)
        out.append([u.full_name, u.staff_code, _money(sold), _money(back), net,
                    _money(net * (u.commission_pct or 0) / 100.0)])
    out.sort(key=lambda r: r[4], reverse=True)
    return {"columns": ["Staff", "Code", "Sold", "Returned", "Net sales", "Commission"],
            "rows": out, "totals": {"Net sales": _money(sum(r[4] for r in out)),
                                    "Commission": _money(sum(r[5] for r in out))},
            "note": "Credited to whoever served the sale; returns come off that "
                    "same person, not whoever handled the refund."}


def sales_by_customer(start, end):
    rows = db.session.query(
        Customer.name, Customer.phone, func.count(Invoice.id), func.sum(Invoice.total)
    ).join(Invoice, Invoice.customer_id == Customer.id
           ).filter(func.date(Invoice.invoice_date) >= start,
                    func.date(Invoice.invoice_date) <= end
                    ).group_by(Customer.id).order_by(func.sum(Invoice.total).desc()).all()
    out = [[n, p or "—", c, _money(t)] for n, p, c, t in rows]
    walkins = _inv_in(start, end).filter(Invoice.customer_id.is_(None)).count()
    return {"columns": ["Customer", "Phone", "Bills", "Spent"], "rows": out,
            "totals": {"Spent": _money(sum(r[3] for r in out))},
            "note": f"{walkins} bill(s) were walk-ins with no customer attached, "
                    "so they are not in this list."}


def sales_by_payment(start, end):
    """What was taken, by tender.

    Summed from the settlement rows rather than grouped on the invoice's
    one-word label: a bill paid half in cash and half on a card is labelled
    "mixed", and grouping on that would drop its cash out of the cash line — so
    the drawer would stop reconciling to this report the day split payments
    arrived. `Invoice.settled` falls back to the single method for every bill
    raised before settlements were recorded.
    """
    totals, counts = {}, {}
    for inv in _inv_in(start, end).all():
        for method, amount in inv.settled.items():
            totals[method] = round(totals.get(method, 0.0) + amount, 2)
            counts[method] = counts.get(method, 0) + 1
    out = [[(m or "—").capitalize(), counts[m], totals[m]]
           for m in sorted(totals, key=lambda k: -totals[k])]
    return {"columns": ["Payment", "Bills", "Amount"],
            "rows": [[m, c, _money(t)] for m, c, t in out],
            "totals": {"Amount": _money(sum(r[2] for r in out))},
            "note": "How the money came in, before any refunds. A split bill "
                    "appears under each tender it used, so Bills can exceed the "
                    "number of sales."}


def gst_summary(start, end):
    invoices = _inv_in(start, end).all()
    rows = [["CGST", _money(sum(i.cgst for i in invoices))],
            ["SGST", _money(sum(i.sgst for i in invoices))],
            ["IGST", _money(sum(i.igst for i in invoices))]]
    taxable = _money(sum(i.subtotal - (i.discount or 0) for i in invoices))
    return {"columns": ["Head", "Amount"], "rows": rows,
            "totals": {"Taxable value": taxable,
                       "Tax": _money(sum(r[1] for r in rows))},
            "note": "Tax on bills raised. Credit notes reverse tax separately — "
                    "see the returns report."}


def invoice_list(start, end):
    out = []
    for i in _inv_in(start, end).order_by(Invoice.invoice_date.desc()).all():
        who = i.staff.full_name if i.staff else i.cashier.full_name
        out.append([i.invoice_number, i.invoice_date.strftime("%d %b %Y %H:%M"),
                    i.customer.name if i.customer else "Walk-in",
                    who, (i.payment_method or "").capitalize(), _money(i.total)])
    return {"columns": ["Bill", "When", "Customer", "Served by", "Payment", "Total"],
            "rows": out, "totals": {"Total": _money(sum(r[5] for r in out))},
            "note": "Every bill in the period, newest first."}


def returns_report(start, end):
    out = []
    for n in CreditNote.query.filter(func.date(CreditNote.created_at) >= start,
                                     func.date(CreditNote.created_at) <= end
                                     ).order_by(CreditNote.id.desc()).all():
        out.append([n.number, n.invoice.invoice_number,
                    n.created_at.strftime("%d %b %Y"),
                    n.invoice.customer.name if n.invoice.customer else "Walk-in",
                    (n.refund_method or "").replace("_", " ").capitalize(),
                    n.reason or "—", _money(n.total)])
    return {"columns": ["Credit note", "Against", "When", "Customer", "Refund", "Reason", "Amount"],
            "rows": out, "totals": {"Refunded": _money(sum(r[6] for r in out))},
            "note": "Goods that came back. Stock was restored unless the line was "
                    "marked damaged."}


def alterations_report(start, end):
    out = []
    for a in Alteration.query.filter(func.date(Alteration.created_at) >= start,
                                     func.date(Alteration.created_at) <= end
                                     ).order_by(Alteration.id.desc()).all():
        out.append([a.number, a.invoice.invoice_number,
                    a.created_at.strftime("%d %b %Y"),
                    a.tailor.name if a.tailor else "—",
                    a.promised_date.strftime("%d %b") if a.promised_date else "—",
                    "OVERDUE" if a.is_overdue else a.status, a.total_qty, _money(a.charge)])
    return {"columns": ["Job", "Against", "Taken in", "Tailor", "Promised", "Status", "Pieces", "Charge"],
            "rows": out, "totals": {"Charges": _money(sum(r[7] for r in out))},
            "note": "Alterations move no stock — the garment already belongs to "
                    "the customer."}


def low_stock(start, end):
    items = Product.query.filter(Product.stock_qty <= Product.reorder_level,
                                 Product.active.is_(True)).order_by(Product.stock_qty).all()
    out = [[p.sku, p.name, p.stock_qty, p.reorder_level, _money(p.selling_price)] for p in items]
    return {"columns": ["SKU", "Item", "In stock", "Reorder at", "Price"], "rows": out,
            "totals": {"Items": len(out)},
            "note": "Current position — not affected by the date range."}


def stock_on_hand(start, end):
    items = Product.query.filter(Product.active.is_(True)).order_by(Product.name).all()
    out = [[p.sku, p.name, p.category.name if p.category else "—", p.stock_qty,
            _money(p.cost_price), _money((p.stock_qty or 0) * (p.cost_price or 0))]
           for p in items]
    return {"columns": ["SKU", "Item", "Category", "Qty", "Cost", "Value"], "rows": out,
            "totals": {"Value": _money(sum(r[5] for r in out))},
            "note": "Shop stock only, valued at cost. The warehouse holds its own "
                    "separately. Not affected by the date range."}


def never_sold(start, end):
    sold = db.session.query(InvoiceItem.product_id).distinct()
    items = Product.query.filter(Product.active.is_(True),
                                 ~Product.id.in_(sold)).order_by(Product.name).all()
    out = [[p.sku, p.name, p.stock_qty, _money(p.selling_price)] for p in items]
    return {"columns": ["SKU", "Item", "In stock", "Price"], "rows": out,
            "totals": {"Items": len(out)},
            "note": "Never sold at all, ever — not just in the period."}


def loyalty_report(start, end):
    rows = db.session.query(
        Customer.name, func.sum(db.case((LoyaltyTxn.points > 0, LoyaltyTxn.points), else_=0)),
        func.sum(db.case((LoyaltyTxn.points < 0, -LoyaltyTxn.points), else_=0))
    ).join(LoyaltyTxn, LoyaltyTxn.customer_id == Customer.id
           ).filter(func.date(LoyaltyTxn.created_at) >= start,
                    func.date(LoyaltyTxn.created_at) <= end
                    ).group_by(Customer.id).all()
    out = [[n, round(float(e or 0), 2), round(float(r or 0), 2)] for n, e, r in rows]
    held = _money(db.session.query(func.coalesce(func.sum(Customer.loyalty_points), 0)).scalar())
    return {"columns": ["Customer", "Earned", "Redeemed/reversed"], "rows": out,
            "totals": {"Points outstanding (all time)": held},
            "note": "Points reversed by a return appear under redeemed."}


def commission_report(start, end):
    r = sales_by_staff(start, end)
    return {"columns": r["columns"], "rows": r["rows"], "totals": r["totals"],
            "note": r["note"]}


REPORTS = {
    "sales_summary": {
        "label": "Sales summary", "run": sales_summary, "dated": True,
        "blurb": "Day-by-day bills, sales and tax",
        "keywords": ["sales", "summary", "total", "revenue", "turnover", "how much",
                     "daily", "விற்பனை", "மொத்தம்", "வருமானம்"]},
    "sales_by_product": {
        "label": "Sales by item", "run": sales_by_product, "dated": True,
        "blurb": "What sold, how many and for how much",
        # Topic words only. "which item" was here once and beat "running low" on
        # a count of matches, sending a low-stock question to sales — a question's
        # phrasing is not evidence of its subject.
        "keywords": ["product", "item", "best seller", "top selling", "best selling",
                     "பொருள்", "விற்ற"]},
    "sales_by_category": {
        "label": "Sales by category", "run": sales_by_category, "dated": True,
        "blurb": "Revenue per category code",
        "keywords": ["category", "categories", "department", "வகை"]},
    "sales_by_staff": {
        "label": "Sales by staff", "run": sales_by_staff, "dated": True,
        "blurb": "Who sold what, net of returns, with commission",
        "keywords": ["staff", "salesperson", "who sold", "employee", "cashier",
                     "ஊழியர்", "விற்பனையாளர்"]},
    "commission": {
        "label": "Staff commission", "run": commission_report, "dated": True,
        "blurb": "Commission earned per staff member",
        "keywords": ["commission", "incentive", "கமிஷன்"]},
    "sales_by_customer": {
        "label": "Sales by customer", "run": sales_by_customer, "dated": True,
        "blurb": "Who bought, how often and how much",
        "keywords": ["customer", "buyer", "client", "வாடிக்கையாளர்"]},
    "sales_by_payment": {
        "label": "Payment breakdown", "run": sales_by_payment, "dated": True,
        "blurb": "Cash, card and UPI split",
        "keywords": ["payment", "cash", "card", "upi", "paid", "பணம்"]},
    "gst_summary": {
        "label": "GST summary", "run": gst_summary, "dated": True,
        "blurb": "CGST, SGST and IGST on bills raised",
        "keywords": ["gst", "tax", "cgst", "sgst", "igst", "வரி"]},
    "invoice_list": {
        "label": "Bill register", "run": invoice_list, "dated": True,
        "blurb": "Every bill raised, with who served it",
        "keywords": ["invoice", "bill", "register", "list of bills", "ரசீது", "பில்"]},
    "returns": {
        "label": "Returns", "run": returns_report, "dated": True,
        "blurb": "Credit notes raised and money refunded",
        "keywords": ["return", "refund", "credit note", "returned", "திரும்ப", "ரிட்டர்ன்"]},
    "alterations": {
        "label": "Alterations", "run": alterations_report, "dated": True,
        "blurb": "Tailoring jobs, tailors and what is overdue",
        "keywords": ["alteration", "tailor", "stitching", "overdue", "தையல்", "ஆல்டரேஷன்"]},
    "low_stock": {
        "label": "Low stock", "run": low_stock, "dated": False,
        "blurb": "At or below reorder level right now",
        "keywords": ["low stock", "running low", "reorder", "running out",
                     "out of stock", "restock", "short", "இருப்பு குறைவு",
                     "குறைவான"]},
    "stock_on_hand": {
        "label": "Stock on hand", "run": stock_on_hand, "dated": False,
        "blurb": "Everything the shop holds, valued at cost",
        "keywords": ["stock", "inventory", "on hand", "holding", "stock value",
                     "இருப்பு", "சரக்கு"]},
    "never_sold": {
        "label": "Never sold", "run": never_sold, "dated": False,
        "blurb": "Items that have never sold at all",
        "keywords": ["never sold", "dead stock", "not selling", "slow moving"]},
    "loyalty": {
        "label": "Loyalty points", "run": loyalty_report, "dated": True,
        "blurb": "Points earned and redeemed per customer",
        "keywords": ["loyalty", "points", "reward", "புள்ளி"]},
}


def run(key, start, end):
    """Run one report. Undated ones ignore the range but still receive it."""
    spec = REPORTS[key]
    out = spec["run"](start, end)
    out["key"] = key
    out["label"] = spec["label"]
    out["dated"] = spec["dated"]
    return out


def catalogue():
    return [{"key": k, "label": v["label"], "blurb": v["blurb"], "dated": v["dated"]}
            for k, v in REPORTS.items()]
