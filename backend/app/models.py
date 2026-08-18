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
from sqlalchemy.orm import relationship, backref
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
    # The unit this product is COUNTED in — its stock figure, its labels and its
    # sale are all one of these. A pillow cover is a PAIR, so a dozen received is
    # six of them; a handkerchief is a PCS, so a dozen is twelve. `uom` above is
    # kept in step and is what screens display.
    #
    # `pieces_per_unit` is frozen here at creation rather than read from the
    # UnitType master every time. The master is editable, and stock already on the
    # shelf was counted, valued and labelled under the rule in force when it
    # arrived — re-deriving it later would silently restate the quantity of goods
    # nobody has touched. See services/unit_types.py.
    unit_type = Column(String, index=True)             # UnitType.code
    pieces_per_unit = Column(Float, default=1.0)       # individual items in one unit
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
    # From the stock master (Attributes Reference.xlsx, "Quanto Report"): the
    # columns Essa actually keeps against 13,851 stock rows are PRODUCT, BRAND,
    # COLOUR, PATTERN, STYLE, FIT, SLEEVE, TYPE, MATERIAL, SIZE. Everything but
    # these three was already here; without them a t-shirt could not record whose
    # label was on it, whether it was RNS or a straight cut, or whether it had
    # sleeves — and brand in particular is not decoration, it is identity: an
    # ESSA t-shirt and a YUVA t-shirt are different stock items.
    brand = Column(String, index=True)
    style = Column(String)             # RNS | Straight Cut | Umbrella | Casual | …
    sleeve = Column(String)            # Full | Half | Sleeveless | 3/4th
    # category master classification, mapped from the invoice description by
    # services/categorize.py (auto-applied only when the match is confident)
    category = Column(String, index=True)          # e.g. LADIES-T-SHIRT
    category_section = Column(String)              # OVERALL | KIDS | LADIES | MENS
    sale_price = Column(Float)
    # Discount off MRP, in percent — the selling lever ("20% off 1000 = 800").
    # Named for the SALE side because `LineItem.discount_pct` already means the
    # supplier's trade discount on what we PAID; the two must never be confused.
    # Replaced `margin_pct` (markup over cost), which nobody was filling in.
    sale_discount_pct = Column(Float)
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
    # The size cell exactly as the supplier printed it — often the whole size
    # mix in one string, "30:2, 32:4, 34:4, 36:2". Kept verbatim rather than
    # parsed into the column, because it is the document's own words and the
    # reading of it is offered, never imposed: services/size_split.py turns it
    # into breakdown rows for a human to accept.
    size = Column(String)
    # Retail, per piece. `mrp` is off the invoice — suppliers print it and it was
    # being dropped here, so an undivided line created a product with no MRP even
    # though the bill stated one. The other two are not on any bill: nobody prints
    # your shelf price for you. They are set on the invoice review screen
    # (MRP − sale discount % = sell price) and applied at post, so a product is
    # born priced instead of landing in Inventory with nothing on it.
    #
    # A breakdown row overrides all three per variant — a shop's price belongs to
    # "L / Red", not to the bundle the supplier billed — and inherits these when
    # it leaves them blank. See PurchaseLineSplit.
    mrp = Column(Float)
    sale_price = Column(Float)
    sale_discount_pct = Column(Float)                 # off MRP; see Product.sale_discount_pct
    is_new_product = Column(Boolean, default=False)   # matched vs newly created
    # category master classification chosen at GRN time. Set here, the product is
    # born mapped instead of landing "unmapped" for someone to fix in Inventory;
    # left blank, services/categorize.py still auto-maps from the description.
    category = Column(String)
    # What one of these IS — piece, pair, dozen. Chosen here, or left to the rule
    # that reads it off the description, and applied at post: the billed quantity
    # in `uom` is converted into this unit. `qty` / `uom` / `rate` above stay the
    # supplier's own figures, so the invoice keeps reconciling either way.
    unit_type = Column(String)

    purchase = relationship("Purchase", back_populates="lines")
    product = relationship("Product")
    splits = relationship("PurchaseLineSplit", back_populates="line",
                          cascade="all, delete-orphan", order_by="PurchaseLineSplit.id")
    shortages = relationship("GrnShortage", back_populates="line",
                             cascade="all, delete-orphan", order_by="GrnShortage.id")

    @property
    def split_qty(self):
        """Total quantity accounted for by the attribute breakdown (0 if none)."""
        return round(sum((s.qty or 0) for s in self.splits), 3)

    @property
    def is_split(self):
        return bool(self.splits)

    @property
    def received_qty(self):
        """What actually arrived — billed, less what was short, plus any excess.

        This, not `qty`, is the quantity that becomes stock. `qty` stays the
        supplier's figure so the invoice keeps reconciling against their document;
        the difference between the two is exactly the set of GrnShortage rows."""
        net = sum((s.signed_qty for s in self.shortages), 0.0)
        return round(float(self.qty or 0) + net, 3)


class ProductUnit(Base):
    """One physical piece, under a SKU that has many.

    Receiving 8 of ESSA-00008 makes ONE inventory record with a stock of 8 — the
    master, the valuation and the ledger all stay at SKU level, because that is
    what a weighted-average cost and a stock report are about. What this table adds
    is an *identity per piece*: ESSA-00008-001 … -008, each with its own QR, all
    pointing back at the same SKU.

    That is what lets a label be stuck on a garment rather than on a shelf edge. A
    scan of any child code resolves to the product (so every existing scan point
    keeps working), while the code itself says which of the eight it is — which is
    what a returns desk, a lost-piece query or a per-piece audit needs and a shared
    SKU code can never answer.

    Deliberately NOT stock rows: eight units under a stock of eight is the same
    eight garments, not sixteen. `status` tracks where a piece is; the quantity
    remains the ledger's answer.
    """
    __tablename__ = "product_units"
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    code = Column(String, unique=True, index=True)     # ESSA-00008-003
    seq = Column(Integer)                              # 3 — its number within the SKU
    # which receipt put this piece on the floor, so a unit can be traced to its GRN
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True, index=True)
    bundle_id = Column(Integer, ForeignKey("bundles.id"), nullable=True, index=True)
    status = Column(String, default="in_stock", index=True)   # in_stock | dispatched | sold | returned
    # printing is tracked so the screen can offer Print the first time and Reprint
    # afterwards — a label reprinted because it tore is worth telling apart from
    # one that was never printed
    print_count = Column(Integer, default=0)
    last_printed_at = Column(DateTime, nullable=True)
    last_printed_by = Column(String)
    created_at = Column(DateTime, default=now)

    product = relationship("Product", backref=backref(
        "units", cascade="all, delete-orphan", order_by="ProductUnit.seq"))


class Bundle(Base):
    """The carton a GRN line physically arrived as, and the code stuck on it.

    Deliberately **not** a Product and never a stock row. The 50 pieces inside are
    already counted as the items they became; counting the carton as well would
    double the warehouse. What a bundle owns is a *handling* identity — something
    to scan when putting it on a rack, finding it again, or opening it — plus the
    receipt it came from and where it is now.

    This is the first of the two labels an item can carry, and they answer
    different questions. The bundle label says "this box, from this GRN, holds 50
    women's t-shirts across four sizes" and is printed the moment the GRN posts,
    because the goods are on the floor and have to be put somewhere. The product
    label says "this garment is ESSA-00004, L, Red, MRP 899" and is printed later,
    when the box is opened and its contents tagged for sale. Printing item labels
    at GRN would mean tagging 50 loose garments that are about to sit in a carton
    for a fortnight; printing only item labels would leave the carton itself
    anonymous on the rack.

    Life: stored (labelled and put away) → opened → tagged (its items now carry
    their own labels, so the bundle stops being the unit anyone handles).
    """
    __tablename__ = "bundles"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, index=True)      # ESSA-B-00001, what its QR resolves to
    purchase_id = Column(Integer, ForeignKey("purchases.id"), index=True)
    line_id = Column(Integer, ForeignKey("purchase_lines.id"), index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    # copied off the line at post, not read through it: a bundle label is a
    # historical record of what was received, and must still read correctly if the
    # GRN is later unposted, corrected and posted again
    description = Column(String)
    hsn = Column(String)
    uom = Column(String)
    qty = Column(Float)                                  # pieces in the carton
    item_count = Column(Integer, default=0)              # distinct products inside
    grn_no = Column(String)
    invoice_number = Column(String)
    location = Column(String, index=True)                # where it was put away
    status = Column(String, default="stored", index=True)  # stored | opened | tagged
    received_at = Column(DateTime, default=now)
    located_at = Column(DateTime, nullable=True)
    located_by = Column(String)
    opened_at = Column(DateTime, nullable=True)
    tagged_at = Column(DateTime, nullable=True)
    tagged_by = Column(String)
    created_at = Column(DateTime, default=now)

    purchase = relationship("Purchase")
    line = relationship("PurchaseLine", backref=backref("bundle", uselist=False))
    supplier = relationship("Supplier")

    @property
    def products(self):
        """What is inside — the items this carton's line produced."""
        if not self.line:
            return []
        if self.line.is_split:
            return [s.product for s in self.line.splits if s.product]
        return [self.line.product] if self.line.product else []


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
    # the rest of the stock master's attribute set — see Product.brand
    brand = Column(String)
    style = Column(String)
    sleeve = Column(String)
    # not part of identity — a classification the product carries (falls back to
    # the line's category, then to auto-mapping from the description)
    category = Column(String)
    # nor is this: a variant is the same KIND of thing as its line, so it inherits
    # the line's unit type unless someone says otherwise (a bundle of assorted
    # goods where one row is sold in pairs and another by the piece)
    unit_type = Column(String)
    qty = Column(Float, default=0.0)
    rate = Column(Float)                 # unit cost (defaults to the line's rate)
    mrp = Column(Float)                  # retail prices are per variant, not per bundle
    sale_price = Column(Float)
    sale_discount_pct = Column(Float)    # off MRP; see Product.sale_discount_pct
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
        # brand first: on a rack it is what the eye reaches for before the size
        bits = [self.brand, self.size, self.color, self.material, self.pattern,
                self.fit, self.style, self.sleeve, self.product_type, self.design_no]
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


class GrnShortage(Base):
    """What the invoice billed and the cartons did not deliver.

    A supplier bills 50 pieces; 40 come out of the boxes. Until now the receiving
    screen had only two answers, and both were lies: invent ten pieces so the
    breakdown balances (stock gains units that do not exist), or leave the GRN
    unpostable forever. This is the third and true answer — *ten were short* — and
    it is recorded here, on the floor, at the moment the boxes are opened, by the
    only person who can know it.

    That timing is the whole point, and it is why this belongs to the Receive flow
    and not to Inventory. Once a GRN posts, the difference has already been
    absorbed: the stock figure is 40, the invoice says 50, and nothing on the
    system remembers why. Recorded *before* posting, the gap is a document — with
    a quantity, a reason and a name against it — that the debit note is later
    built from without anyone re-counting anything.

    `kind` says what happened to the units, and the only thing that separates the
    three is which side of the count they land on:

      * **short** — never arrived. Never becomes stock.
      * **damaged** — arrived, but rejected at the dock and set aside for the
        supplier. Also never becomes stock: taking it in and writing it off again
        would put goods we refused into the valuation for as long as the paperwork
        takes. Damage found *later*, in stock we already accepted, is a purchase
        return — that is the existing module, and it reverses stock because there
        is stock to reverse.
      * **excess** — more arrived than was billed. The mirror case, and it *does*
        become stock: the goods are on the floor whatever the invoice says.

    So `PurchaseLine.received_qty` = billed − short − damaged + excess, and it is
    that figure the attribute breakdown has to add up to and that posting turns
    into stock. The invoice line is left alone, exactly as it is for a breakdown:
    it stays the record of what the supplier billed, so the payables side keeps
    reconciling against their own document.

    Money is deliberately absent from this table. A shortage is a *fact about a
    count*; what it is worth is a fact about the GRN, derived from the line rate
    whenever it is asked for (services/shortages.unit_cost). Freezing a rate here
    would be a second place for the same number to live, and the debit note —
    which is where it becomes financial — re-derives from the GRN anyway.

    `waived` is the one decision a human makes afterwards: the supplier is sending
    the rest, or it is not worth the paperwork. A waived shortage stays on the
    record and stops being offered for claim; a claimed one is answered by the
    posted debit note that references it."""
    __tablename__ = "grn_shortages"
    id = Column(Integer, primary_key=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), index=True)
    line_id = Column(Integer, ForeignKey("purchase_lines.id"), index=True)
    kind = Column(String, default="short", index=True)   # short | damaged | excess
    qty = Column(Float, default=0.0)                     # always positive
    # Which variant it was, when the person opening the carton could tell — free
    # text, not a link to a breakdown row. The breakdown is cleared and rebuilt
    # every time it is edited, so a foreign key into it would dangle; and the
    # arithmetic is at line level regardless, because the supplier billed a bundle
    # and never said what was in it.
    variant = Column(String)
    reason = Column(String)                              # torn / wet / not in box / …
    note = Column(Text)
    recorded_by = Column(String)
    recorded_at = Column(DateTime, default=now)
    # accepted rather than claimed — the supplier is re-sending, or it is too small
    # to raise a debit note for. Kept on the record either way.
    waived = Column(Boolean, default=False, index=True)
    waived_reason = Column(String)
    waived_by = Column(String)
    waived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    purchase = relationship("Purchase")
    line = relationship("PurchaseLine", back_populates="shortages")

    #: kinds that reduce what was received — the ones a supplier can be debited for
    CLAIMABLE_KINDS = ("short", "damaged")

    @property
    def signed_qty(self):
        """This row's effect on the received count: negative for goods that never
        made it in, positive for goods that turned up over and above the bill."""
        q = float(self.qty or 0)
        return q if self.kind == "excess" else -q

    @property
    def claimable(self):
        """Whether this row can be put on a debit note at all. Excess is the
        mirror case — the supplier under-billed us, which is their credit to raise
        and not ours to debit."""
        return self.kind in self.CLAIMABLE_KINDS


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
#  Stock Outward / Stock Inward  (warehouse → destination, and its acceptance)
# ============================================================================
class StockOutward(Base):
    """A dispatch / inter-location transfer out of the warehouse, and the record
    the destination accepts it on.

    One row, two screens, because they are two ends of ONE movement: Stock
    Outward is the warehouse packing and sending it (posting decrements stock via
    a negative StockMovement, kind='outward'), Stock Inward is the destination
    counting what turned up and accepting it. Splitting them into two documents
    would mean reconciling the pair; keeping the sent qty and the accepted qty on
    the same line makes a short delivery visible by subtraction.

    draft → posted (dispatched, stock out) → received (counted and accepted)."""
    __tablename__ = "stock_outwards"
    id = Column(Integer, primary_key=True)
    code = Column(String)                       # package / order code
    date = Column(String)
    from_company = Column(String, default="Essa Garments Private Limited")
    from_location = Column(String, default="WAREHOUSE")
    to_destination = Column(String)             # store / customer
    packed_by = Column(String)
    received_by = Column(String)                # who accepted it at the far end
    received_date = Column(String)              # the date they wrote on the note
    received_at = Column(DateTime, nullable=True)   # when it was keyed in
    status = Column(String, default="draft", index=True)   # draft | posted | received
    posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    lines = relationship("StockOutwardLine", back_populates="outward",
                         cascade="all, delete-orphan", order_by="StockOutwardLine.id")

    @property
    def total_qty(self):
        return sum((l.qty or 0) for l in self.lines)

    @property
    def total_accepted(self):
        """What the destination took in. Unreceived lines count as nothing
        accepted YET — reading them as fully accepted would show a dispatch
        nobody has looked at as if it had been checked and agreed."""
        if self.status != "received":
            return 0.0
        return sum((l.accepted_qty if l.accepted_qty is not None else (l.qty or 0))
                   for l in self.lines)

    @property
    def shortfall(self):
        """Sent minus accepted — the transfer discrepancy, once received."""
        if self.status != "received":
            return 0.0
        return round(self.total_qty - self.total_accepted, 3)


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

    @property
    def short_qty(self):
        """How many of this line failed to arrive (0 until it is received)."""
        if self.accepted_qty is None:
            return 0.0
        return round(float(self.qty or 0) - float(self.accepted_qty or 0), 3)


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
    """One product going back to the supplier, valued at what we PAID for it.

    `rate` is the received price — the GRN cost of this exact item: the rate on
    the invoice line, or, when the billed bundle was broken down, the rate of the
    variant being returned. It is never the sale price or the MRP: a debit note
    settles a supplier account, so it can only carry what that supplier charged
    us. services/returns.grn_cost() is the single place that decides it, and it is
    re-derived from the GRN at post time so a stale draft cannot slip through.

    `purchase_line_id` / `split_id` record WHICH received row this line is
    returning, which is what makes that re-derivation exact — and what lets the
    screen show the batch and how many of it were bought in the first place.

    `shortage_id` marks the other kind of line entirely: goods the supplier billed
    and never delivered (see GrnShortage). Financially it settles the same way —
    it reduces what we owe on that invoice at the same GRN rate — but it must NOT
    move stock, because the units it debits never entered stock in the first
    place. `post()` reads this column for exactly that reason."""
    __tablename__ = "purchase_return_lines"
    id = Column(Integer, primary_key=True)
    return_id = Column(Integer, ForeignKey("purchase_returns.id"))
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    purchase_line_id = Column(Integer, ForeignKey("purchase_lines.id"), nullable=True)
    split_id = Column(Integer, ForeignKey("purchase_line_splits.id"), nullable=True)
    # set when this line claims a receiving shortage instead of returning goods
    shortage_id = Column(Integer, ForeignKey("grn_shortages.id"), nullable=True)
    barcode = Column(String)
    description = Column(String)
    hsn = Column(String)
    uom = Column(String)
    qty = Column(Float)                         # qty returned
    rate = Column(Float)                        # GRN / received cost per unit
    amount = Column(Float)

    ret = relationship("PurchaseReturn", back_populates="lines")
    product = relationship("Product")
    purchase_line = relationship("PurchaseLine")
    split = relationship("PurchaseLineSplit")
    shortage = relationship("GrnShortage")

    @property
    def is_shortage_claim(self):
        """True when this line debits goods that were never received, so posting
        it must value the claim without touching the stock ledger."""
        return self.shortage_id is not None


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


class CategoryAlias(Base):
    """A supplier's wording, and the category a human said it means.

    The rules in services/categorize.py cover the wordings we thought of; suppliers
    keep inventing more, and no list written up front ever finishes. So the moment
    someone sets a category on a GRN line by hand, that correction is kept and the
    same wording maps itself next time — the engine gets better with use instead of
    waiting on a developer to add another synonym.

    The key is the CANONICAL form of the description (gender words replaced by the
    master's own vocabulary, sizes and noise stripped), not the raw text, so one
    correction covers every spelling that canonicalises the same way: teach it
    "Ladies Tee" and "Women's Tee" is taught too.

    `source` records who said so, and `hits` how often it has been used — together
    they are the evidence for reviewing a mapping that turns out to be wrong."""
    __tablename__ = "category_aliases"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True)   # canonical description text
    sample = Column(String)                         # a raw description that produced it
    category = Column(String, index=True)           # the master name it maps to
    section = Column(String)
    source = Column(String, default="human")        # human | import
    hits = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class UnitType(Base):
    """A unit of handling, and how many pieces are in one of it.

    Suppliers bill in dozens; Essa stocks, labels and sells in whatever the item
    actually is. A handkerchief is a piece, a pillow cover is a pair, a towel is a
    piece — and a dozen of each is a different number of things to put a QR on.
    One table answers both halves of that, because they are the same question
    asked twice:

      * **the billed unit** — DOZ on the invoice means `pieces` = 12,
      * **the stock unit** — PAIR for a pillow cover means `pieces` = 2.

    So 1 DOZ of pillow covers is 12 pieces is 6 pairs, and 6 is the number of
    labels, the number of stock units and the number the shop sells. The
    arithmetic is nothing more than pieces-in ÷ pieces-per-stock-unit, which is
    exactly why both ends live in one master: add "HALF DOZEN = 6" once and it
    works as a purchase unit and as a selling unit without a second thought.

    `aliases` is what makes it survive real invoices, where the same unit is
    printed DOZ, DZN, DZ, Doz. and DOZEN by five suppliers. `pieces` is a float
    only so an odd trade unit (a "set" of 1.5?) is expressible; every seeded value
    is a whole number.

    Editing `pieces` here does NOT re-value existing stock: a product freezes its
    own factor at creation (Product.pieces_per_unit), because the goods on the
    shelf were counted under the rule that was in force when they arrived.
    """
    __tablename__ = "unit_types"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, index=True)    # PCS | PAIR | DOZEN | SET | BOX
    name = Column(String)                             # "Pair"
    pieces = Column(Float, default=1.0)               # individual items in one of these
    aliases = Column(JSON, default=list)              # ["DOZ", "DZN", "DZ"]
    countable = Column(Boolean, default=True)         # False for MTR/KG — no pieces to tag
    is_seed = Column(Boolean, default=False)          # shipped with the system
    sort = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)

    @property
    def pieces_per(self):
        p = float(self.pieces or 0)
        return p if p > 0 else 1.0


class UnitRule(Base):
    """Which unit type a product is, decided from what it is called.

    "Make Unit Type configurable per product" cannot mean somebody choosing it on
    every GRN line forever — the same twenty products come in every week, and a
    setting that must be re-entered is a setting that will be got wrong. So the
    rule is stated once against the wording ("pillow cover" → PAIR) and every line
    that says pillow cover is born a pair.

    Matched on a lower-cased substring of the description (or against a category
    master name when `scope` is "category"), longest pattern first, so
    "pillow cover" beats a broader "cover". A choice made by hand on a GRN line is
    remembered here the same way category corrections are — see
    services/unit_types.learn — which is what stops the list going stale.
    """
    __tablename__ = "unit_rules"
    id = Column(Integer, primary_key=True)
    pattern = Column(String, index=True)              # "pillow cover" (lower case)
    scope = Column(String, default="keyword")         # keyword | category
    unit_type = Column(String, index=True)            # UnitType.code
    source = Column(String, default="seed")           # seed | human
    hits = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class MasterRecord(Base):
    """One row of any master, stored against its definition.

    Seventeen master screens would otherwise be seventeen tables that differ only
    in their column names, and a field added to one of them would mean a
    migration before anyone could type it. The shape of each master lives in
    services/master_defs.py; the values live here, in `data`.

    What is NOT in `data` is deliberate: `code` and `name` are lifted out into
    real columns because they are the two things every other master looks a
    record up by — a dropdown sourced from `master:tax` needs to list and search
    them without opening a JSON blob per row. Everything else is the definition's
    business.

    This backs the masters that had no home. Supplier, Agent and Transport keep
    their own tables (they are created automatically from documents and are wired
    into the LR and invoice flows); their extra ERP fields are stored here
    against the same `code`, so neither copy has to know about the other.
    """
    __tablename__ = "master_records"
    id = Column(Integer, primary_key=True)
    master = Column(String, index=True)          # master_defs key: "tax", "brand", …
    code = Column(String, index=True)            # the master's own code, when it has one
    name = Column(String, index=True)            # what a dropdown shows
    data = Column(JSON, default=dict)            # every other field, by key
    grids = Column(JSON, default=dict)           # {grid_key: [row, …]} for child tables
    matrix = Column(JSON, default=dict)          # {row: {column: bool}} — Product's switchboard
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


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
    """LR Entry register — one row per received consignment.

    Two ways in, and `entry_source` says which:
      * "import" — OCR/vision reads a photographed LR book page or the TRANSPORT
        Excel, and a batch of rows lands at once. This is the fast path.
      * "manual" — one consignment keyed in on the LR Entry form, for when the
        goods arrive with no register page to photograph.

    The columns below are the union of what the register page carries and what
    the Transport Entry form captures, so either route fills the same record."""
    __tablename__ = "lr_entries"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    entry_source = Column(String, default="import")   # import | manual
    # Our own running number for the entry (LRE-00001). Distinct from `lr_no`,
    # which is the TRANSPORTER's docket number printed on the consignment.
    lr_entry_no = Column(String, index=True)
    lr_entry_date = Column(String)       # when the entry was booked in the office
    lr_mode = Column(String)             # Hand Delivery | Transport | Courier | …
    recv_date = Column(String)
    transport = Column(String)
    bundle = Column(Float)
    boxes = Column(Float)
    lr_no = Column(String)
    lr_date = Column(String)
    supplier_name = Column(String)
    agent = Column(String)
    agent_commission = Column(Float)     # % or flat, as the agent is engaged
    inv_no = Column(String)
    inv_date = Column(String)
    qty = Column(Float)                  # No Of Pieces
    amount = Column(Float)               # Goods Value
    auto_transfer_location = Column(String)   # onward branch, or NONE to keep here
    purchase_manager = Column(String)
    stock_holding_days = Column(Float)
    additional_margin = Column(Float)
    # Freight settles as mode + amount; the flag mirrors the form's checkbox and
    # says the charge APPLIES at all, which a zero amount cannot express (nothing
    # yet quoted vs quoted at nil).
    paid_topay = Column(String)          # TOPAY | NO | PAID
    freight_applicable = Column(Boolean, default=False)
    freight_amount = Column(Float)       # the FREIGHT line alone
    # What the transporter is actually owed — the "G. TOTAL" printed at the foot
    # of an LR copy. Freight is only the first line of that bill: a Golden
    # Transport LR reads Freight 425, H.C. 10, S.T. Charge 20, G. TOTAL 455, and
    # for a long time this system recorded the 425 and dropped the 30. That made
    # every transport payment report short by whatever the sundries came to, with
    # nothing on screen to say a number was missing.
    #
    # Kept as its own column rather than derived, because the printed total is
    # the document: if the charges do not add up to it, the LR is still what the
    # lorry will be paid against. `freight_charges` holds the named lines that
    # made it up — {"H.C.": 10, "S.T. Charge": 20} — as a map rather than a column
    # apiece, because every transporter prints a different set of them and eight
    # mostly-empty columns is exactly the trap this table was cleaned of once
    # before.
    freight_total = Column(Float)
    freight_charges = Column(JSON, default=dict)
    # Columns dropped after they proved to be dead weight in this warehouse — no
    # consignment ever carried one: company, bundle_rack, section, remark,
    # due_date, pay_mode, package_slip_no, slip_date, actual_weight,
    # charged_weight, from_city, receiving_city, loading_charge/
    # loading_applicable, cash_cheque, and the earlier `place` and `purchaser`.
    # `_migrate` drops them; older databases may still hold the physical column,
    # but nothing maps or reads it.
    item = Column(String)
    # Who physically RECEIVED the consignment. Not typed on the desktop and not
    # read off the register page: only the warehouse knows who took the packages,
    # so they record it from the phone app (POST /api/lr/{id}/receive) as the
    # goods land. The desktop shows it read-only.
    received_by = Column(String)
    # Freight settlement is recorded as mode + amount only (paid_topay,
    # freight_amount). `paid_by`, `cash_receiver_mobile` and `cash_cheque` columns
    # may still exist in older databases; they are no longer mapped or read.
    # --- what the page was written in, when it wasn't English ---
    # A Tamil register is read in Tamil and stored in English, because that is
    # what every search box, master and report downstream is in. The reading is
    # not allowed to be the only copy though: `original_values` is {field: the
    # text on the page} for each value that changed, so the row can always show
    # what the clerk actually wrote — which is the record, and the thing anyone
    # disputing a reading will ask for. Numbers never appear here; they are never
    # translated. See services/translate.py.
    source_language = Column(String)          # "Tamil" — None when it was English
    original_values = Column(JSON, default=dict)
    # --- invoice linkage (cross-fill): set when a matching invoice is uploaded ---
    invoice_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    matched = Column(Boolean, default=False, index=True)   # an invoice has been linked
    # fields where the linked invoice DISAGREES with the register value we kept:
    # [{"field","register","invoice"}] — register value is retained, conflict flagged
    mismatches = Column(JSON, default=list)
    created_at = Column(DateTime, default=now)

    attachments = relationship("LRAttachment", back_populates="entry",
                               cascade="all, delete-orphan",
                               order_by="LRAttachment.id")


class LRAttachment(Base):
    """A file pinned to one LR entry — the LR copy, a weighment slip, a photo of
    damaged bundles. Separate from `Document` on purpose: a Document is something
    the extraction engine reads and learns a supplier format from, whereas these
    are evidence kept against the consignment and never extracted."""
    __tablename__ = "lr_attachments"
    id = Column(Integer, primary_key=True)
    lr_id = Column(Integer, ForeignKey("lr_entries.id"), index=True)
    doc_type = Column(String)            # from the `attachment_type` master
    filename = Column(String)
    stored_path = Column(String)
    mime = Column(String)
    created_at = Column(DateTime, default=now)

    entry = relationship("LREntry", back_populates="attachments")


class LabelTemplate(Base):
    """The DESIGN of a label — where each field sits on the sticker, never the
    values that will be printed in those places.

    This is the whole reason label design is its own module rather than a corner
    of Inventory. A product's data belongs to the GRN that created it; how that
    data is laid out on a 50×35mm sticker is a decision made once by a manager
    and then used by the warehouse for months. Storing a product's name IN a
    template would mean a template per product; storing the *reference*
    ("product_name goes at 4mm/9mm, 8pt, bold") means one template prints the
    whole warehouse, and a product renamed on its GRN prints correctly the next
    day without anyone reopening the designer.

    `elements` is the design: a list of

        {"id": "e3", "field": "size", "x": 4, "y": 18, "w": 22, "h": 5,
         "size": 8, "bold": true, "align": "left", "prefix": "Size: ",
         "locked": false, "visible": true, "z": 3}

    with x/y/w/h in MILLIMETRES from the label's top-left corner, because that is
    what the printer works in and what the person holding a ruler against the
    output can check. Field keys are the catalogue in services/label_designer.py;
    an element naming a field that catalogue no longer has renders as blank
    rather than breaking the sheet, so a template outlives a renamed attribute.

    `locked` is per element and is enforced in the UI only — it stops a warehouse
    user nudging the QR off the sticker by accident, which is a usability guard,
    not a permission. Deleting or moving a locked element is still possible after
    unlocking it, which is the point.

    One template at a time may be `is_default`; that is the one the printing
    screen opens on. Setting it is a router operation because it has to clear the
    previous default in the same transaction — see routers/labels.set_default.
    """
    __tablename__ = "label_templates"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    description = Column(String)
    # the sticker itself. Millimetres, and floats — 50 × 35 is common, so is 38.1.
    width_mm = Column(Float, default=50.0)
    height_mm = Column(Float, default=35.0)
    padding_mm = Column(Float, default=2.0)
    border = Column(Boolean, default=True)
    #: what this template prints one of: "product" (one label per SKU) or
    #: "unit" (one label per physical piece, carrying that piece's own code)
    target = Column(String, default="product", index=True)
    font = Column(String, default="Arial, Helvetica, sans-serif")
    elements = Column(JSON, default=list)
    is_default = Column(Boolean, default=False, index=True)
    active = Column(Boolean, default=True, index=True)
    created_by = Column(String)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class MasterOption(Base):
    """One entry in a small dropdown list, keyed by `kind`.

    The big masters earn their own table (Supplier, Agent, Transport, Category)
    because they carry structure — a GSTIN, a phone, a section. These do not: a
    rack, a warehouse section, a pay mode or a city is a name and nothing else,
    and there are a dozen such lists on the LR Entry form alone. A table apiece
    would be a dozen near-identical models and routers; one keyed table is the
    same data with one place to maintain.

    Lists that are fixed vocabulary (lr_mode, attachment_type, …) are seeded
    once; the free ones (company, rack, section, …) fill themselves from whatever
    is entered, the same way agents and transporters already do."""
    __tablename__ = "master_options"
    id = Column(Integer, primary_key=True)
    kind = Column(String, index=True)    # see masters.OPTION_KINDS
    value = Column(String, index=True)
    sort = Column(Integer, default=0)    # seeded lists keep their given order
    created_at = Column(DateTime, default=now)


# ============================================================================
#  Notifications
# ============================================================================
class NotificationState(Base):
    """What has been READ of a standing queue — not a log of events.

    Nearly everything worth telling someone about in this system is a condition
    rather than an occurrence: "four GRNs are in draft", "eleven lines have been
    dead for six months". Written as an event log it would file the same four
    drafts again on every check and the list would be unreadable inside a day.

    So the notices themselves are derived live (services/notifications.py) and
    only the acknowledgement is stored: which key was read, and at what COUNT. A
    queue read at four drafts goes quiet, and speaks up again at five — because
    what someone acknowledged was four, and a fifth is news. It never re-alerts
    for a queue that shrank; that is the direction nobody needs chasing about.

    `muted` is the stronger form, for a queue a particular warehouse simply does
    not work that way — it stays out of the list until it is unmuted, whatever
    the count does."""
    __tablename__ = "notification_states"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True)   # see notifications.RULES
    first_seen = Column(DateTime, default=now)      # since when this has been open
    read_level = Column(Float, default=0.0)         # the count acknowledged
    read_at = Column(DateTime, nullable=True)
    read_by = Column(String)
    muted = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=now, onupdate=now)


class NotificationRecipient(Base):
    """Who should be told, and on what number.

    Delivery today is in-app: the bell on the desktop and the Notifications tab
    on the warehouse phone. The number is held against the person so a channel
    that sends to a phone can be switched on later without anybody re-collecting
    the roster — and so the list answers "who is meant to be watching this",
    which is a question the warehouse has an answer to and the software did not."""
    __tablename__ = "notification_recipients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    mobile = Column(String)                 # as dialled, +91… or ten digits
    role = Column(String)                   # what they watch — free text
    #: which levels reach them: ["critical", "warn", "info"]
    levels = Column(JSON, default=list)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)


# ============================================================================
#  Dead stock clearance  (Inventory → Dead Stock & Clearance)
# ============================================================================
class ClearanceCampaign(Base):
    """A run at clearing slow-moving stock: which lines, at what discount, over
    which dates.

    It is a PLAN over stock that already exists, not stock of its own. There is
    no quantity held here, no ledger and no movement — a line points at the
    product it came from, and when that product sells at the till the campaign's
    realisation moves because the sale moved. Anything else would be a second
    stock record to keep in step with the first, and the first is the one the
    business runs on (see services/dead_stock.campaign_lines).

    The dates are what makes the reading possible: sold-and-realised are the
    till's own figures for these products BETWEEN starts_on and ends_on, so two
    campaigns over the same SKU in different months each own their own result."""
    __tablename__ = "clearance_campaigns"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    status = Column(String, default="draft", index=True)   # draft | active | closed
    starts_on = Column(String)                             # ISO YYYY-MM-DD
    ends_on = Column(String)
    note = Column(Text)
    created_by = Column(String)
    created_at = Column(DateTime, default=now)
    closed_at = Column(DateTime, nullable=True)

    lines = relationship("ClearanceLine", back_populates="campaign",
                         cascade="all, delete-orphan", order_by="ClearanceLine.id")


class ClearanceLine(Base):
    """One product in a campaign, at the price the campaign was approved on.

    The age, band, discount and price are frozen copies of what the register
    showed when the line was added. Read live instead, every line would re-price
    itself quietly as the stock went on ageing, and the campaign could no longer
    say what it had promised — or be judged against it afterwards."""
    __tablename__ = "clearance_lines"
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("clearance_campaigns.id"), index=True)
    product_id = Column(Integer, ForeignKey("products.id"), index=True)
    qty = Column(Float)                    # what was put into the campaign
    days_idle = Column(Integer)
    bucket = Column(String)                # the age band, as labelled at the time
    discount_pct = Column(Float)
    cost_price = Column(Float)             # weighted-average cost, for the margin
    mrp = Column(Float)
    clearance_price = Column(Float)
    expected_realisation = Column(Float)
    action = Column(String, default="Review")   # see dead_stock.ACTIONS
    note = Column(String)
    added_at = Column(DateTime, default=now)

    campaign = relationship("ClearanceCampaign", back_populates="lines")
    product = relationship("Product")


class User(Base):
    """A person who can sign in, on the desktop app or the phone.

    Three roles, ranked — see services/users.ROLE_RANK. `user` runs the floor
    (receive, count, scan, print), `admin` also owns the setup the floor works
    against (masters, suppliers, label design) and the money screens, and
    `superadmin` additionally owns this table and the server's own settings.

    The password is never stored. `password_hash` holds a PBKDF2 digest and its
    salt; `token_seed` is folded into every token this user is issued, so
    changing a password (or a super admin resetting one) invalidates the
    sessions already out there instead of leaving them valid until they expire.
    """
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")
    full_name = Column(String)
    active = Column(Boolean, default=True)
    token_seed = Column(String)
    # Kept so a super admin can see who has never signed in — a created-and-
    # forgotten account is the one most worth deactivating.
    last_login_at = Column(DateTime)
    created_at = Column(DateTime, default=now)
    created_by = Column(String)
