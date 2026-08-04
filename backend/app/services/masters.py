"""
Masters: product categories (imported from the GRN Excel), agents, transporters,
and the small keyed dropdown lists behind the LR Entry form (see MasterOption).

Agents and transporters are created automatically from whatever the extractor
reads off documents (invoice 'agent' / 'transporter', LR register 'transport'),
so the masters fill themselves — no manual entry — but can also be added by hand.
The keyed option lists work the same way: a company or rack typed on the LR form
is remembered, so the next entry picks it from the dropdown.
"""
import os
import json
from .. import models
from ..config import DATA_DIR

CATEGORIES_JSON = os.path.join(DATA_DIR, "categories.json")

# ---- keyed dropdown lists (MasterOption) ------------------------------------
#
# SEEDED lists are fixed vocabulary — the set of ways a consignment can travel or
# a bill can settle doesn't grow from data entry, and a typo shouldn't add a new
# "Hand Delivry" mode. OPEN lists start empty and learn from what is entered.
SEEDED_OPTIONS = {
    "lr_mode": ["Hand Delivery", "Transport", "Courier", "Train", "Air Cargo",
                "Self Pickup"],
    "attachment_type": ["LR Copy", "Invoice", "Packing Slip", "Weight Slip",
                        "Photo", "Other"],
    # NONE is the default on the form: received goods stay in this warehouse
    # unless a branch is named, so it has to be a pickable value, not a blank.
    "auto_transfer_location": ["NONE"],
}
# Lists whose LR Entry field has since been dropped as unused. A dropdown with
# nothing left to fill is just a Masters tab that does nothing, so they are
# retired here and purged from any database that ran an earlier build.
OPEN_OPTIONS = ["purchase_manager"]
RETIRED_OPTIONS = ["city", "pay_mode", "company", "bundle_rack", "section"]
OPTION_KINDS = sorted(set(SEEDED_OPTIONS) | set(OPEN_OPTIONS))


def seed_options(db):
    """Put the fixed vocabularies in place (idempotent).

    Also clears out lists whose field has since been removed, so an install that
    ran an older build doesn't keep offering a dropdown nothing reads."""
    n = 0
    stale = db.query(models.MasterOption).filter(
        models.MasterOption.kind.in_(RETIRED_OPTIONS)).delete(synchronize_session=False)
    if stale:
        db.commit()
    for kind, values in SEEDED_OPTIONS.items():
        have = {o.value for o in db.query(models.MasterOption).filter(
            models.MasterOption.kind == kind).all()}
        for i, v in enumerate(values):
            if v not in have:
                db.add(models.MasterOption(kind=kind, value=v, sort=i))
                n += 1
    if n:
        db.commit()
    return n


def get_or_create_option(db, kind, value):
    """Remember a value typed on a form, so it is offered next time.

    Only OPEN lists grow this way — a value that isn't in a seeded vocabulary is
    a typo, not a new mode, and silently adding it would corrupt the list."""
    value = (value or "").strip()
    if not value or kind not in OPTION_KINDS:
        return None
    if kind not in OPEN_OPTIONS:
        return db.query(models.MasterOption).filter(
            models.MasterOption.kind == kind, models.MasterOption.value == value).first()
    o = db.query(models.MasterOption).filter(
        models.MasterOption.kind == kind, models.MasterOption.value == value).first()
    if not o:
        o = models.MasterOption(kind=kind, value=value)
        db.add(o)
        db.flush()
    return o


def options(db, kind):
    """The values for one list, seeded order first then alphabetical."""
    rows = db.query(models.MasterOption).filter(
        models.MasterOption.kind == kind).order_by(
        models.MasterOption.sort, models.MasterOption.value).all()
    return [o.value for o in rows]


def all_options(db):
    return {k: options(db, k) for k in OPTION_KINDS}


def import_categories(db, force=False):
    """Load categories.json (from GRN PRODUCT DETAILS.xlsx) into the DB once."""
    if not force and db.query(models.Category).first():
        return 0
    if not os.path.exists(CATEGORIES_JSON):
        return 0
    with open(CATEGORIES_JSON) as f:
        data = json.load(f)
    if force:
        db.query(models.Category).delete()
    existing = {(c.section, c.name) for c in db.query(models.Category).all()}
    n = 0
    for section, names in data.items():
        for name in names:
            key = (section, name)
            if key in existing:
                continue
            db.add(models.Category(section=section, name=name))
            existing.add(key)
            n += 1
    db.commit()
    return n


def get_or_create_agent(db, name):
    name = (name or "").strip()
    if not name or name.lower() in ("direct", "none", "-"):
        return None
    a = db.query(models.Agent).filter(models.Agent.name == name).first()
    if not a:
        a = models.Agent(name=name)
        db.add(a)
        db.flush()
    return a


def get_or_create_transport(db, name):
    name = (name or "").strip()
    if not name or name.lower() in ("none", "-", "direct"):
        return None
    t = db.query(models.Transport).filter(models.Transport.name == name).first()
    if not t:
        t = models.Transport(name=name)
        db.add(t)
        db.flush()
    return t


def category_names(db, section=None):
    q = db.query(models.Category)
    if section:
        q = q.filter(models.Category.section == section)
    return [c.name for c in q.order_by(models.Category.name).all()]
