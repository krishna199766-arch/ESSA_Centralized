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

    payment_method = db.Column(db.String(16), default="cash")  # cash/card/upi
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
