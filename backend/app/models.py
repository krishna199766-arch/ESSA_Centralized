"""
Relational model for the document-intake system.

Supplier         one row per vendor Essa buys from
SupplierProfile  the LEARNED template for a supplier ("train once per format").
                 Holds detection keys, tax behaviour, column mapping and a
                 reference example. Versioned so retraining never loses history.
Document         one uploaded invoice image/PDF + its lifecycle status
Extraction       an engine run over a Document -> canonical JSON + confidence.
                 A Document can have several (raw draft, human-corrected, ...).
LineItem         denormalised line rows for querying/reporting once confirmed.
"""
import datetime as dt
from sqlalchemy import (Column, Integer, String, Float, Text, DateTime,
                        ForeignKey, JSON, Boolean)
from sqlalchemy.orm import relationship
from .database import Base


def now():
    return dt.datetime.utcnow()


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    gstin = Column(String, index=True)
    pan = Column(String)
    state = Column(String)
    state_code = Column(String)
    address = Column(Text)
    phone = Column(String)
    email = Column(String)
    bank = Column(JSON, default=dict)
    aliases = Column(JSON, default=list)   # alternate names seen on invoices
    created_at = Column(DateTime, default=now)

    profiles = relationship("SupplierProfile", back_populates="supplier",
                            cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="supplier")

    @property
    def active_profile(self):
        active = [p for p in self.profiles if p.is_active]
        return max(active, key=lambda p: p.version) if active else None


class SupplierProfile(Base):
    """The trained format for one supplier. This is what makes 'train once,
    reuse forever' work: created/updated when a human confirms a correction."""
    __tablename__ = "supplier_profiles"
    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    template_key = Column(String, index=True)
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)

    # --- detection: how we recognise this format on a fresh upload ---
    detect_gstin = Column(String, index=True)
    detect_keywords = Column(JSON, default=list)   # strings that appear in header

    # --- extraction guidance handed to whichever provider runs ---
    tax_mode = Column(String, default="auto")      # intra_state | inter_state | auto
    default_tax_rates = Column(JSON, default=dict)  # {"igst":5} or {"cgst":2.5,"sgst":2.5}
    has_tds = Column(Boolean, default=False)
    column_map = Column(JSON, default=dict)         # printed header -> canonical field
    field_hints = Column(JSON, default=dict)        # regex/labels for header fields
    uom_default = Column(String, default="PCS")

    # --- a corrected example used as a few-shot reference for the vision model ---
    reference_example = Column(JSON, default=dict)

    trained_from_document_id = Column(Integer, ForeignKey("documents.id"))
    sample_count = Column(Integer, default=0)       # how many docs confirmed this profile
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    supplier = relationship("Supplier", back_populates="profiles")


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    content_hash = Column(String, index=True)
    mime = Column(String)
    document_type = Column(String, default="invoice")   # invoice | lr_register | purchase_order
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    # uploaded -> extracted -> needs_review -> confirmed -> posted
    status = Column(String, default="uploaded", index=True)
    uploaded_at = Column(DateTime, default=now)

    supplier = relationship("Supplier", back_populates="documents")
    extractions = relationship("Extraction", back_populates="document",
                               cascade="all, delete-orphan", order_by="Extraction.id")
    line_items = relationship("LineItem", back_populates="document",
                              cascade="all, delete-orphan")

    @property
    def latest_extraction(self):
        return self.extractions[-1] if self.extractions else None


class Extraction(Base):
    __tablename__ = "extractions"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"))
    provider = Column(String)                    # seeded | tesseract | claude_vision | human
    profile_id = Column(Integer, ForeignKey("supplier_profiles.id"), nullable=True)
    data = Column(JSON)                          # canonical invoice dict
    confidence = Column(Float, default=0.0)
    warnings = Column(JSON, default=list)         # reconciliation issues
    field_flags = Column(JSON, default=dict)      # field -> "ok"|"review"
    is_correction = Column(Boolean, default=False)
    raw_text = Column(Text)
    created_at = Column(DateTime, default=now)

    document = relationship("Document", back_populates="extractions")


class LineItem(Base):
    __tablename__ = "line_items"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"))
    barcode = Column(String)
    description = Column(String)
    hsn = Column(String)
    qty = Column(Float)
    uom = Column(String)
    rate = Column(Float)
    amount = Column(Float)

    document = relationship("Document", back_populates="line_items")


# ============================================================================
#  Inventory + Purchase / GRN  (the module that consumes confirmed extractions)
# ============================================================================
class Product(Base):
    """The inventory master. One row per distinct stock item. Identified by
    barcode when the supplier prints one, otherwise by (description, hsn,
    supplier). Stock qty and a weighted-average cost are maintained by the
    stock-movement ledger — never edited directly."""
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    sku = Column(String, unique=True, index=True)      # internal code we assign
    barcode = Column(String, index=True)               # supplier barcode if any
    description = Column(String, nullable=False)
    hsn = Column(String, index=True)
    uom = Column(String, default="PCS")
    mrp = Column(Float)
    primary_supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    # maintained by stock movements:
    stock_qty = Column(Float, default=0.0)
    avg_cost = Column(Float, default=0.0)              # weighted-average purchase cost
    last_rate = Column(Float)
    created_at = Column(DateTime, default=now)

    # --- physical-detail attributes captured by the warehouse mobile app ---
    # (employee inspects each product and records what they see)
    color = Column(String)
    size = Column(String)
    pattern = Column(String)
    fit = Column(String)
    product_type = Column(String)      # the video's "Type"
    material = Column(String)
    design_no = Column(String)
    # category master classification, mapped from the invoice description by
    # services/categorize.py (auto-applied only when the match is confident)
    category = Column(String, index=True)          # e.g. LADIES-T-SHIRT
    category_section = Column(String)              # OVERALL | KIDS | LADIES | MENS
    sale_price = Column(Float)
    margin_pct = Column(Float)
    detailed = Column(Boolean, default=False, index=True)
    detailed_at = Column(DateTime)
    detailed_by = Column(String)

    primary_supplier = relationship("Supplier")
    movements = relationship("StockMovement", back_populates="product",
                             cascade="all, delete-orphan", order_by="StockMovement.id")

    @property
    def stock_value(self):
        return round((self.stock_qty or 0) * (self.avg_cost or 0), 2)


class Purchase(Base):
    """A purchase / GRN header, created from a confirmed Document extraction.
    status: draft -> posted. Posting writes stock movements (idempotent)."""
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    grn_no = Column(String)
    invoice_number = Column(String)
    invoice_date = Column(String)
    taxable_total = Column(Float, default=0.0)
    tax_total = Column(Float, default=0.0)
    grand_total = Column(Float, default=0.0)
    status = Column(String, default="draft", index=True)   # draft | posted
    posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    document = relationship("Document")
    supplier = relationship("Supplier")
    lines = relationship("PurchaseLine", back_populates="purchase",
                         cascade="all, delete-orphan", order_by="PurchaseLine.id")


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"
    id = Column(Integer, primary_key=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    barcode = Column(String)
    description = Column(String)
    hsn = Column(String)
    qty = Column(Float)
    uom = Column(String)
    rate = Column(Float)
    amount = Column(Float)
    is_new_product = Column(Boolean, default=False)   # matched vs newly created
    # category master classification chosen at GRN time. Set here, the product is
    # born mapped instead of landing "unmapped" for someone to fix in Inventory;
    # left blank, services/categorize.py still auto-maps from the description.
    category = Column(String)

    purchase = relationship("Purchase", back_populates="lines")
    product = relationship("Product")
    splits = relationship("PurchaseLineSplit", back_populates="line",
                          cascade="all, delete-orphan", order_by="PurchaseLineSplit.id")

    @property
    def split_qty(self):
        """Total quantity accounted for by the attribute breakdown (0 if none)."""
        return round(sum((s.qty or 0) for s in self.splits), 3)

    @property
    def is_split(self):
        return bool(self.splits)


class PurchaseLineSplit(Base):
    """One VARIANT of a GRN line — the attribute breakdown of a billed bundle.

    Suppliers bill a bundle — "WOMEN T-SHIRT, 250 PCS" — and never print the mix,
    but the goods arrive as distinct items and inventory has to carry each on its
    own. So the warehouse breaks the line up here, at GRN, before anything reaches
    stock: 50 S in cotton, 50 M in cotton, 70 L printed, and so on. Posting turns
    every row into its own Product (own SKU + QR) with its own inward movement,
    which is what lets one variant be priced, scanned and dispatched by itself.

    A variant's identity is its whole attribute tuple (see SPLIT_ATTRS in
    services/inventory.py) — same description with a different colour or material
    is a different stock item, so re-buying an exact variant merges while anything
    new is created.

    The invoice line itself is left untouched — it stays the record of what the
    supplier actually billed, so invoice arithmetic and the payables side keep
    reconciling against the original document."""
    __tablename__ = "purchase_line_splits"
    id = Column(Integer, primary_key=True)
    line_id = Column(Integer, ForeignKey("purchase_lines.id"))
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    # --- the attributes that make this row a distinct stock item ---
    size = Column(String)
    color = Column(String)
    material = Column(String)
    pattern = Column(String)
    fit = Column(String)
    product_type = Column(String)
    design_no = Column(String)
    # not part of identity — a classification the product carries (falls back to
    # the line's category, then to auto-mapping from the description)
    category = Column(String)
    qty = Column(Float, default=0.0)
    rate = Column(Float)                 # unit cost (defaults to the line's rate)
    mrp = Column(Float)                  # retail prices are per variant, not per bundle
    sale_price = Column(Float)
    margin_pct = Column(Float)
    # a QR/barcode scanned on the floor pins this variant to an existing product
    # instead of leaving it to the description + attribute match
    code = Column(String)
    is_new_product = Column(Boolean, default=False)   # set when posting created it
    created_at = Column(DateTime, default=now)

    line = relationship("PurchaseLine", back_populates="splits")
    product = relationship("Product")

    @property
    def variant_label(self):
        """The attributes as one readable string — "L · Red · Cotton"."""
        bits = [self.size, self.color, self.material, self.pattern, self.fit,
                self.product_type, self.design_no]
        return " · ".join(str(b) for b in bits if b)

    @property
    def effective_rate(self):
        """Unit cost for this size: its own if set, otherwise what was billed."""
        if self.rate is not None:
            return self.rate
        return self.line.rate if self.line else None

    @property
    def amount(self):
        return round((self.qty or 0) * (self.effective_rate or 0), 2)


class StockMovement(Base):
    """Append-only ledger. Every stock change is a row with the running balance,
    so inventory is always reconstructable and auditable."""
    __tablename__ = "stock_movements"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    qty_delta = Column(Float)                 # +inward / -outward
    kind = Column(String)                     # inward | outward | adjustment
    ref_type = Column(String)                 # purchase | ...
    ref_id = Column(Integer)
    rate = Column(Float)                      # unit cost for this movement
    balance_after = Column(Float)
    note = Column(String)
    created_at = Column(DateTime, default=now)

    product = relationship("Product", back_populates="movements")


# ============================================================================
#  Stock Outward  (Warehouse → destination dispatch / transfer)
# ============================================================================
class StockOutward(Base):
    """A dispatch / inter-location transfer out of the warehouse. Posting
    decrements product stock (negative StockMovement, kind='outward')."""
    __tablename__ = "stock_outwards"
    id = Column(Integer, primary_key=True)
    code = Column(String)                       # package / order code
    date = Column(String)
    from_company = Column(String, default="Essa Garments Private Limited")
    from_location = Column(String, default="WAREHOUSE")
    to_destination = Column(String)             # store / customer
    packed_by = Column(String)
    received_by = Column(String)
    status = Column(String, default="draft", index=True)   # draft | posted | received
    posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    lines = relationship("StockOutwardLine", back_populates="outward",
                         cascade="all, delete-orphan", order_by="StockOutwardLine.id")

    @property
    def total_qty(self):
        return sum((l.qty or 0) for l in self.lines)


class StockOutwardLine(Base):
    __tablename__ = "stock_outward_lines"
    id = Column(Integer, primary_key=True)
    outward_id = Column(Integer, ForeignKey("stock_outwards.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    barcode = Column(String)
    description = Column(String)
    qty = Column(Float)                         # sent / transferred
    accepted_qty = Column(Float)               # accepted at destination (defaults to qty)
    rate = Column(Float)                        # cost at dispatch

    outward = relationship("StockOutward", back_populates="lines")
    product = relationship("Product")


# ============================================================================
#  Supplier Payments  (Finance / Supplier Payment — accounts payable)
# ============================================================================
class Payment(Base):
    """A supplier payment receipt settling one or more purchase invoices, with
    optional discount, TDS and debit-note adjustments (matching the app)."""
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    receipt_no = Column(String, index=True)     # ESP#####
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    date = Column(String)
    mode = Column(String, default="NEFT")       # NEFT/RTGS | Cash | Cheque
    bank = Column(String)
    cheque_no = Column(String)
    cheque_date = Column(String)
    ref_no = Column(String)
    remarks = Column(String)
    gross_amount = Column(Float, default=0.0)   # sum of selected invoice totals
    discount_total = Column(Float, default=0.0)
    tds_total = Column(Float, default=0.0)
    debit_adjust_total = Column(Float, default=0.0)
    paid_amount = Column(Float, default=0.0)    # actual cash out
    created_at = Column(DateTime, default=now)

    supplier = relationship("Supplier")
    allocations = relationship("PaymentAllocation", back_populates="payment",
                               cascade="all, delete-orphan")


class PaymentAllocation(Base):
    """How a payment is split across the invoices it settles."""
    __tablename__ = "payment_allocations"
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"))
    purchase_id = Column(Integer, ForeignKey("purchases.id"))
    invoice_number = Column(String)
    invoice_total = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)
    tds = Column(Float, default=0.0)
    debit_adjust = Column(Float, default=0.0)
    settled = Column(Float, default=0.0)        # discount+tds+debit+cash applied to this invoice

    payment = relationship("Payment", back_populates="allocations")
    purchase = relationship("Purchase")


# ============================================================================
#  Purchase Return  (Warehouse / Purchase Return — debit note to supplier)
# ============================================================================
class PurchaseReturn(Base):
    """Goods returned to a supplier against a reference purchase invoice.
    Posting reverses stock (negative StockMovement, kind='return') and raises a
    debit note that reduces the supplier's payable for that invoice."""
    __tablename__ = "purchase_returns"
    id = Column(Integer, primary_key=True)
    code = Column(String)                       # PR-#####  (debit note no)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)  # reference invoice
    invoice_number = Column(String)             # reference invoice no
    date = Column(String)
    reason = Column(String)
    taxable_total = Column(Float, default=0.0)
    tax_total = Column(Float, default=0.0)
    total = Column(Float, default=0.0)          # debit note value (reduces payable)
    status = Column(String, default="draft", index=True)   # draft | posted
    posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    supplier = relationship("Supplier")
    purchase = relationship("Purchase")
    lines = relationship("PurchaseReturnLine", back_populates="ret",
                         cascade="all, delete-orphan", order_by="PurchaseReturnLine.id")


class PurchaseReturnLine(Base):
    __tablename__ = "purchase_return_lines"
    id = Column(Integer, primary_key=True)
    return_id = Column(Integer, ForeignKey("purchase_returns.id"))
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    barcode = Column(String)
    description = Column(String)
    hsn = Column(String)
    qty = Column(Float)                         # qty returned
    rate = Column(Float)
    amount = Column(Float)

    ret = relationship("PurchaseReturn", back_populates="lines")
    product = relationship("Product")


# ============================================================================
#  Masters — product categories (from the GRN Excel), agents, transporters
# ============================================================================
class Category(Base):
    """Product category master imported from GRN PRODUCT DETAILS.xlsx
    (sections OVERALL / KIDS / LADIES / MENS). Used to classify products."""
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    section = Column(String, index=True)      # sheet: OVERALL | KIDS | LADIES | MENS
    name = Column(String, index=True)         # e.g. KIDS-BABASUIT
    created_at = Column(DateTime, default=now)


class Agent(Base):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)
    phone = Column(String)
    created_at = Column(DateTime, default=now)


class Transport(Base):
    __tablename__ = "transports"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)
    phone = Column(String)
    created_at = Column(DateTime, default=now)


class LREntry(Base):
    """LR Entry register — one row per received consignment (from the TRANSPORT
    Excel / LR book). Captured by OCR/vision from an uploaded image or PDF."""
    __tablename__ = "lr_entries"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    recv_date = Column(String)
    transport = Column(String)
    bundle = Column(Float)
    lr_no = Column(String)
    lr_date = Column(String)
    supplier_name = Column(String)
    inv_no = Column(String)
    inv_date = Column(String)
    qty = Column(Float)
    amount = Column(Float)
    paid_topay = Column(String)          # TOPAY | NO | PAID
    freight_amount = Column(Float)
    cash_cheque = Column(String)         # CASH | CHEQUE | NO
    # (a `place` column may still exist in older databases — the register's
    # booking-city column was dropped from the app; it is no longer mapped or read)
    item = Column(String)
    # Who physically RECEIVED the consignment. Not typed on the desktop and not
    # read off the register page: only the warehouse knows who took the packages,
    # so they record it from the phone app (POST /api/lr/{id}/receive) as the
    # goods land. The desktop shows it read-only.
    received_by = Column(String)
    # Freight settlement is recorded as mode + amount only (paid_topay,
    # freight_amount, cash_cheque). `paid_by` and `cash_receiver_mobile` columns
    # may still exist in older databases; they are no longer mapped or read.
    # --- invoice linkage (cross-fill): set when a matching invoice is uploaded ---
    invoice_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    matched = Column(Boolean, default=False, index=True)   # an invoice has been linked
    # fields where the linked invoice DISAGREES with the register value we kept:
    # [{"field","register","invoice"}] — register value is retained, conflict flagged
    mismatches = Column(JSON, default=list)
    created_at = Column(DateTime, default=now)
