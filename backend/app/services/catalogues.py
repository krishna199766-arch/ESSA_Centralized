"""Catalogues — the goods a warehouse trades in, and the vocabulary for them.

WHAT PROBLEM THIS SOLVES. Essa receives garments and Taqua receives silks. The
PROCESS over them is identical — LR entry, invoice entry, GRN, inventory, labels,
outward, inward, POS — and none of that needed changing. What differs is the
NOUNS: the categories a receiving clerk picks from, the attributes an item
carries, and the values those attributes offer.

Before this there was one of each, global, and every one of the 686 categories
was a garment (ESSA KIDS-TRUNK, ACC-PILLOW COVER, LADIES-NIGHT SUIT). A silk
warehouse pointed at that list would be asked to file a Kanchipuram saree as a
kids' trunk, and would have nowhere at all to record its weave or its zari.

WHY THE BUSINESS LINE AND NOT THE BUILDING. The wireframe has four warehouses and
three of them stock garments. Tagging masters with a warehouse would put the same
686 rows in the database three times, and they would drift apart the first time
one of them was edited. A catalogue is shared by as many warehouses as trade in
it, so Essa, Palakkad and Madurai are one list maintained once.

WHAT IS SHARED. A master row with no catalogue belongs to everyone. That is the
right answer for payment modes, transporters, racks and LR modes — a lorry is a
lorry whatever is in it — and it is what every row written before catalogues
existed is left as, EXCEPT the categories, which are moved to the garment
catalogue because that is demonstrably what they are.

WHERE THE ATTRIBUTES GO. Ten attributes already have a column on Product, because
those are what the garment stock master keeps. A catalogue attribute that names
one of them writes to the real column, so matching, size splits, labels and every
screen that predates this keep working untouched. One that does not — a weave, a
pallu — lives in `Product.attrs`. That is what makes adding a business line a
data change rather than a migration.
"""
import json
import os

from sqlalchemy.orm import Session

from .. import models
from ..config import DATA_DIR

#: The line every pre-existing row belongs to. Named for the goods rather than
#: the company, because the company is not what distinguishes it — Essa may well
#: open a silk warehouse, and it would use the silk catalogue.
DEFAULT_CODE = "GARMENTS"

#: The attributes Product has a real column for. A catalogue attribute whose key
#: is one of these writes THERE, not into the JSON bag — see the module note.
COLUMN_ATTRS = ("color", "size", "pattern", "fit", "product_type", "material",
                "design_no", "brand", "style", "sleeve")

#: Human labels for those, so a catalogue built through the app does not have to
#: invent a caption for an attribute the app already names.
COLUMN_LABELS = {
    "color": "Colour", "size": "Size", "pattern": "Pattern", "fit": "Fit",
    "product_type": "Type", "material": "Material", "design_no": "Design No",
    "brand": "Brand", "style": "Style", "sleeve": "Sleeve",
}

#: What the garment catalogue captures, in the order the phone form shows it.
#: This is the set the app has always had; it is written down here so the garment
#: catalogue is defined the same way a new one would be, rather than being a
#: special case the code knows about.
GARMENT_ATTRS = [
    ("brand", True), ("product_type", True), ("color", True), ("size", True),
    ("material", True), ("pattern", True), ("fit", True), ("style", True),
    ("sleeve", True), ("design_no", True),
]

_ATTR_FILE = os.path.join(DATA_DIR, "product_attributes.json")


# ---------------------------------------------------------------------------
#  which catalogue
# ---------------------------------------------------------------------------
def default_catalogue(db: Session):
    """The catalogue a warehouse falls back to when nothing has been said.

    Every install that predates this module trades in exactly one line — that is
    what a single global category master MEANS — so resolving a blank to it is
    describing what is already true.
    """
    c = (db.query(models.Catalogue)
           .filter(models.Catalogue.is_default.is_(True)).first())
    if c:
        return c
    return db.query(models.Catalogue).order_by(models.Catalogue.id).first()


def resolve_id(db: Session, catalogue_id=None):
    """A catalogue id that certainly exists."""
    if catalogue_id:
        c = db.get(models.Catalogue, int(catalogue_id))
        if c:
            return c.id
    c = default_catalogue(db)
    return c.id if c else None


def for_warehouse(db: Session, warehouse_id=None):
    """The catalogue a warehouse trades in. Never None once seeding has run."""
    if warehouse_id:
        wh = db.get(models.Warehouse, int(warehouse_id))
        if wh and wh.catalogue_id:
            c = db.get(models.Catalogue, wh.catalogue_id)
            if c:
                return c
    return default_catalogue(db)


def for_product(db: Session, product):
    """The catalogue an item belongs to, for screens that draw its attributes."""
    if product is not None and getattr(product, "catalogue_id", None):
        c = db.get(models.Catalogue, product.catalogue_id)
        if c:
            return c
    return default_catalogue(db)


# ---------------------------------------------------------------------------
#  reads
# ---------------------------------------------------------------------------
def out(c: models.Catalogue, db: Session = None) -> dict:
    d = {"id": c.id, "code": c.code, "name": c.name,
         "description": c.description, "active": bool(c.active),
         "is_default": bool(c.is_default)}
    if db is not None:
        d["warehouse_count"] = db.query(models.Warehouse).filter(
            models.Warehouse.catalogue_id == c.id).count()
        d["category_count"] = db.query(models.Category).filter(
            models.Category.catalogue_id == c.id).count()
        d["attribute_count"] = db.query(models.CatalogueAttribute).filter(
            models.CatalogueAttribute.catalogue_id == c.id,
            models.CatalogueAttribute.active.is_(True)).count()
        d["product_count"] = db.query(models.Product).filter(
            models.Product.catalogue_id == c.id).count()
    return d


def listing(db: Session) -> list:
    return [out(c, db) for c in
            db.query(models.Catalogue).order_by(models.Catalogue.name).all()]


def attributes(db: Session, catalogue_id) -> list:
    """What this catalogue records against an item, in display order.

    Empty is a legitimate answer, and it is what a catalogue created through the
    app starts as. The screens read this list to decide what to draw, so an empty
    one draws no attribute fields at all rather than ten garment ones.
    """
    rows = (db.query(models.CatalogueAttribute)
              .filter(models.CatalogueAttribute.catalogue_id == catalogue_id,
                      models.CatalogueAttribute.active.is_(True))
              .order_by(models.CatalogueAttribute.sort,
                        models.CatalogueAttribute.id).all())
    return [{"key": a.key,
             "label": a.label or COLUMN_LABELS.get(a.key) or a.key.replace("_", " ").title(),
             "column": a.column,
             # An attribute with no column is stored in Product.attrs. Said
             # explicitly so a screen never has to guess where to read a value.
             "stored": "column" if a.column else "attrs",
             "identity": bool(a.identity), "sort": a.sort or 0}
            for a in rows]


def identity_attrs(db: Session, catalogue_id) -> list:
    """The attribute keys that make two items DIFFERENT items in this catalogue.

    Two sarees differing only in weave are two stock items; two differing only in
    a free-text note are one. Getting this wrong merges goods that should be
    separate — behind a single weighted-average cost — so it is recorded per
    attribute rather than assumed.
    """
    return [a["key"] for a in attributes(db, catalogue_id) if a["identity"]]


def options(db: Session, catalogue_id) -> dict:
    """{attr: [values]} — the dropdowns this catalogue offers."""
    rows = (db.query(models.AttributeOption)
              .filter(models.AttributeOption.catalogue_id == catalogue_id)
              .order_by(models.AttributeOption.attr, models.AttributeOption.sort,
                        models.AttributeOption.value).all())
    out_ = {}
    for r in rows:
        out_.setdefault(r.attr, []).append(r.value)
    # Every declared attribute appears, even with nothing in it — an empty list
    # is "type the first one", a missing key looks like the attribute is gone.
    for a in attributes(db, catalogue_id):
        out_.setdefault(a["key"], [])
    return out_


def categories(db: Session, catalogue_id, include_shared=True) -> list:
    """This catalogue's categories, plus the ones nobody claimed."""
    q = db.query(models.Category)
    if include_shared:
        q = q.filter((models.Category.catalogue_id == catalogue_id)
                     | (models.Category.catalogue_id.is_(None)))
    else:
        q = q.filter(models.Category.catalogue_id == catalogue_id)
    return q.order_by(models.Category.name).all()


def add_option(db: Session, catalogue_id, attr, value, sort=None):
    """Remember a value for one attribute of one catalogue. Idempotent."""
    value = (value or "").strip()
    attr = (attr or "").strip()
    if not value or not attr:
        return None
    have = (db.query(models.AttributeOption)
              .filter(models.AttributeOption.catalogue_id == catalogue_id,
                      models.AttributeOption.attr == attr).all())
    # Case-insensitive: "Regular Fit" and "REGULAR FIT" are one fit, and offering
    # both is how a list becomes three spellings of the same thing.
    for o in have:
        if (o.value or "").strip().lower() == value.lower():
            return o
    row = models.AttributeOption(catalogue_id=catalogue_id, attr=attr, value=value,
                                 sort=len(have) if sort is None else sort)
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
#  reading and writing an item's attributes
# ---------------------------------------------------------------------------
def read_attrs(db: Session, product) -> dict:
    """{key: value} for everything this product's catalogue records about it.

    One dict whichever side of Product a value happens to live on, so a screen
    draws an item without knowing or caring which attributes earned a column.
    """
    bag = product.attrs if isinstance(getattr(product, "attrs", None), dict) else {}
    got = {}
    for a in attributes(db, for_product(db, product).id if for_product(db, product) else None):
        got[a["key"]] = (getattr(product, a["column"], None) if a["column"]
                         else bag.get(a["key"]))
    return got


def write_attrs(db: Session, product, values: dict) -> dict:
    """Set an item's attributes from {key: value}, each to wherever it belongs.

    Keys this catalogue does not declare are IGNORED rather than stored. A value
    nothing can display is not data, it is a typo with a database row, and the
    way one gets in is a renamed attribute.

    `Product.attrs` is REPLACED rather than mutated: SQLAlchemy does not notice a
    change made inside a JSON column, so a mutated dict is quietly never written.
    """
    cat = for_product(db, product)
    defs = {a["key"]: a for a in attributes(db, cat.id if cat else None)}
    bag = dict(product.attrs) if isinstance(getattr(product, "attrs", None), dict) else {}
    touched = {}
    for key, raw in (values or {}).items():
        a = defs.get(key)
        if not a:
            continue
        val = raw.strip() if isinstance(raw, str) else raw
        val = val if val not in ("", None) else None
        touched[key] = val
        if a["column"]:
            setattr(product, a["column"], val)
        elif val is None:
            bag.pop(key, None)
        else:
            bag[key] = val
    product.attrs = bag or None
    return touched


# ---------------------------------------------------------------------------
#  seeding — one database that predates all of this
# ---------------------------------------------------------------------------
def _file_options() -> dict:
    """The garment attribute vocabularies that used to ship as a JSON file."""
    try:
        with open(_ATTR_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: [str(v) for v in vals if str(v).strip()]
            for k, vals in data.items()
            if not k.startswith("_") and k != "products" and isinstance(vals, list)}


def ensure_seed(db: Session) -> dict:
    """Create the catalogues, and file everything that predates them.

    Idempotent and safe on every start. Four things happen, each only once:

      1. The GARMENTS catalogue exists and is the default.
      2. Its attributes are declared — the same ten the app has always captured.
      3. Its option lists are loaded from the shipped attribute master, so the
         dropdowns that used to come from that file keep offering exactly what
         they offered before, now from rows somebody can edit.
      4. Every category, product and warehouse with no catalogue is filed under
         it. Not a guess: a single global garment master is what "one business
         line" looked like, and these rows are all demonstrably that line.
    """
    made = {"catalogues": 0, "attributes": 0, "options": 0,
            "categories": 0, "products": 0, "warehouses": 0}

    garments = (db.query(models.Catalogue)
                  .filter(models.Catalogue.code == DEFAULT_CODE).first())
    if not garments:
        garments = models.Catalogue(
            code=DEFAULT_CODE, name="Garments",
            description="Readymade garments and accessories — the stock master "
                        "this warehouse was built on.",
            is_default=True, active=True)
        db.add(garments)
        db.commit()
        db.refresh(garments)
        made["catalogues"] += 1

    # Exactly one default. Two would make `default_catalogue` depend on row
    # order, and the answer would change the next time either was edited.
    if not db.query(models.Catalogue).filter(
            models.Catalogue.is_default.is_(True)).first():
        garments.is_default = True
        db.commit()

    # --- 2. the garment attribute set ---
    have_attrs = {a.key for a in db.query(models.CatalogueAttribute).filter(
        models.CatalogueAttribute.catalogue_id == garments.id).all()}
    if not have_attrs:
        for i, (key, identity) in enumerate(GARMENT_ATTRS):
            db.add(models.CatalogueAttribute(
                catalogue_id=garments.id, key=key, label=COLUMN_LABELS.get(key),
                column=key, identity=identity, sort=i, active=True))
            made["attributes"] += 1
        db.commit()

    # --- 3. its dropdown values, from the file that used to be the only source ---
    if not db.query(models.AttributeOption).filter(
            models.AttributeOption.catalogue_id == garments.id).first():
        for attr, values in _file_options().items():
            if attr not in COLUMN_ATTRS:
                continue
            for i, v in enumerate(values):
                if add_option(db, garments.id, attr, v, sort=i) is not None:
                    made["options"] += 1
        db.commit()

    # --- 4. file what predates catalogues ---
    made["categories"] = db.query(models.Category).filter(
        models.Category.catalogue_id.is_(None)).update(
        {"catalogue_id": garments.id}, synchronize_session=False)
    made["products"] = db.query(models.Product).filter(
        models.Product.catalogue_id.is_(None)).update(
        {"catalogue_id": garments.id}, synchronize_session=False)
    made["warehouses"] = db.query(models.Warehouse).filter(
        models.Warehouse.catalogue_id.is_(None)).update(
        {"catalogue_id": garments.id}, synchronize_session=False)
    if made["categories"] or made["products"] or made["warehouses"]:
        db.commit()
    return made


def create(db: Session, code, name, description=None, attrs=None):
    """A new business line. Starts EMPTY unless attributes are named.

    Empty is the honest default: nobody but the people who trade in it knows what
    a silk catalogue should contain, and pre-filling it with guesses produces a
    master somebody has to delete before they can build the real one.
    """
    code = (code or "").strip().upper()
    name = (name or "").strip()
    if not code or not name:
        raise ValueError("a catalogue needs a code and a name")
    if db.query(models.Catalogue).filter(models.Catalogue.code == code).first():
        raise ValueError(f"there is already a catalogue coded “{code}”")
    c = models.Catalogue(code=code, name=name, description=description or None,
                         is_default=False, active=True)
    db.add(c)
    db.flush()
    for i, key in enumerate(attrs or []):
        key = (key or "").strip()
        if not key:
            continue
        db.add(models.CatalogueAttribute(
            catalogue_id=c.id, key=key, label=COLUMN_LABELS.get(key),
            # A key that matches a Product column uses it, so a silk catalogue
            # recording a colour lands in the same place a garment's does — and
            # every screen, label and report that reads `color` sees both.
            column=key if key in COLUMN_ATTRS else None,
            identity=True, sort=i, active=True))
    db.commit()
    db.refresh(c)
    return c
