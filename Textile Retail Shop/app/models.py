from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


# ---------- Users / Staff ----------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(128))
    phone = db.Column(db.String(32))
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="cashier")  # admin/manager/cashier
    salary = db.Column(db.Float, default=0.0)
    commission_pct = db.Column(db.Float, default=0.0)  # % of sales
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attendance = db.relationship("Attendance", backref="user", lazy=True)
    # Spelled out because an invoice now points at users twice — the till login
    # and the staff member served it — and SQLAlchemy cannot guess which.
    invoices = db.relationship("Invoice", backref="cashier", lazy=True,
                               foreign_keys="Invoice.cashier_id")
    sales = db.relationship("Invoice", backref="staff", lazy=True,
                            foreign_keys="Invoice.staff_id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def staff_code(self):
        """What is printed on this person's ID card and typed at the counter.

        Derived from the id rather than stored, the same way a customer's card
        number is: there is nothing to keep in step, and a code can never go
        missing or be issued twice.
        """
        return f"STF{self.id:06d}"

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_manager(self):
        return self.role in ("admin", "manager")


class Attendance(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    check_in = db.Column(db.DateTime, default=datetime.utcnow)
    check_out = db.Column(db.DateTime)
    notes = db.Column(db.String(256))


# ---------- Inventory ----------
class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    # Which sheet of the warehouse master this code came from: OVERALL | KIDS |
    # LADIES | MENS. Null means it predates the master or was typed in by hand —
    # see app/master_categories.py.
    section = db.Column(db.String(16), index=True)
    description = db.Column(db.String(256))
    products = db.relationship("Product", backref="category", lazy=True)


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), unique=True, nullable=False, index=True)
    barcode = db.Column(db.String(64), index=True)  # EAN/UPC/Code128 printed barcode
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.String(256))
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    hsn_code = db.Column(db.String(16), default="5208")  # cotton fabrics HSN default
    unit = db.Column(db.String(16), default="pcs")  # pcs / mtr / kg
    cost_price = db.Column(db.Float, default=0.0)
    selling_price = db.Column(db.Float, nullable=False)
    gst_rate = db.Column(db.Float, default=5.0)  # % (0/5/12/18/28)
    stock_qty = db.Column(db.Float, default=0.0)
    reorder_level = db.Column(db.Float, default=5.0)
    color = db.Column(db.String(32))
    size = db.Column(db.String(32))
    fabric = db.Column(db.String(64))  # cotton, silk, polyester, etc
    active = db.Column(db.Boolean, default=True)
    # Which warehouse item this is. Null for anything the shop added on its own.
    # `warehouse_qr` is the exact payload the warehouse prints on the item's tag,
    # kept so the shop's label carries the SAME code — see app/warehouse_items.py.
    warehouse_id = db.Column(db.Integer, index=True)
    warehouse_qr = db.Column(db.Text)
    # The rest of the warehouse's attribute tuple, copied so the shop can show a
    # product in full without the warehouse being reachable. The shop doesn't
    # price or search on these; they are what the tag and the detail screen show.
    mrp = db.Column(db.Float)
    product_type = db.Column(db.String(64))
    pattern = db.Column(db.String(64))
    fit = db.Column(db.String(64))
    design_no = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_low_stock(self):
        return self.stock_qty <= self.reorder_level


class StockMovement(db.Model):
    """Audit log for every stock change."""
    __tablename__ = "stock_movements"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    change = db.Column(db.Float, nullable=False)  # +ve = inflow, -ve = outflow
    reason = db.Column(db.String(64))  # sale / purchase / adjustment / return
    reference = db.Column(db.String(64))  # invoice#/PO#
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship("Product")


# ---------- Customers & Loyalty ----------
class Customer(db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    phone = db.Column(db.String(32), index=True)
    email = db.Column(db.String(128))
    address = db.Column(db.String(256))
    gstin = db.Column(db.String(16))  # for B2B customers
    state_code = db.Column(db.String(4), default="33")
    loyalty_points = db.Column(db.Float, default=0.0)
    total_spent = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoices = db.relationship("Invoice", backref="customer", lazy=True)

    @property
    def card_code(self):
        """Stable membership-card / barcode value for this customer."""
        return f"CUST{self.id:06d}"


class LoyaltyTxn(db.Model):
    __tablename__ = "loyalty_txns"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    points = db.Column(db.Float, nullable=False)  # +ve earned, -ve redeemed
    reason = db.Column(db.String(64))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# Suppliers and purchase orders used to live here. They are the warehouse's
# job now — stock reaches the shop by GRN, and buying from a mill is not
# something a till does — so the models, routes and screens were removed
# rather than left as a second, unused way to record the same thing.


# ---------- Invoices / Sales ----------
class Company(db.Model):
    """A legal entity that raises bills.

    Not decoration and not a label: a tax invoice carries the GSTIN of whoever
    issued it, and two companies trading from one shop file two returns. So the
    header the bill prints comes from the row picked at the till, and an invoice
    remembers which one raised it — otherwise a month's sales cannot be split
    between them afterwards, and that is the one thing this has to survive.
    """
    __tablename__ = "companies"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    gstin = db.Column(db.String(20))
    address = db.Column(db.String(256))
    state_code = db.Column(db.String(4))
    phone = db.Column(db.String(32))
    #: what a till starts on before anybody chooses. The shop's own config
    #: becomes this on first run, so a single-company shop never sees the picker
    #: as a decision it has to make.
    is_default = db.Column(db.Boolean, default=False, index=True)
    active = db.Column(db.Boolean, default=True, index=True)
    locations = db.relationship("Location", backref="company", lazy=True)


class Location(db.Model):
    """A place that sells — a branch, a floor, a counter's address.

    The names come from the WAREHOUSE's own list (app/places.sync_locations), not
    from a second master kept here. The warehouse already dispatches stock to
    these places by name; a shop with its own spelling of the same branch cannot
    be asked "what did we send there, and what did they sell".
    """
    __tablename__ = "locations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), index=True)
    #: True for a name this shop added itself, so a sync that no longer lists it
    #: does not delete it. See places.sync_locations.
    local = db.Column(db.Boolean, default=False)
    active = db.Column(db.Boolean, default=True, index=True)
    counters = db.relationship("Counter", backref="location", lazy=True)


class Counter(db.Model):
    """One till at one location. Two tills at a branch are two drawers."""
    __tablename__ = "counters"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"),
                            nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, index=True)
    __table_args__ = (db.UniqueConstraint("location_id", "name",
                                          name="uq_counter_location_name"),)


class LocationStock(db.Model):
    """How much of one product is at one place.

    `Product.stock_qty` is what this SHOP holds altogether, and it stays the
    figure the till sells against. This is the split of it: what arrived at each
    branch and what has sold there. The two are kept in step, not derived from
    each other — a shop whose branches were stocked before any of this existed
    has a total and no split, and inventing one by dividing would be making up
    numbers about real goods.
    """
    __tablename__ = "location_stock"
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"),
                            nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"),
                           nullable=False, index=True)
    qty = db.Column(db.Float, default=0.0)
    location = db.relationship("Location")
    product = db.relationship("Product")
    __table_args__ = (db.UniqueConstraint("location_id", "product_id",
                                          name="uq_location_stock"),)


class TransferReceipt(db.Model):
    """One dispatched line from the warehouse, taken into a branch's stock.

    The point of this table is that it happens ONCE. The sync reads the
    warehouse's outward lines on every start, and without a record of what has
    already been applied, a shop's stock would grow by the whole delivery every
    time the till was restarted. So the warehouse's own line id is unique here,
    and it is the thing that makes the import safe to repeat.

    It is also the audit trail: which transfer, on what day, brought these pieces
    to this branch. `qty` is what was ACCEPTED at the far end, not what was sent —
    the warehouse already records the difference as a discrepancy, and a shop that
    took in the sent figure would be holding pieces that never arrived.
    """
    __tablename__ = "transfer_receipts"
    id = db.Column(db.Integer, primary_key=True)
    #: StockOutwardLine.id in the warehouse — the natural key, and unique so the
    #: same dispatch cannot be taken in twice
    wh_line_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    wh_outward_id = db.Column(db.Integer, index=True)
    code = db.Column(db.String(32))                # the transfer note's code
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), index=True)
    qty = db.Column(db.Float, default=0.0)
    received_on = db.Column(db.String(16))
    applied_at = db.Column(db.DateTime, default=datetime.utcnow)
    location = db.relationship("Location")
    product = db.relationship("Product")


class Invoice(db.Model):
    __tablename__ = "invoices"
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(32), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    # Two different people, and the difference matters at payroll.
    # `cashier_id` is the login that rang the sale — who was on the till, and
    # therefore who answers for the drawer. `staff_id` is who served the customer,
    # identified by their code or ID card at the start of the sale, and that is
    # who the commission belongs to (see routes/staff.py). It is nullable because
    # every invoice raised before staff were identified has no answer for it.
    cashier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    invoice_date = db.Column(db.DateTime, default=datetime.utcnow)

    subtotal = db.Column(db.Float, default=0.0)     # sum of taxable amounts
    discount = db.Column(db.Float, default=0.0)
    cgst = db.Column(db.Float, default=0.0)
    sgst = db.Column(db.Float, default=0.0)
    igst = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)

    loyalty_earned = db.Column(db.Float, default=0.0)
    loyalty_redeemed = db.Column(db.Float, default=0.0)

    # How it was settled, in one word, for the screens and reports that want a
    # single answer: cash / card / upi, or "mixed" when it took more than one.
    # The real breakdown is in `payments` below — this stays because every
    # invoice raised before settlements existed has only this, and the invoice
    # list, the printed bill and the day's reports all read it.
    payment_method = db.Column(db.String(16), default="cash")  # cash/card/upi/mixed
    # Who billed it, from where, at which till. Nullable because every invoice
    # raised before there were counters has no answer, and inventing one would be
    # worse than leaving it blank. The company is the one that MATTERS: it is
    # whose GSTIN went on the bill.
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    counter_id = db.Column(db.Integer, db.ForeignKey("counters.id"), index=True)
    company = db.relationship("Company")
    location = db.relationship("Location")
    counter = db.relationship("Counter")
    payment_status = db.Column(db.String(16), default="paid")  # paid/pending
    is_interstate = db.Column(db.Boolean, default=False)
    notes = db.Column(db.String(256))

    items = db.relationship("InvoiceItem", backref="invoice", lazy=True, cascade="all, delete-orphan")
    payments = db.relationship("InvoicePayment", backref="invoice", lazy=True,
                               cascade="all, delete-orphan",
                               order_by="InvoicePayment.id")

    @property
    def settled(self):
        """What was actually tendered against this bill, by method.

        Falls back to the whole total under `payment_method` for every invoice
        raised before settlements were recorded — and for the floor-sales path,
        which still bills a single method. Without that fallback the day's cash
        figure would drop to zero the moment this table arrived, on history that
        was perfectly well recorded.
        """
        if self.payments:
            out = {}
            for p in self.payments:
                out[p.method] = round(out.get(p.method, 0.0) + (p.amount or 0), 2)
            return out
        return {(self.payment_method or "cash"): round(self.total or 0, 2)}

    @property
    def change_given(self):
        """Cash handed back. Only cash is ever over-tendered."""
        return round(sum(max(0.0, (p.tendered or p.amount or 0) - (p.amount or 0))
                         for p in self.payments if p.method == "cash"), 2)

    @property
    def total_qty(self):
        """Pieces on this bill, which is what a delivery counter counts in."""
        return round(sum(i.quantity for i in self.items), 3)

    @property
    def delivered_qty(self):
        """Pieces the customer has actually carried out.

        Counted, not inferred from `total_qty - pending_qty`: that subtraction
        also picks up anything returned on a credit note, and would report goods
        the customer handed BACK as goods they collected.
        """
        return round(sum(i.delivered_qty for i in self.items), 3)

    @property
    def pending_qty(self):
        """Pieces still to be collected. 0 once the customer has everything."""
        return round(sum(i.pending_qty for i in self.items), 3)

    @property
    def delivery_status(self):
        """`pending` · `part` · `delivered` — what the counter still owes.

        Derived, never stored. A stored flag is a second answer to a question the
        delivery lines already answer, and the two would disagree the first time
        a credit note changed what was owed.
        """
        pending = self.pending_qty
        if pending <= 0:
            return "delivered"
        return "part" if any(i.delivered_qty for i in self.items) else "pending"


class InvoicePayment(db.Model):
    """One tender against one bill — ₹2,000 cash, ₹1,340 on a card.

    A bill is settled by however many of these add up to its total, which is what
    lets a customer put half on a card and hand over the rest. One column on the
    invoice could not say that; it could only say which method to blame the whole
    amount on, and the day's cash reconciliation would be wrong by whatever the
    other half was.

    `tendered` is what physically changed hands and is only ever different for
    cash: a customer pays 2,000 against 1,860 and takes 140 back. The bill is
    still settled for 1,860 — `amount` is what the invoice received, `tendered`
    is what the drawer saw — and keeping both is the only way the till and the
    invoice can each be right.
    """
    __tablename__ = "invoice_payments"
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"),
                           nullable=False, index=True)
    method = db.Column(db.String(16), nullable=False)      # cash | card | upi
    amount = db.Column(db.Float, nullable=False, default=0.0)
    # What was handed over. Equal to `amount` for everything but over-tendered cash.
    tendered = db.Column(db.Float)
    # The card's last four, a UPI reference, an approval code — whatever the
    # cashier has to be able to quote when a payment is queried later.
    reference = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def change(self):
        return round(max(0.0, (self.tendered or self.amount or 0) - (self.amount or 0)), 2)


#: The tenders a till accepts. One list, so the counter, the settlement check and
#: the reports cannot disagree about what a payment method is.
PAYMENT_METHODS = ("cash", "card", "upi")


class InvoiceItem(db.Model):
    __tablename__ = "invoice_items"
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)  # pre-tax
    gst_rate = db.Column(db.Float, default=0.0)
    line_total = db.Column(db.Float, nullable=False)  # taxable amount (qty*price)
    tax_amount = db.Column(db.Float, default=0.0)
    product = db.relationship("Product")

    @property
    def returned_qty(self):
        """How much of this line has already come back on a credit note."""
        return round(sum(c.quantity for c in self.credit_lines), 3)

    @property
    def returnable_qty(self):
        """What is still left to return — nothing can come back twice."""
        return max(0.0, round(self.quantity - self.returned_qty, 3))

    @property
    def delivered_qty(self):
        """How much of this line the customer has physically collected."""
        return round(sum(d.quantity for d in self.delivery_lines), 3)

    @property
    def pending_qty(self):
        """What the shop still owes the customer on this line.

        Goods that came back on a credit note are subtracted, and that is a
        judgement rather than an identity: nothing records whether a returned
        piece had been collected first. A return of something already collected
        leaves this UNDER-stating what is owed by that amount; not subtracting
        would leave it over-stating on a return raised at the delivery counter
        before the goods ever moved.

        Under-stating is the one to live with. It shows up as a customer at the
        counter with a bill and a discrepancy a person then looks at; the other
        way round hands over goods that have already been refunded, silently.
        """
        return max(0.0, round(self.quantity - self.delivered_qty
                              - self.returned_qty, 3))


# ---------- Returns ----------
class CreditNote(db.Model):
    """Goods coming back, against the bill they went out on.

    Its own document rather than a negative invoice: a sale and a refund are
    different events, and blurring them means every sales figure, GST return and
    invoice list has to remember to exclude the negatives. This way a credit note
    is countable on its own and the original invoice can show what came back.

    `staff_id` is who handled the return. It is NOT who loses the commission —
    that is the staff member on the original sale, which is what
    routes/staff.py subtracts from.
    """
    __tablename__ = "credit_notes"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(32), unique=True, nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    cashier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subtotal = db.Column(db.Float, default=0.0)      # goods value returned
    discount = db.Column(db.Float, default=0.0)      # share of the bill's discount
    cgst = db.Column(db.Float, default=0.0)
    sgst = db.Column(db.Float, default=0.0)
    igst = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)         # what the customer gets back

    loyalty_reversed = db.Column(db.Float, default=0.0)
    refund_method = db.Column(db.String(16), default="cash")
    reason = db.Column(db.String(256))

    invoice = db.relationship("Invoice", backref=db.backref("credit_notes", lazy=True))
    staff = db.relationship("User", foreign_keys=[staff_id])
    cashier = db.relationship("User", foreign_keys=[cashier_id])
    items = db.relationship("CreditNoteItem", backref="note", lazy=True,
                            cascade="all, delete-orphan")


class CreditNoteItem(db.Model):
    __tablename__ = "credit_note_items"
    id = db.Column(db.Integer, primary_key=True)
    credit_note_id = db.Column(db.Integer, db.ForeignKey("credit_notes.id"), nullable=False)
    # The exact line being returned, so a bill listing the same product twice at
    # different prices refunds the one actually handed back.
    invoice_item_id = db.Column(db.Integer, db.ForeignKey("invoice_items.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    gst_rate = db.Column(db.Float, default=0.0)
    line_total = db.Column(db.Float, nullable=False)
    tax_amount = db.Column(db.Float, default=0.0)
    # resellable goes back on the shelf; damaged comes back and is written off,
    # so the movement log tells the truth and the sellable count stays honest.
    condition = db.Column(db.String(16), default="resellable")

    product = db.relationship("Product")
    invoice_item = db.relationship("InvoiceItem", backref="credit_lines")


# ---------- Delivery ----------
class Delivery(db.Model):
    """Goods physically handed to the customer, against the bills they paid on.

    Billing and collection are two counters and two moments. The till already
    took the money and took the stock off the shelf; what nothing recorded until
    now is whether the customer actually walked out with the garments. In a shop
    where a bill is paid at one desk and the pieces are packed at another, that
    gap is where goods go missing and where "I paid for three and got two" has no
    answer either way.

    So this touches NO stock and NO money — the sale did both. It records
    custody, exactly as an `Alteration` does: who handed over what, from which
    bills, verified against the tag on each garment.

    **One delivery, several bills.** A customer buying at two counters leaves
    with one bundle, and the handover is that bundle, not each bill separately.
    That is what `DeliveryBill` is for, and it is why the reference screen's
    left-hand table has a Total under it. This document IS the settlement the
    reference ERP scans — the shop has no separate settlement or voucher record,
    and inventing an empty one to hold a number would be a document that never
    says anything.
    """
    __tablename__ = "deliveries"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(32), unique=True, nullable=False)
    # Who it was handed to. Copied from the bills rather than typed, and null for
    # a walk-in, exactly as an invoice is.
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)
    # Two people again, and the same split the invoice makes: `staff_id` handed
    # the goods over and is who a query about this delivery goes to; `cashier_id`
    # is the login that was open at the delivery desk.
    staff_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    cashier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Where the handover happened. The delivery desk is not always the till that
    # billed it, so this is the desk's own choice and not copied off the invoice.
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), index=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), index=True)
    counter_id = db.Column(db.Integer, db.ForeignKey("counters.id"), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    notes = db.Column(db.String(256))

    customer = db.relationship("Customer")
    staff = db.relationship("User", foreign_keys=[staff_id])
    cashier = db.relationship("User", foreign_keys=[cashier_id])
    company = db.relationship("Company")
    location = db.relationship("Location")
    counter = db.relationship("Counter")
    bills = db.relationship("DeliveryBill", backref="delivery", lazy=True,
                            cascade="all, delete-orphan")
    lines = db.relationship("DeliveryLine", backref="delivery", lazy=True,
                            cascade="all, delete-orphan")

    @property
    def total_qty(self):
        """Pieces handed over on this delivery."""
        return round(sum(l.quantity for l in self.lines), 3)

    @property
    def scanned_qty(self):
        """How many of them were read off a tag rather than ticked by hand."""
        return round(sum(l.scanned for l in self.lines), 3)

    @property
    def overridden_qty(self):
        """Pieces a manager passed without a scan. 0 on an ordinary delivery."""
        return round(self.total_qty - self.scanned_qty, 3)

    @property
    def total_amount(self):
        """What the goods in this handover were billed at."""
        return round(sum(l.amount for l in self.lines), 2)


class DeliveryBill(db.Model):
    """One bill covered by one handover.

    A plain link row and nothing more: the quantities live on `DeliveryLine`,
    which points at the invoice LINE. Holding a per-bill quantity here as well
    would be the same figure in two places, free to drift the moment a delivery
    is corrected.
    """
    __tablename__ = "delivery_bills"
    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey("deliveries.id"),
                            nullable=False, index=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"),
                           nullable=False, index=True)
    invoice = db.relationship("Invoice",
                              backref=db.backref("delivery_bills", lazy=True))
    __table_args__ = (db.UniqueConstraint("delivery_id", "invoice_id",
                                          name="uq_delivery_bill"),)


class DeliveryLine(db.Model):
    """How much of one invoice line went out on one delivery.

    Keyed on the invoice ITEM, not the product, for the reason a credit note is:
    a bill carrying the same garment twice at two prices has two lines, and the
    customer collecting one of them has collected a specific one.

    `scanned` is how many of `quantity` were verified against a tag. It is a
    count rather than a flag because a line of five can be four scans and one
    piece whose label came off in a bag, and a delivery that could only say
    "verified" or "not" would have to lie about which.
    """
    __tablename__ = "delivery_lines"
    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey("deliveries.id"),
                            nullable=False, index=True)
    invoice_item_id = db.Column(db.Integer, db.ForeignKey("invoice_items.id"),
                                nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    scanned = db.Column(db.Float, default=0.0)
    # Why anything on this line went out unscanned, and who allowed it. Both null
    # on a line that was scanned in full, which is the ordinary case.
    override_reason = db.Column(db.String(256))
    overridden_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    product = db.relationship("Product")
    overridden_by = db.relationship("User")
    invoice_item = db.relationship("InvoiceItem",
                                   backref=db.backref("delivery_lines", lazy=True))
    scans = db.relationship("DeliveryScan", backref="line", lazy=True,
                            cascade="all, delete-orphan")

    @property
    def unit_price(self):
        """What this was billed at. Read through, never copied.

        A posted invoice's rate cannot change, so a second copy here could only
        ever be the same number or a wrong one.
        """
        return (self.invoice_item.unit_price if self.invoice_item else 0.0) or 0.0

    @property
    def amount(self):
        return round(self.quantity * self.unit_price, 2)

    @property
    def overridden(self):
        """Pieces on this line that no tag was read for."""
        return round(self.quantity - (self.scanned or 0), 3)


class DeliveryScan(db.Model):
    """One tag actually read at the delivery desk.

    The evidence behind `DeliveryLine.scanned`, kept rather than counted away,
    because "which of these went out" is the whole reason the warehouse mints a
    code per garment (see the piece labels in the warehouse's ARCHITECTURE §7).
    A line that says 3 and three codes that say which three are different
    records, and only the second one settles a dispute.

    `piece_code` is filled only when the tag identified an individual garment —
    a `EU1|…` payload or a bare `ESSA-00002-007`. A plain SKU tag is printed
    identically on every piece of that item, so it carries no identity and
    scanning it three times for a line of three is correct. That distinction is
    what `delivery.piece_of()` decides, and it is the whole of the
    double-scan guard: a piece code may be read once, a SKU tag as often as the
    line has pieces.
    """
    __tablename__ = "delivery_scans"
    id = db.Column(db.Integer, primary_key=True)
    delivery_line_id = db.Column(db.Integer, db.ForeignKey("delivery_lines.id"),
                                 nullable=False, index=True)
    #: exactly what the reader produced, unparsed
    code = db.Column(db.Text)
    #: the individual garment, when the tag named one
    piece_code = db.Column(db.String(64), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------- Alterations ----------
class Tailor(db.Model):
    """Who does the stitching.

    Kept apart from staff on purpose: the tailor is usually an outside workshop
    that never logs in, has no shift and draws no commission. Giving them a login
    to appear in a dropdown would be the wrong shape.
    """
    __tablename__ = "tailors"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    phone = db.Column(db.String(32))
    notes = db.Column(db.String(256))
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Alteration(db.Model):
    """A garment gone back for tailoring, against the bill it was sold on.

    The garment is the customer's already — it left stock when it was sold — so
    nothing here touches stock or the ledger. What it tracks is custody: who has
    the piece, what was asked for, when it was promised, and whether the customer
    has it back.

    `charge` is what the alteration costs, usually nothing. It is collected when
    the garment is handed over, not when it is taken in, because until the work
    is done there is nothing to charge for.
    """
    __tablename__ = "alterations"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(32), unique=True, nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False, index=True)
    tailor_id = db.Column(db.Integer, db.ForeignKey("tailors.id"), index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("users.id"))      # took it in
    delivered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    status = db.Column(db.String(16), default="pending", index=True)  # pending/ready/delivered
    promised_date = db.Column(db.Date, index=True)
    remarks = db.Column(db.String(256))

    charge = db.Column(db.Float, default=0.0)
    charge_method = db.Column(db.String(16))       # how it was settled, at handover

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ready_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)

    invoice = db.relationship("Invoice", backref=db.backref("alterations", lazy=True))
    tailor = db.relationship("Tailor")
    staff = db.relationship("User", foreign_keys=[staff_id])
    delivered_by = db.relationship("User", foreign_keys=[delivered_by_id])
    items = db.relationship("AlterationItem", backref="alteration", lazy=True,
                            cascade="all, delete-orphan")

    @property
    def total_qty(self):
        return round(sum(i.quantity for i in self.items), 3)

    @property
    def is_overdue(self):
        """Promised, still not ready, and the day has passed."""
        return bool(self.promised_date
                    and self.status == "pending"
                    and self.promised_date < date.today())


class AlterationItem(db.Model):
    __tablename__ = "alteration_items"
    id = db.Column(db.Integer, primary_key=True)
    alteration_id = db.Column(db.Integer, db.ForeignKey("alterations.id"), nullable=False)
    invoice_item_id = db.Column(db.Integer, db.ForeignKey("invoice_items.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    # What the customer actually asked for — "shorten 2 inches", "take in waist".
    # The whole point of the ticket the tailor works from.
    instructions = db.Column(db.String(256))

    product = db.relationship("Product")
    invoice_item = db.relationship("InvoiceItem", backref="alteration_lines")


# ---------- Floor Sales (mobile salesperson + customer live view) ----------
class SaleSession(db.Model):
    """A draft cart shared between salesperson and customer via a short code."""
    __tablename__ = "sale_sessions"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(8), unique=True, nullable=False, index=True)
    salesperson_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    # open, awaiting_approval, approved, rejected, completed, cancelled
    status = db.Column(db.String(20), default="open", nullable=False, index=True)
    discount = db.Column(db.Float, default=0.0)
    redeem_points = db.Column(db.Float, default=0.0)
    payment_method = db.Column(db.String(16), default="cash")
    notes = db.Column(db.String(256))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    salesperson = db.relationship("User", foreign_keys=[salesperson_id])
    customer = db.relationship("Customer", foreign_keys=[customer_id])
    items = db.relationship("SaleSessionItem", backref="session",
                            lazy=True, cascade="all, delete-orphan")

    def totals(self):
        # 1 point == ₹1 off (see config LOYALTY_POINT_VALUE)
        subtotal = sum(i.line_total for i in self.items)
        tax = sum(i.tax_amount for i in self.items)
        discount = min(self.discount or 0, subtotal)
        pre_loyalty = max(0, subtotal - discount + tax)
        # Cap redemption at customer balance AND at the bill amount so we
        # never over-redeem.
        requested = self.redeem_points or 0
        available = (self.customer.loyalty_points if self.customer else 0) or 0
        redeem = min(requested, available, pre_loyalty)
        total = round(pre_loyalty - redeem, 2)
        return {
            "subtotal": round(subtotal, 2),
            "tax": round(tax, 2),
            "discount": round(discount, 2),
            "redeem_requested": round(requested, 2),
            "redeem_available": round(available, 2),
            "redeem": round(redeem, 2),  # actually applied
            "total": total,
        }


class SaleSessionItem(db.Model):
    __tablename__ = "sale_session_items"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sale_sessions.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    gst_rate = db.Column(db.Float, default=0.0)
    line_total = db.Column(db.Float, nullable=False)
    tax_amount = db.Column(db.Float, default=0.0)
    product = db.relationship("Product")

    def recompute(self):
        self.line_total = round(self.quantity * self.unit_price, 2)
        self.tax_amount = round(self.line_total * self.gst_rate / 100.0, 2)
