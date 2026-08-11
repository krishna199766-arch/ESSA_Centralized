from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from app import db
from app.models import Customer, Invoice, LoyaltyTxn

customers_bp = Blueprint("customers", __name__)


@customers_bp.route("/")
@login_required
def list_customers():
    q = request.args.get("q", "").strip()
    query = Customer.query
    if q:
        query = query.filter(
            (Customer.name.ilike(f"%{q}%")) | (Customer.phone.ilike(f"%{q}%")) | (Customer.email.ilike(f"%{q}%"))
        )
    customers = query.order_by(Customer.name).all()
    return render_template("customers/list.html", customers=customers, q=q)


@customers_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_customer():
    if request.method == "POST":
        c = Customer(
            name=request.form["name"].strip(),
            phone=request.form.get("phone", "").strip(),
            email=request.form.get("email", "").strip(),
            address=request.form.get("address", ""),
            gstin=request.form.get("gstin", "").strip(),
            state_code=request.form.get("state_code", "33").strip(),
        )
        db.session.add(c)
        db.session.commit()
        flash("Customer added.", "success")
        return redirect(url_for("customers.list_customers"))
    return render_template("customers/form.html", customer=None)


@customers_bp.route("/<int:cid>")
@login_required
def view_customer(cid):
    c = Customer.query.get_or_404(cid)
    invoices = Invoice.query.filter_by(customer_id=cid).order_by(Invoice.invoice_date.desc()).all()
    txns = LoyaltyTxn.query.filter_by(customer_id=cid).order_by(LoyaltyTxn.created_at.desc()).all()
    return render_template("customers/detail.html", customer=c, invoices=invoices, txns=txns)


@customers_bp.route("/<int:cid>/card")
@login_required
def customer_card(cid):
    """Printable membership card with a scannable barcode for the POS."""
    c = Customer.query.get_or_404(cid)
    return render_template("customers/card.html", customer=c)


@customers_bp.route("/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(cid):
    c = Customer.query.get_or_404(cid)
    if request.method == "POST":
        c.name = request.form["name"].strip()
        c.phone = request.form.get("phone", "").strip()
        c.email = request.form.get("email", "").strip()
        c.address = request.form.get("address", "")
        c.gstin = request.form.get("gstin", "").strip()
        c.state_code = request.form.get("state_code", "33").strip()
        db.session.commit()
        flash("Customer updated.", "success")
        return redirect(url_for("customers.view_customer", cid=cid))
    return render_template("customers/form.html", customer=c)
