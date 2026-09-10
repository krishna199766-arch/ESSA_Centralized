from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   jsonify, current_app, session)
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app import billing_numbers, places, promotions, transfers, warehouse_items
from app.models import (Product, Customer, Invoice, InvoiceItem, InvoicePayment,
                        StockMovement, LoyaltyTxn, User, PAYMENT_METHODS)

pos_bp = Blueprint("pos", __name__)

#: Money is compared to the paisa. Floats do not land exactly on a total built
#: from percentages, so "did this balance" needs a tolerance rather than ==.
SETTLE_TOLERANCE = 0.01


def parse_payments(data):
    """The tenders offered for this bill, cleaned. Raises ValueError on nonsense.

    Accepts the new `payments` list — [{method, amount, tendered, reference}] —
    and falls back to the single `payment_method` this route has always taken, so
    anything still posting the old shape keeps working. The fallback carries no
    amount: it means "settle the whole bill this way", and the amount is filled
    in below once the total is known.
    """
    # Whether a settlement was SENT, not whether it has anything in it. An empty
    # list is a settlement with no money in it and has to be refused; a missing
    # key is the old single-method shape and falls back. Reading both as "no
    # payments" let a till bill an empty settlement as cash for the full amount.
    if "payments" not in data:
        method = (data.get("payment_method") or "cash").strip().lower()
        if method not in PAYMENT_METHODS:
            raise ValueError(f"“{method}” is not a payment method")
        return None, method              # None = settle the whole total this way

    raw = data.get("payments")
    if isinstance(raw, str):
        import json
        raw = json.loads(raw or "[]")
    if not raw:
        raise ValueError("no payment was entered")

    out = []
    for row in raw:
        method = (row.get("method") or "").strip().lower()
        if method not in PAYMENT_METHODS:
            raise ValueError(f"“{method}” is not a payment method")
        try:
            amount = round(float(row.get("amount") or 0), 2)
            tendered = row.get("tendered")
            tendered = round(float(tendered), 2) if tendered not in (None, "") else amount
        except (TypeError, ValueError):
            raise ValueError("payment amounts must be numbers")
        if amount <= 0:
            continue                     # a blank row is not a payment
        if tendered < amount - SETTLE_TOLERANCE:
            raise ValueError(f"{method}: {tendered:g} tendered against {amount:g} — "
                             f"less was handed over than is being settled")
        if method != "cash" and tendered > amount + SETTLE_TOLERANCE:
            # Only a drawer gives change. A card or a UPI transfer is for an
            # exact amount, and recording an over-tender on one would invent
            # change that nobody handed back.
            raise ValueError(f"{method} cannot be over-tendered — "
                             f"it is settled for an exact amount")
        out.append({"method": method, "amount": amount, "tendered": tendered,
                    "reference": (row.get("reference") or "").strip() or None})
    if not out:
        raise ValueError("no payment was entered")
    return out, ("mixed" if len({p["method"] for p in out}) > 1
                 else out[0]["method"])

#: Where the till's Company / Location / Counter choice is kept.
#:
#: In the SESSION, deliberately. It is a property of the machine somebody is
#: standing at, not of the person signed into it and not of the shop: two tills
#: at one branch are two counters, and the same cashier moving between them must
#: not carry the first one's drawer to the second. It also has to outlast a
#: reload — a picker that forgets on every refresh gets set wrong, or ignored.
POST_KEYS = ("company_id", "location_id", "floor_id", "counter_id")


def _chosen():
    """(company, location, floor, counter) — each None until picked or mapped."""
    return places.resolve(*(session.get(k) for k in POST_KEYS))


def _place():
    """Where this till is, in the shape the promotion engine reads.

    Includes the warehouse the frame was opened from, so an offer configured for
    one warehouse's branches does not run at another's — the same narrowing
    places.picker_options already does to the branch list.
    """
    company, location, _floor, counter = _chosen()
    return promotions.Place(warehouse_id=places.current_scope(), company=company,
                            location=location, counter=counter)


def _cart_lines(data):
    """The paid lines a request is offering, as the engine wants them.

    Reward lines are dropped if a caller sends any: what the customer gets free
    is decided here, from the products they are paying for, and a page that
    could add its own free lines could bill the shop's stock away.
    """
    items = data.get("items")
    if isinstance(items, str):
        import json
        items = json.loads(items or "[]")
    out = []
    for it in items or []:
        if (it.get("promo_role") or it.get("free")):
            continue
        out.append({"product_id": it.get("product_id"),
                    "quantity": it.get("quantity")})
    return out


@pos_bp.route("/api/promotions", methods=["POST"])
@login_required
def api_promotions():
    """What this cart has earned, as the cashier builds it.

    Called on every cart change so a free item appears while the customer is
    still at the counter, and so an offer that cannot be honoured says why
    there and then. Advisory only — `checkout` runs the same evaluation again
    against its own reading of the cart, and that is the one that bills.
    """
    data = request.get_json(silent=True) or request.form
    choices = data.get("choices") or {}
    if isinstance(choices, str):
        import json
        choices = json.loads(choices or "{}")
    try:
        outcome = promotions.evaluate(_cart_lines(data), place=_place(),
                                      choices=choices)
    except Exception as exc:                        # noqa: BLE001
        # A promotion is a bonus; a till that cannot bill because an offer is
        # misconfigured is worse than one that quietly runs no offers. The
        # counter shows nothing and the sale goes through.
        current_app.logger.warning("promotion evaluation failed", exc_info=True)
        return jsonify({"awards": [], "notices": [], "error": str(exc)})
    return jsonify(outcome.to_json())


@pos_bp.route("/")
@login_required
def counter():
    """The billing counter. Scan-driven: no product list.

    The grid used to render every in-stock product as a tile. On a shop with a
    few hundred items that is a wall of near-identical names — five tiles reading
    PILLOW COVER at ₹0.00 tell a cashier nothing about which one is in their hand
    — and picking off it is how the wrong variant gets billed. The tag on the
    garment is unambiguous, so the counter takes the scan and nothing else.

    Every product still resolves: `api_product` looks up a SKU, a barcode or a
    warehouse QR against the whole catalogue, and pulls the item in from the
    warehouse if the shop has not seen it yet. Dropping the grid removed a
    listing, not a capability — and it removed a query that loaded the entire
    catalogue on every page load.
    """
    customers = Customer.query.order_by(Customer.name).all()
    staff = User.query.filter(User.active.is_(True)).order_by(User.full_name).all()
    company, location, storey, till = _chosen()
    return render_template("pos/counter.html",
                           customers=customers, staff=staff,
                           places=places.picker_options(),
                           chosen_company=company, chosen_location=location,
                           chosen_floor=storey, chosen_counter=till,
                           next_bill=billing_numbers.peek(till),
                           default_company=places.default_company())


def _place_json(company, location, storey, till):
    """What the till is billing as, and what its next bill will be called.

    The bill number travels with the place because it is DECIDED by it — a
    cashier moving a till from Ground to First has to see TF appear, there and
    then, or the first they know of a wrong series is a printed bill.
    """
    return {
        "company": {"id": company.id, "name": company.name,
                    "gstin": company.gstin or ""} if company else None,
        "location": {"id": location.id, "name": location.name} if location else None,
        "floor": {"id": storey.id, "name": storey.name,
                  "prefix": storey.prefix} if storey else None,
        "counter": {"id": till.id, "name": till.name} if till else None,
        "next_bill": billing_numbers.peek(till),
        "options": places.picker_options(),
    }


@pos_bp.route("/place", methods=["GET", "POST"])
@login_required
def place():
    """Read or set which company, location, floor and counter this till bills as.

    POST takes the four ids and answers with what it actually settled on, which
    is not always what was sent: a counter belonging to another branch is dropped
    rather than stored, and so is a floor the chosen till is not on. The till
    redraws from the answer, so what it shows is what the next bill will carry —
    never what was merely asked for.
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        for key in POST_KEYS:
            raw = data.get(key)
            session[key] = int(raw) if str(raw or "").strip().isdigit() else None
        company, location, storey, till = places.resolve(
            *(session.get(k) for k in POST_KEYS))
        # store back what survived, so the session never holds a pairing the
        # screen has already been told is impossible
        session["company_id"] = company.id if company else None
        session["location_id"] = location.id if location else None
        session["floor_id"] = storey.id if storey else None
        session["counter_id"] = till.id if till else None
    else:
        company, location, storey, till = _chosen()
    return jsonify(_place_json(company, location, storey, till))


@pos_bp.route("/api/next-bill")
@login_required
def api_next_bill():
    """The number the next bill on this till would carry.

    A look, not a reservation — see billing_numbers.peek. The till shows it so
    the cashier can read the series off the screen before taking money; the
    number that counts is the one on the committed bill, which the checkout
    returns.
    """
    _company, _location, _storey, till = _chosen()
    return jsonify(billing_numbers.peek(till))


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

        # Parsed before anything is written, so a malformed settlement is refused
        # while the cart is still a cart — not after stock has come off the shelf.
        try:
            payments, payment_method = parse_payments(data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
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
        company, location, storey, till = _chosen()
        if company is None:
            company = places.default_company()

        # ---- what this cart has earned ---------------------------------------
        # Worked out BEFORE anything is written, and from the products the
        # customer is paying for rather than from anything the page sent about
        # free items. Two reasons it has to be here and not further down:
        #
        #   * it reads stock, and every line below reduces it. Asked after the
        #     sale had come off the shelf it would see the shop as three garments
        #     poorer and could refuse a reward the customer had plainly earned.
        #   * it is the same reading the counter was shown while the cart was
        #     being built, so the till and the bill agree.
        #
        # A misconfigured offer must never cost the shop a sale, so the whole
        # evaluation is allowed to fail and the bill goes through without it.
        try:
            outcome = promotions.evaluate(_cart_lines(data), place=_place(),
                                          choices=(data.get("promotion_choices") or {}))
        except Exception:                            # noqa: BLE001
            current_app.logger.warning("promotions skipped for this bill",
                                       exc_info=True)
            outcome = promotions.Outcome([], [])

        # ---- the bill number -------------------------------------------------
        # Taken from the till's own series, at the backend, inside this
        # transaction — see app/billing_numbers.py. Never sent by the page and
        # never typed: a bill number is the shop's statutory record of the sale,
        # and a cashier who could choose one could raise two bills with the same
        # number or skip a number nobody can then account for.
        #
        # Allocating HERE, before the lines are written, is what makes two tills
        # on one floor safe: the series' row is locked for the rest of this
        # transaction, so the second till waits and gets the next number rather
        # than the same one. A till on another floor is on another row and never
        # waits at all.
        number, prefix, fin_year, seq = billing_numbers.allocate(till)

        inv = Invoice(
            invoice_number=number,
            bill_prefix=prefix,
            fin_year=fin_year,
            bill_seq=seq,
            customer_id=customer_id,
            cashier_id=current_user.id,
            staff_id=staff.id,
            payment_method=payment_method,
            discount=discount,
            is_interstate=is_interstate,
            company_id=company.id if company else None,
            location_id=location.id if location else None,
            floor_id=storey.id if storey else None,
            counter_id=till.id if till else None,
        )
        db.session.add(inv)
        db.session.flush()

        subtotal = 0.0
        total_tax = 0.0

        # Stock leaves the building in exactly one place, whether it was sold or
        # given away. `where` is the branch it comes off, and `why` is what the
        # ledger will say about it — a free garment under a promotion is not a
        # sale, and a movement that called it one would make the promotion
        # invisible to every stock question afterwards.
        def take_stock(product, qty, why):
            product.stock_qty -= qty
            # …and out of the branch it was rung at. The shop's total above is
            # what the till sells against and what every screen reads; this is
            # the split of it, so a sale at Tirupur comes off Tirupur's shelf and
            # not off the pieces sitting at another branch.
            #
            # Not a second check on whether the sale may happen. A shop whose
            # branches were stocked before any of this existed has a total and no
            # split, and refusing to sell what is plainly on the counter because
            # a table added last week says zero would be the software arguing
            # with the room.
            if location is not None:
                transfers.move(location.id, product.id, -qty)
            db.session.add(StockMovement(
                product_id=product.id, change=-qty, reason=why,
                reference=inv.invoice_number
                          + (f" @ {location.name}" if location is not None else "")
            ))

        # Every paid line, by product, so the promotion can mark which of them
        # earned it. A list per product because a till may bill the same garment
        # on two lines at two prices.
        sold_items = {}

        for it in items:
            if it.get("promo_role") or it.get("free"):
                # Free lines are not accepted from the page. They are worked out
                # below, from the products actually being paid for.
                continue
            pid = int(it["product_id"])
            qty = float(it["quantity"])
            product = Product.query.get(pid)
            if not product or product.stock_qty < qty:
                db.session.rollback()
                return jsonify({"error": f"Insufficient stock for {product.name if product else 'product'}"}), 400
            unit_price = float(it.get("unit_price", product.selling_price))
            line_total = qty * unit_price  # taxable
            tax = round(line_total * product.gst_rate / 100.0, 2)

            line = InvoiceItem(
                invoice_id=inv.id, product_id=pid,
                quantity=qty, unit_price=unit_price,
                gst_rate=product.gst_rate,
                line_total=line_total, tax_amount=tax,
            )
            db.session.add(line)
            sold_items.setdefault(pid, []).append(line)
            take_stock(product, qty, "sale")
            subtotal += line_total
            total_tax += tax

        # ---- promotions, written onto the bill --------------------------------
        # A reward line is a real invoice line at a real quantity, so its stock
        # moves exactly as a sold one does — through the same `take_stock`,
        # against this same bill, under its own reason.
        #
        # Inside a SAVEPOINT so that an offer that blows up here undoes only
        # itself. Without one, a half-written application would ride along on the
        # commit below and the bill would carry a promotion nobody can account
        # for; with the outer rollback instead, a bad scheme would cost the shop
        # the sale. Neither is acceptable at a counter with a customer at it.
        promo_free_value = 0.0
        if outcome.awards or outcome.notices:
            mark = db.session.begin_nested()
            before = (subtotal, total_tax)
            try:
                for line, product, qty in promotions.apply_to_invoice(
                        inv, outcome, sold_items, place=_place(),
                        user_id=current_user.id):
                    take_stock(product, qty, promotions.MOVEMENT_REASON)
                    subtotal += line.line_total
                    total_tax += line.tax_amount
                    promo_free_value += line.promo_value or 0
                mark.commit()
            except Exception:                        # noqa: BLE001
                mark.rollback()
                subtotal, total_tax = before
                promo_free_value = 0.0
                current_app.logger.warning("promotions skipped for this bill",
                                           exc_info=True)

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

        # ---- settlement -------------------------------------------------------
        # Checked HERE and nowhere earlier, because this is the first moment the
        # amount to settle actually exists: the discount and the points redeemed
        # both come off before it, and a till that balanced its tenders against
        # the pre-loyalty figure would refuse every sale a customer used points on.
        if payments is None:
            # the old single-method shape — settle the whole bill that way
            payments = [{"method": payment_method, "amount": inv.total,
                         "tendered": inv.total, "reference": None}]
        settled = round(sum(p["amount"] for p in payments), 2)
        if abs(settled - inv.total) > SETTLE_TOLERANCE:
            db.session.rollback()
            short = round(inv.total - settled, 2)
            return jsonify({
                "error": (f"Payment does not settle the bill — "
                          f"₹{abs(short):.2f} {'short' if short > 0 else 'over'}. "
                          f"Bill ₹{inv.total:.2f}, entered ₹{settled:.2f}."),
                "total": inv.total, "settled": settled, "balance": short}), 400
        for p in payments:
            db.session.add(InvoicePayment(
                invoice_id=inv.id, method=p["method"], amount=p["amount"],
                tendered=p["tendered"], reference=p["reference"]))
        inv.payment_method = payment_method

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
        return jsonify({"success": True, "invoice_id": inv.id,
                        "invoice_number": inv.invoice_number,
                        "total": inv.total, "payment_method": inv.payment_method,
                        # what to hand back, so the counter can say it out loud
                        # instead of the cashier working it out on the counter
                        "change": inv.change_given,
                        # …and what to put in the bag that nobody paid for, for
                        # the same reason: it is the other thing that has to
                        # physically happen before the customer walks away.
                        "free_items": [
                            {"name": l.product.name, "sku": l.product.sku,
                             "qty": l.quantity,
                             "scheme": l.promo_application.scheme_name
                                       if l.promo_application else ""}
                            for l in inv.items if l.promo_role == "reward"],
                        "promo_benefit": round(promo_free_value, 2)})
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
    series    = (request.args.get("series") or "").strip().upper()
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
    # On the prefix the bill was BUILT with, not on the text of its number: a
    # floor whose prefix was changed later still returns the bills it raised, and
    # a LIKE on the number would quietly miss them.
    if series: query = query.filter(Invoice.bill_prefix == series)
    if min_amt is not None: query = query.filter(Invoice.total >= min_amt)
    if max_amt is not None: query = query.filter(Invoice.total <= max_amt)

    # Ordered by the SEQUENCE within a chosen series, because that is the whole
    # question somebody filtering to one floor is asking — "…001, …002, …003,
    # and is anything missing". Across all floors the sequences interleave and
    # mean nothing together, so those stay in date order.
    if series:
        invoices = query.order_by(Invoice.fin_year.desc(),
                                  Invoice.bill_seq.desc()).limit(500).all()
    else:
        invoices = query.order_by(Invoice.invoice_date.desc()).limit(500).all()
    summary = {
        "count": len(invoices),
        "total": sum(i.total for i in invoices),
        "tax":   sum(i.cgst + i.sgst + i.igst for i in invoices),
    }
    cashiers = User.query.filter_by(active=True).order_by(User.full_name).all()

    # What the series dropdown offers: every prefix that has actually been billed
    # on, named by the floor it belongs to. Built from the bills rather than from
    # the floor master so a series that predates a rename, or one whose floor has
    # since been removed, is still selectable — the bills exist either way.
    from app.models import Floor as _Floor
    named = {f.prefix: f"{f.location.name} · {f.name}"
             for f in _Floor.query.all() if f.location}
    used = [p for (p,) in db.session.query(Invoice.bill_prefix)
            .filter(Invoice.bill_prefix.isnot(None)).distinct().all() if p]
    series_options = sorted(
        (p, f"{p} — {named.get(p, 'the shop’s own series' if p == billing_numbers.FALLBACK_PREFIX else 'no floor uses this now')}")
        for p in used)

    return render_template(
        "pos/invoices.html",
        invoices=invoices, summary=summary, cashiers=cashiers,
        q=q, date_from=date_from, date_to=date_to,
        cashier_id=cashier, payment=payment,
        series=series, series_options=series_options,
        min_amt=min_amt if min_amt is not None else "",
        max_amt=max_amt if max_amt is not None else "",
    )
