"""Customer returns — goods coming back against the bill they went out on.

A return is not a sale in reverse; it is its own document (a credit note) that
undoes part of one invoice. Undoing it properly means touching everything the
sale touched:

  stock     the goods come back. Resellable ones go on the shelf; damaged ones
            come back and are immediately written off, so the movement log shows
            what really happened rather than a shelf count that quietly lies.
  GST       the tax on the returned lines is reversed, split the same way the
            invoice split it (IGST interstate, CGST/SGST otherwise).
  discount  a bill-level discount is refunded pro rata, or a customer returning
            one item of five would get back more than they paid for it.
  loyalty   points earned on the refunded amount are taken back, and what the
            customer actually spent comes down.
  commission the staff member on the ORIGINAL sale loses the credit — not
            whoever happens to be handling the return. See routes/staff.py.

Nothing can come back twice: every line is capped at what it was sold for, less
what has already been credited.
"""
from datetime import date, datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, current_app)
from flask_login import login_required, current_user

from app import db
from app.models import (CreditNote, CreditNoteItem, Invoice, InvoiceItem,
                        LoyaltyTxn, Product, StockMovement)
from app.routes.pos import resolve_staff
from app.utils import generate_number, role_required

returns_bp = Blueprint("returns", __name__)


@returns_bp.route("/")
@login_required
def index():
    """Find the bill the goods went out on."""
    q = (request.args.get("q") or "").strip()
    invoice = None
    if q:
        invoice = Invoice.query.filter_by(invoice_number=q).first()
        if not invoice and q.isdigit():
            invoice = Invoice.query.get(int(q))
        if not invoice:
            flash(f"No invoice matches '{q}'.", "warning")
    recent = CreditNote.query.order_by(CreditNote.id.desc()).limit(10).all()
    return render_template("returns/index.html", q=q, invoice=invoice, recent=recent)


@returns_bp.route("/create", methods=["POST"])
@login_required
def create():
    """Take back some of an invoice and hand the money over."""
    invoice_id = request.form.get("invoice_id", type=int)
    inv = Invoice.query.get_or_404(invoice_id)

    staff = resolve_staff(request.form.get("staff_code"))
    if staff is None:
        flash("Identify the staff member handling this return first.", "danger")
        return redirect(url_for("returns.index", q=inv.invoice_number))

    # What is actually coming back, line by line.
    taking = []
    for item in inv.items:
        qty = request.form.get(f"qty_{item.id}", type=float) or 0.0
        if qty <= 0:
            continue
        if qty > item.returnable_qty:
            flash(f"{item.product.name}: only {item.returnable_qty} left to return.", "danger")
            return redirect(url_for("returns.index", q=inv.invoice_number))
        condition = request.form.get(f"cond_{item.id}") or "resellable"
        taking.append((item, qty, condition))

    if not taking:
        flash("Nothing was marked for return.", "warning")
        return redirect(url_for("returns.index", q=inv.invoice_number))

    note = CreditNote(
        number=generate_number("CRN", CreditNote, "number"),
        invoice_id=inv.id,
        staff_id=staff.id,
        cashier_id=current_user.id,
        refund_method=request.form.get("refund_method") or "cash",
        reason=(request.form.get("reason") or "").strip()[:256],
    )
    db.session.add(note)
    db.session.flush()

    goods = 0.0
    tax = 0.0
    for item, qty, condition in taking:
        line_total = round(qty * item.unit_price, 2)
        line_tax = round(line_total * (item.gst_rate or 0) / 100.0, 2)

        db.session.add(CreditNoteItem(
            credit_note_id=note.id, invoice_item_id=item.id,
            product_id=item.product_id, quantity=qty,
            unit_price=item.unit_price, gst_rate=item.gst_rate,
            line_total=line_total, tax_amount=line_tax, condition=condition,
        ))

        # The goods are back in the building either way; the log says so, and a
        # damaged one is then written off in its own right.
        product = Product.query.get(item.product_id)
        product.stock_qty += qty
        db.session.add(StockMovement(
            product_id=item.product_id, change=qty, reason="return",
            reference=note.number))
        if condition == "damaged":
            product.stock_qty -= qty
            db.session.add(StockMovement(
                product_id=item.product_id, change=-qty, reason="damaged",
                reference=note.number))

        goods += line_total
        tax += line_tax

    # A discount was given against the whole bill, so it comes back in the same
    # proportion as the goods being returned.
    share = (goods / inv.subtotal) if inv.subtotal else 0.0
    discount_back = round((inv.discount or 0) * share, 2)

    note.subtotal = round(goods, 2)
    note.discount = discount_back
    if inv.is_interstate:
        note.igst = round(tax, 2)
    else:
        note.cgst = round(tax / 2, 2)
        note.sgst = round(tax / 2, 2)
    note.total = round(goods - discount_back + tax, 2)

    # Loyalty: take back what this refund earned, and correct what was spent.
    customer = inv.customer
    if customer:
        earned = inv.loyalty_earned or 0
        reverse = round(earned * share, 2)
        # Never push a balance negative — points already spent are gone, and a
        # customer should not owe the shop loyalty.
        reverse = min(reverse, customer.loyalty_points or 0)
        if reverse > 0:
            customer.loyalty_points = round((customer.loyalty_points or 0) - reverse, 2)
            note.loyalty_reversed = reverse
            db.session.add(LoyaltyTxn(
                customer_id=customer.id, points=-reverse,
                reason=f"return {note.number}", invoice_id=inv.id))
        customer.total_spent = round(max(0.0, (customer.total_spent or 0) - note.total), 2)

    db.session.commit()
    flash(f"Return recorded — {note.number}, {note.total:.2f} refunded.", "success")
    return redirect(url_for("returns.view_note", nid=note.id))


@returns_bp.route("/<int:nid>")
@login_required
def view_note(nid):
    note = CreditNote.query.get_or_404(nid)
    return render_template("returns/note.html", note=note)


@returns_bp.route("/<int:nid>/print")
@login_required
def print_note(nid):
    note = CreditNote.query.get_or_404(nid)
    return render_template("returns/note.html", note=note, print_mode=True)


@returns_bp.route("/list")
@login_required
@role_required("admin", "manager")
def list_notes():
    notes = CreditNote.query.order_by(CreditNote.id.desc()).limit(200).all()
    total = sum(n.total for n in notes)
    return render_template("returns/list.html", notes=notes, total=total)
