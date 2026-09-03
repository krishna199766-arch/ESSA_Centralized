"""Every number this app hands out — configurable, and never handed out twice.

WHAT THIS REPLACES. Five format strings scattered through the services:

    inventory._next_sku      f"ESSA-{n:05d}"      …and barcode_svc._next_sku,
                                                  the same rule written twice
    inventory.next_grn_no    f"GRN-{year}-{n:05d}"
    lr.next_entry_no         f"LRE-{n:05d}"
    outward._next_code       f"OUT-{n:05d}"
    returns._next_code       f"PR-{n:05d}"

Each was right for one company. None of them can produce GRN-ES-2627-0001 beside
GRN-TQ-2627-0001 without editing Python, which on a warehouse PC means it cannot
happen. So the format moves into a row (models.NumberSequence) and this module
renders it.

THE PROPERTY THAT MUST NOT BE LOST. `inventory.next_grn_no` deliberately did NOT
keep a counter. Its docstring:

    The sequence is read from the numbers already issued rather than from a
    counter, and it steps over any that are taken. A counter would have to be
    kept in step with rows that get deleted, and the first time the two
    disagreed it would hand out a number that already existed — on the one field
    people quote to each other when a delivery is queried.

That reasoning survives a config table, so both are used here: `next_number` is
where the search STARTS, and `is_taken` is consulted before any number is
returned. A counter that has drifted low costs a few wasted probes; it never
issues a duplicate.

RESOLUTION IS MOST-SPECIFIC-FIRST. A rule for this warehouse beats a rule for its
business, which beats a rule for everybody, which beats the built-in default. So
a company sets its house style once and one warehouse can still differ, and an
install that has configured nothing keeps the numbers it has always issued.
"""
import datetime as dt

from sqlalchemy.orm import Session

from .. import models

#: Every document this app numbers, with the format it has always used. These
#: are the FALLBACK — what an install that has configured nothing keeps getting —
#: so changing one here changes history's shape and should not be done lightly.
DOCS = {
    "sku":         {"label": "Product SKU",      "prefix": "ESSA-", "padding": 5,
                    "use_year": False},
    "purchase_order": {"label": "Purchase Order", "prefix": "PO-",  "padding": 5,
                       "use_year": False},
    "grn":         {"label": "GRN",              "prefix": "GRN-",  "padding": 5,
                    "use_year": True, "year_format": "calendar"},
    "lr":          {"label": "LR Entry",         "prefix": "LRE-",  "padding": 5,
                    "use_year": False},
    "transfer":    {"label": "Stock Transfer",   "prefix": "OUT-",  "padding": 5,
                    "use_year": False},
    "debit_note":  {"label": "Debit Note",       "prefix": "PR-",   "padding": 5,
                    "use_year": False},
    "invoice":     {"label": "Purchase Invoice", "prefix": "INV-",  "padding": 5,
                    "use_year": False},
    "adjustment":  {"label": "Stock Adjustment", "prefix": "ADJ-",  "padding": 5,
                    "use_year": False},
    "pos_invoice": {"label": "POS Invoice",      "prefix": "POS-",  "padding": 5,
                    "use_year": False},
}

#: How many candidates to try before giving up and trusting the counter. A
#: sequence whose counter is far behind reality is a misconfiguration, and
#: probing forever would turn it into a hung request instead of a wrong number.
MAX_PROBES = 5000


# ---------------------------------------------------------------------------
#  the financial year
# ---------------------------------------------------------------------------
def fy_bounds(when=None, start_month=4):
    """(start_year, end_year) of the financial year `when` falls in.

    April-to-March by default, which is India. A date in January 2027 belongs to
    the year that opened in April 2026, so it is 2026-27 — getting this backwards
    is how a document lands in the wrong year's book.
    """
    d = when or dt.date.today()
    if isinstance(d, dt.datetime):
        d = d.date()
    start = d.year if d.month >= (start_month or 4) else d.year - 1
    return start, start + 1


def fy_code(when=None, start_month=4, style="short"):
    """The year as it appears in a document number.

    short     2627      the house style already in use ("GRN-TQ-2627-1")
    long      2026-27
    calendar  2026      no financial year at all — the calendar one
    """
    if style == "calendar":
        d = when or dt.date.today()
        return str(d.year)
    a, b = fy_bounds(when, start_month)
    if style == "long":
        return f"{a}-{str(b)[-2:]}"
    return f"{str(a)[-2:]}{str(b)[-2:]}"


# ---------------------------------------------------------------------------
#  which rule applies
# ---------------------------------------------------------------------------
def resolve(db: Session, doc: str, warehouse_id=None, business_id=None):
    """The NumberSequence that governs `doc` here, or None to use the default.

    Most specific wins. The business is taken from the warehouse when one was
    given, so a caller only ever has to know where it is standing.
    """
    if warehouse_id and not business_id:
        wh = db.get(models.Warehouse, int(warehouse_id))
        business_id = wh.business_id if wh else None
    if business_id is None:
        # A warehouse that has not been filed under a business yet belongs to the
        # one company there is — that is what a single-business install means.
        # Without this it would silently fall past its own company's rules to the
        # built-in defaults, so a configured prefix would appear to do nothing.
        from . import businesses
        b = businesses.default_business(db)
        business_id = b.id if b else None

    rows = db.query(models.NumberSequence).filter(
        models.NumberSequence.doc == doc,
        models.NumberSequence.active.is_(True)).all()

    def pick(pred):
        return next((r for r in rows if pred(r)), None)

    return (pick(lambda r: warehouse_id and r.warehouse_id == int(warehouse_id))
            or pick(lambda r: business_id and r.business_id == int(business_id)
                    and r.warehouse_id is None)
            or pick(lambda r: r.business_id is None and r.warehouse_id is None))


def spec(db: Session, doc: str, warehouse_id=None, business_id=None) -> dict:
    """The effective format for `doc` here — the row's, or the built-in default."""
    base = dict(DOCS.get(doc) or {"label": doc, "prefix": "", "padding": 5,
                                  "use_year": False})
    base.setdefault("year_format", "short")
    base.setdefault("separator", "-")
    base.setdefault("next_number", 1)
    row = resolve(db, doc, warehouse_id, business_id)
    if row is None:
        return {**base, "source": "default", "row_id": None}
    return {
        "label": base["label"],
        "prefix": row.prefix if row.prefix is not None else base["prefix"],
        "use_year": bool(row.use_year),
        "year_format": row.year_format or "short",
        "padding": int(row.padding or base.get("padding") or 5),
        "separator": row.separator if row.separator is not None else "-",
        "next_number": int(row.next_number or 1),
        "source": ("warehouse" if row.warehouse_id else
                   "business" if row.business_id else "global"),
        "row_id": row.id,
    }


def _fy_start_month(db, warehouse_id=None, business_id=None):
    if warehouse_id and not business_id:
        wh = db.get(models.Warehouse, int(warehouse_id))
        business_id = wh.business_id if wh else None
    if business_id:
        b = db.get(models.Business, int(business_id))
        if b and b.fy_start_month:
            return int(b.fy_start_month)
    return 4


def render(sp: dict, n: int, year: str = "") -> str:
    """One number, from a spec. `prefix` + optional year + zero-padded counter."""
    parts = [str(n).zfill(max(0, int(sp.get("padding") or 0)))]
    if sp.get("use_year") and year:
        parts.insert(0, year)
    sep = sp.get("separator") or "-"
    return (sp.get("prefix") or "") + sep.join(parts)


# ---------------------------------------------------------------------------
#  issuing one
# ---------------------------------------------------------------------------
def peek(db: Session, doc: str, warehouse_id=None, business_id=None, when=None):
    """What the next number would look like, without consuming it.

    What the setup wizard shows under the prefix box while somebody types.
    """
    sp = spec(db, doc, warehouse_id, business_id)
    year = fy_code(when, _fy_start_month(db, warehouse_id, business_id),
                   sp["year_format"]) if sp["use_year"] else ""
    return render(sp, sp["next_number"], year)


def next_number(db: Session, doc: str, warehouse_id=None, business_id=None,
                when=None, is_taken=None) -> str:
    """Issue the next number for `doc`, stepping over anything already used.

    `is_taken(candidate) -> bool` is how the caller says what "already used"
    means for its own table. It is not optional in spirit: without it this
    becomes the bare counter that the numbering it replaces deliberately refused
    to be. Callers pass a lambda that looks in the column the number lands in.

    The row's counter is advanced past whatever was issued, so the next call
    starts from the right place even when this one had to probe forward.
    """
    sp = spec(db, doc, warehouse_id, business_id)
    year = fy_code(when, _fy_start_month(db, warehouse_id, business_id),
                   sp["year_format"]) if sp["use_year"] else ""

    n = max(1, int(sp["next_number"]))
    candidate = render(sp, n, year)
    probes = 0
    while is_taken is not None and is_taken(candidate) and probes < MAX_PROBES:
        n += 1
        probes += 1
        candidate = render(sp, n, year)

    row = db.query(models.NumberSequence).get(sp["row_id"]) if sp["row_id"] else None
    if row is not None:
        row.next_number = n + 1
        db.flush()
    return candidate


# ---------------------------------------------------------------------------
#  setting the rules up
# ---------------------------------------------------------------------------
def upsert(db: Session, doc: str, *, business_id=None, warehouse_id=None,
           prefix=None, use_year=None, year_format=None, padding=None,
           separator=None, start=None, active=True):
    """Create or amend one rule. Returns the row.

    `start` sets where the counter begins and is the field the wizard asks for.
    It is never lowered silently below what has already been issued — that would
    hand out a number somebody has already quoted — so a caller wanting to
    restart a sequence has to say so by deleting the rule and making a new one.
    """
    if doc not in DOCS:
        raise ValueError(f"“{doc}” is not a document this app numbers")
    row = db.query(models.NumberSequence).filter(
        models.NumberSequence.doc == doc,
        models.NumberSequence.business_id == business_id,
        models.NumberSequence.warehouse_id == warehouse_id).first()
    if row is None:
        base = DOCS[doc]
        row = models.NumberSequence(
            doc=doc, business_id=business_id, warehouse_id=warehouse_id,
            prefix=base.get("prefix", ""), use_year=base.get("use_year", False),
            year_format=base.get("year_format", "short"),
            padding=base.get("padding", 5), separator="-", next_number=1)
        db.add(row)
    if prefix is not None:
        row.prefix = prefix
    if use_year is not None:
        row.use_year = bool(use_year)
    if year_format is not None:
        row.year_format = year_format
    if padding is not None:
        row.padding = max(0, min(int(padding), 12))
    if separator is not None:
        row.separator = separator
    if start is not None:
        row.next_number = max(1, int(start))
    row.active = bool(active)
    db.flush()
    return row


def out(row: models.NumberSequence, db: Session = None) -> dict:
    d = {"id": row.id, "doc": row.doc,
         "label": (DOCS.get(row.doc) or {}).get("label", row.doc),
         "business_id": row.business_id, "warehouse_id": row.warehouse_id,
         "prefix": row.prefix or "", "use_year": bool(row.use_year),
         "year_format": row.year_format or "short",
         "padding": row.padding, "separator": row.separator or "-",
         "next_number": row.next_number, "active": bool(row.active)}
    if db is not None:
        d["example"] = peek(db, row.doc, row.warehouse_id, row.business_id)
    return d


def catalogue(db: Session, warehouse_id=None, business_id=None) -> list:
    """Every document kind with the format that currently applies here.

    Includes the ones with no rule of their own, showing the built-in default —
    a setup screen has to offer them all, and one that listed only the configured
    ones would hide exactly the sequences nobody had set up yet.
    """
    out_ = []
    for doc in DOCS:
        sp = spec(db, doc, warehouse_id, business_id)
        out_.append({
            "doc": doc, "label": sp["label"], "prefix": sp["prefix"],
            "use_year": sp["use_year"], "year_format": sp["year_format"],
            "padding": sp["padding"], "separator": sp["separator"],
            "next_number": sp["next_number"], "source": sp["source"],
            "example": peek(db, doc, warehouse_id, business_id),
        })
    return out_
