from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, current_app, session)
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app import places, warehouse_items
from app.models import (Product, Customer, Invoice, InvoiceItem, StockMovement,
                        LoyaltyTxn, User)
from app.utils import generate_number

pos_bp = Blueprint("pos", __name__)

#: Where the till's Company / Location / Counter choice is kept.
#:
#: In the SESSION, deliberately. It is a property of the machine somebody is
#: standing at, not of the person signed into it and not of the shop: two tills
#: at one branch are two counters, and the same cashier moving between them must
#: not carry the first one's drawer to the second. It also has to outlast a
#: reload — a picker that forgets on every refresh gets set wrong, or ignored.
POST_KEYS = ("company_id", "location_id", "counter_id")


def _chosen():
    """(company, location, counter) for this till — each None until picked."""
    return places.resolve(*(session.get(k) for k in POST_KEYS))


@pos_bp.route("/")
@login_required
def counter():
    products = Product.query.filter(Product.active == True, Product.stock_qty > 0).order_by(Product.name).all()
    customers = Customer.query.order_by(Customer.name).all()
    staff = User.query.filter(User.active.is_(True)).order_by(User.full_name).all()
    company, location, till = _chosen()
    return render_template("pos/counter.html", products=products,
                           customers=customers, staff=staff,
                           places=places.picker_options(),
                           chosen_company=company, chosen_location=location,
                           chosen_counter=till,
                           default_company=places.default_company())


@pos_bp.route("/place", methods=["GET", "POST"])
@login_required
def place():
    """Read or set which company, location and counter this till is billing as.

    POST takes the three ids and answers with what it actually settled on, which
    is not always what was sent: a counter belonging to another branch is dropped
    rather than stored. The till redraws from the answer, so what it shows is
    what the next bill will carry — never what was merely asked for.
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        for key in POST_KEYS:
            raw = data.get(key)
            session[key] = int(raw) if str(raw or "").strip().isdigit() else None
        company, location, till = places.resolve(*(session.get(k) for k in POST_KEYS))
        # store back what survived, so the session never holds a pairing the
        # screen has already been told is impossible
        session["company_id"] = company.id if company else None
        session["location_id"] = location.id if location else None
        session["counter_id"] = till.id if till else None
    else:
        company, location, till = _chosen()
    return jsonify({
        "company": {"id": company.id, "name": company.name,
                    "gstin": company.gstin or ""} if company else None,
        "location": {"id": location.id, "name": location.name} if location else None,
        "counter": {"id": till.id, "name": till.name} if till else None,
        "options": places.picker_options(),
    })


def resolve_staff(value):
    """The staff member a counter identified, or None.

    Takes what the ID card carries (`STF000003`), a bare staff number, or the id
    the picker sends. Only active staff resolve — someone who has left should not
    be collecting commission on today's sales.
    """
    text = str(value or "").strip()
    if not text:
        return None
    q = User.query.filter(User.active.is_(True))
    digits = text.upper()[3:] if text.upper().startswith("STF") else text
    if digits.isdigit():
        u = q.filter(User.id == int(digits)).first()
        if u:
            return u
    # A username typed in full is the other thing people reach for.
    return q.filter(User.username.ilike(text)).first()


@pos_bp.route("/api/staff")
@login_required
def api_staff():
    """Resolve a scanned ID card or a typed staff code to the person."""
    u = resolve_staff(request.args.get("code", ""))
    if not u:
        return jsonify({"error": "No active staff member for that code"}), 404
    return jsonify({"id": u.id, "code": u.staff_code,
                    "name": u.full_name, "role": u.role})


@pos_bp.route("/api/product")
@pos_bp.route("/api/product/<path:code>")
@login_required
def api_product(code=None):
    """Resolve anything scannable at the counter to a cart line.

    Takes a SKU, a printed barcode, a warehouse QR (`E1|…` / `EU1|…`) or a bare
    per-piece code — resolve_scan sorts out which, and pulls the item in from the
    warehouse if the shop hasn't got it yet. So any tag that exists scans here,
    including items not on the grid because they're out of stock.

    `?code=` is the form the counter uses: a QR payload can carry '/', which a
    path segment mangles. The path route stays for anything still calling it.
    """
    code = (code if code is not None else request.args.get("code", "")).strip()
    p = warehouse_items.resolve_scan(code)
    if not p:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": p.id, "sku": p.sku, "barcode": p.barcode, "name": p.name,
        "price": p.selling_price, "gst": p.gst_rate,
        "stock": p.stock_qty, "unit": p.unit, "hsn": p.hsn_code,
    })


@pos_bp.route("/checkout", methods=["POST"])
@login_required
def checkout():
    data = request.get_json() or request.form
    try:
        items_json = data.get("items")
        if isinstance(items_json, str):
            import json
            items = json.loads(items_json)
        else:
            items = items_json or []
        if not items:
            return jsonify({"error": "Cart is empty"}), 400

        customer_id = data.get("customer_id") or None
        customer_id = int(customer_id) if customer_id else None
        customer = Customer.query.get(customer_id) if customer_id else None

        payment_method = data.get("payment_method", "cash")
        discount = float(data.get("discount") or 0)
        redeem_points = float(data.get("redeem_points") or 0)

        # Interstate = customer state code != shop state code
        shop_state = current_app.config["SHOP_STATE_CODE"]
        is_interstate = False
        if customer and customer.state_code and customer.state_code != shop_state:
            is_interstate = True

        # Who served this sale. Identified at the counter before it starts, and
        # refused if missing: the commission is worked out from this, so a sale
        # with nobody against it is a sale nobody gets paid for.
        staff = resolve_staff(data.get("staff_code") or data.get("staff_id"))
        if staff is None:
            return jsonify({"error": "Identify the staff member before billing "
                                     "— scan the ID card or enter the staff code."}), 400

        # Where this sale happened, and under whose registration. The company
        # falls back to the default rather than being left blank: a tax invoice
        # with no entity on it is not a tax invoice, and a till nobody has
        # configured is the normal state of a shop that only has one company.
        company, location, till = _chosen()
        if company is None:
            company = places.default_company()

        inv = Invoice(
            invoice_number=generate_number("INV", Invoice, "invoice_number"),
            customer_id=customer_id,
            cashier_id=current_user.id,
            staff_id=staff.id,
            payment_method=payment_method,
            discount=discount,
            is_interstate=is_interstate,
            company_id=company.id if company else None,
            location_id=location.id if location else None,
            counter_id=till.id if till else None,
        )
        db.session.add(inv)
        db.session.flush()

        subtotal = 0.0
        total_tax = 0.0

        for it in items:
            pid = int(it["product_id"])
            qty = float(it["quantity"])
            product = Product.query.get(pid)
            if not product or product.stock_qty < qty:
                db.session.rollback()
                return jsonify({"error": f"Insufficient stock for {product.name if product else 'product'}"}), 400
            unit_price = float(it.get("unit_price", product.selling_price))
            line_total = qty * unit_price  # taxable
            tax = round(line_total * product.gst_rate / 100.0, 2)

            db.session.add(InvoiceItem(
                invoice_id=inv.id, product_id=pid,
                quantity=qty, unit_price=unit_price,
                gst_rate=product.gst_rate,
                line_total=line_total, tax_amount=tax,
            ))
            product.stock_qty -= qty
            db.session.add(StockMovement(
                product_id=pid, change=-qty, reason="sale",
                reference=inv.invoice_number
            ))
            subtotal += line_total
            total_tax += tax

        # apply discount to subtotal proportionally to keep tax reasonable
        if discount > subtotal:
            discount = subtotal

        if is_interstate:
            inv.igst = round(total_tax, 2)
        else:
            inv.cgst = round(total_tax / 2, 2)
            inv.sgst = round(total_tax / 2, 2)

        inv.subtotal = round(subtotal, 2)
        pre_loyalty_total = round(subtotal - discount + total_tax, 2)

        # Loyalty redemption
        loyalty_redeemed_value = 0.0
        if customer and redeem_points > 0:
            available = customer.loyalty_points
            use = min(redeem_points, available, pre_loyalty_total)
            loyalty_redeemed_value = use * current_app.config["LOYALTY_POINT_VALUE"]
            inv.loyalty_redeemed = use
            customer.loyalty_points -= use
            db.session.add(LoyaltyTxn(
                customer_id=customer.id, points=-use,
                reason="redeem", invoice_id=inv.id
            ))

        inv.total = round(pre_loyalty_total - loyalty_redeemed_value, 2)

        # Loyalty earning
        if customer and inv.total >= current_app.config["LOYALTY_MIN_BILL"]:
            earned = round(inv.total * current_app.config["LOYALTY_EARN_RATE"], 2)
            inv.loyalty_earned = earned
            customer.loyalty_points = (customer.loyalty_points or 0) + earned
            customer.total_spent = (customer.total_spent or 0) + inv.total
            db.session.add(LoyaltyTxn(
                customer_id=customer.id, points=earned,
                reason="earn", invoice_id=inv.id
            ))
        elif customer:
            customer.total_spent = (customer.total_spent or 0) + inv.total

        db.session.commit()
        return jsonify({"success": True, "invoice_id": inv.id, "invoice_number": inv.invoice_number})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@pos_bp.route("/invoice/<int:iid>")
@login_required
def view_invoice(iid):
    inv = Invoice.query.get_or_404(iid)
    return render_template("pos/invoice.html", inv=inv, print_view=False)


@pos_bp.route("/invoice/<int:iid>/print")
@login_required
def print_invoice(iid):
    inv = Invoice.query.get_or_404(iid)
    return render_template("pos/invoice.html", inv=inv, print_view=True)


@pos_bp.route("/invoices")
@login_required
def invoice_list():
    from datetime import datetime, timedelta, date
    from sqlalchemy import func, or_

    q         = (request.args.get("q") or "").strip()
    date_from = request.args.get("from") or ""
    date_to   = request.args.get("to") or ""
    cashier   = request.args.get("cashier", type=int)
    payment   = request.args.get("payment") or ""
    min_amt   = request.args.get("min", type=float)
    max_amt   = request.args.get("max", type=float)

    query = Invoice.query.outerjoin(Customer, Invoice.customer_id == Customer.id)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Invoice.invoice_number.ilike(like),
            Customer.name.ilike(like),
            Customer.phone.ilike(like),
        ))

    def parse(d):
        try: return datetime.strptime(d, "%Y-%m-%d").date()
        except: return None
    df = parse(date_from); dt = parse(date_to)
    if df: query = query.filter(func.date(Invoice.invoice_date) >= df)
    if dt: query = query.filter(func.date(Invoice.invoice_date) <= dt)
    if cashier: query = query.filter(Invoice.cashier_id == cashier)
    if payment: query = query.filter(Invoice.payment_method == payment)
    if min_amt is not None: query = query.filter(Invoice.total >= min_amt)
    if max_amt is not None: query = query.filter(Invoice.total <= max_amt)

    invoices = query.order_by(Invoice.invoice_date.desc()).limit(500).all()
    summary = {
        "count": len(invoices),
        "total": sum(i.total for i in invoices),
        "tax":   sum(i.cgst + i.sgst + i.igst for i in invoices),
    }
    cashiers = User.query.filter_by(active=True).order_by(User.full_name).all()
    return render_template(
        "pos/invoices.html",
        invoices=invoices, summary=summary, cashiers=cashiers,
        q=q, date_from=date_from, date_to=date_to,
        cashier_id=cashier, payment=payment,
        min_amt=min_amt if min_amt is not None else "",
        max_amt=max_amt if max_amt is not None else "",
    )
