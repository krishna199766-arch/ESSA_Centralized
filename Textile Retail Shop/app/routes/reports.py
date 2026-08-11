from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from datetime import datetime, date, timedelta
from sqlalchemy import func
from app import db
from app.models import Invoice, InvoiceItem, Product, Customer
from app.utils import role_required
from app import nlq, reports_lib

reports_bp = Blueprint("reports", __name__)


def parse_range():
    end = date.today()
    start = end - timedelta(days=30)
    if request.args.get("from"):
        try:
            start = datetime.strptime(request.args["from"], "%Y-%m-%d").date()
        except ValueError:
            pass
    if request.args.get("to"):
        try:
            end = datetime.strptime(request.args["to"], "%Y-%m-%d").date()
        except ValueError:
            pass
    return start, end


@reports_bp.route("/")
@login_required
@role_required("admin", "manager")
def index():
    start, end = parse_range()
    q = db.session.query(Invoice).filter(
        func.date(Invoice.invoice_date) >= start,
        func.date(Invoice.invoice_date) <= end,
    )
    invoices = q.all()

    total_sales = sum(i.total for i in invoices)
    total_tax = sum(i.cgst + i.sgst + i.igst for i in invoices)
    total_discount = sum(i.discount for i in invoices)
    invoice_count = len(invoices)
    avg_bill = total_sales / invoice_count if invoice_count else 0

    # Daily trend
    trend_map = {}
    d = start
    while d <= end:
        trend_map[d.isoformat()] = 0.0
        d += timedelta(days=1)
    for inv in invoices:
        key = inv.invoice_date.date().isoformat()
        if key in trend_map:
            trend_map[key] += inv.total
    trend_labels = list(trend_map.keys())
    trend_values = [round(v, 2) for v in trend_map.values()]

    # Top products
    top = db.session.query(
        Product.name,
        func.sum(InvoiceItem.quantity).label("qty"),
        func.sum(InvoiceItem.line_total + InvoiceItem.tax_amount).label("revenue")
    ).join(InvoiceItem, InvoiceItem.product_id == Product.id
    ).join(Invoice, Invoice.id == InvoiceItem.invoice_id
    ).filter(
        func.date(Invoice.invoice_date) >= start,
        func.date(Invoice.invoice_date) <= end,
    ).group_by(Product.id).order_by(func.sum(InvoiceItem.line_total + InvoiceItem.tax_amount).desc()).limit(10).all()

    # Payment breakdown
    pay_rows = db.session.query(
        Invoice.payment_method,
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.total), 0)
    ).filter(
        func.date(Invoice.invoice_date) >= start,
        func.date(Invoice.invoice_date) <= end,
    ).group_by(Invoice.payment_method).all()

    # GST summary
    gst_summary = {
        "cgst": sum(i.cgst for i in invoices),
        "sgst": sum(i.sgst for i in invoices),
        "igst": sum(i.igst for i in invoices),
    }

    # Top customers
    top_customers = db.session.query(
        Customer.name, func.count(Invoice.id), func.sum(Invoice.total)
    ).join(Invoice, Invoice.customer_id == Customer.id
    ).filter(
        func.date(Invoice.invoice_date) >= start,
        func.date(Invoice.invoice_date) <= end,
    ).group_by(Customer.id).order_by(func.sum(Invoice.total).desc()).limit(10).all()

    return render_template(
        "reports/index.html",
        start=start, end=end,
        total_sales=total_sales, total_tax=total_tax, total_discount=total_discount,
        invoice_count=invoice_count, avg_bill=avg_bill,
        trend_labels=trend_labels, trend_values=trend_values,
        top_products=top, pay_rows=pay_rows,
        gst_summary=gst_summary, top_customers=top_customers,
    )


@reports_bp.route("/low-stock")
@login_required
@role_required("admin", "manager")
def low_stock():
    items = Product.query.filter(
        Product.stock_qty <= Product.reorder_level, Product.active == True
    ).order_by(Product.stock_qty).all()
    return render_template("reports/low_stock.html", items=items)


# ---------- Ask a question ----------
#
# The question is routed to one of the reports in reports_lib rather than turned
# into a query — see app/nlq.py for why. The answer comes back in the same
# {columns, rows, totals, note} shape every report uses, so one renderer draws it.
@reports_bp.route("/ask", methods=["POST"])
@login_required
@role_required("admin", "manager")
def ask():
    data = request.get_json(silent=True) or {}
    return jsonify(nlq.ask(data.get("q", "")))


@reports_bp.route("/catalogue")
@login_required
@role_required("admin", "manager")
def catalogue():
    """What can be asked about at all — used to show suggestions."""
    return jsonify({"engine": "model" if nlq.available() else "keywords",
                    "reports": reports_lib.catalogue(),
                    "examples": nlq.EXAMPLES})
