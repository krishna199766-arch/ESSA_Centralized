"""
Masters: product categories (imported from the GRN Excel), agents, transporters.

Agents and transporters are created automatically from whatever the extractor
reads off documents (invoice 'agent' / 'transporter', LR register 'transport'),
so the masters fill themselves — no manual entry — but can also be added by hand.
"""
import os
import json
from .. import models
from ..config import DATA_DIR

CATEGORIES_JSON = os.path.join(DATA_DIR, "categories.json")


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
