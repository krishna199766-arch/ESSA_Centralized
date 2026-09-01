"""Warehouses, stores and POS terminals — the places this company trades from.

WHAT THIS REPLACES. "Where" used to be a name. Stock Outward dispatches to
`to_destination` (free text), an LR forwards to `auto_transfer_location` (free
text), and the list somebody was supposed to maintain is `master_options` of
kind `auto_transfer_location` — which is a SEEDED list holding exactly one
value, "NONE", that the API refuses additions to. So there was no way to add a
branch through the app at all, and the retail shop's location sync, which reads
that list, received nothing to sync.

WHY THE OLD LIST IS STILL WRITTEN. Four master definitions and the LR form read
`option:auto_transfer_location` for their dropdowns, and LR rows already store
the chosen name as a string. Dropping the list would empty those dropdowns and
strand that history. So these tables become the system of record and every
active store is MIRRORED back into the option list — the old readers keep
working unchanged and simply start seeing real branches.

WHY NAMES STILL MATTER. The shop (Textile Retail Shop) keeps its own
`locations`/`counters` in its own database and matches them to ours BY NAME,
case-insensitively — see the shop's app/places.sync_locations. Renaming a store
here therefore does not rename it there; it creates a second one. Until the
shop learns to match on `code`, a rename is a two-sided operation and this
module refuses to pretend otherwise (see `rename_warning`).

NOTHING HERE SPLITS STOCK. Product.stock_qty is still one figure and
StockMovement still has no location column. These tables are what that change
needs in place first.
"""
from typing import Optional

from sqlalchemy.orm import Session

from .. import models

#: The list the whole app already reads for "which branch".
LOCATION_OPTION_KIND = "auto_transfer_location"

#: "keep it here" — the default on the LR form, and never a store.
NONE_VALUE = "NONE"

#: What a warehouse is called when one has to be invented to hold the stores a
#: pre-existing install already had.
DEFAULT_WAREHOUSE = "Main Warehouse"


# ---------------------------------------------------------------------------
#  codes
# ---------------------------------------------------------------------------
def _next_code(db: Session, model, prefix: str) -> str:
    """The next free PREFIX-nn.

    Codes are generated rather than required because the person adding a store
    is naming a place, not allocating an identifier, and a screen that demands
    one before it will save is a screen people work around by typing anything.
    An explicit code is always honoured — this only fills the blank.
    """
    n = db.query(model).count() + 1
    while True:
        code = f"{prefix}-{n:02d}"
        if not db.query(model).filter(model.code == code).first():
            return code
        n += 1


# ---------------------------------------------------------------------------
#  the option list the rest of the app reads
# ---------------------------------------------------------------------------
def mirror_to_options(db: Session) -> int:
    """Make the legacy dropdown say exactly what the active stores say.

    Additive for anything that is not a store: NONE stays, and a value somebody
    typed against an LR before this existed is left alone. Only a name that WAS
    a mirrored store and is no longer active is withdrawn, so a closed branch
    stops being offered without erasing the rows that already name it — those
    are strings, not foreign keys, and history stays readable.
    """
    active = {s.name for s in db.query(models.Store).filter(
        models.Store.active.is_(True)).all()}
    every = {s.name for s in db.query(models.Store).all()}

    have = {o.value: o for o in db.query(models.MasterOption).filter(
        models.MasterOption.kind == LOCATION_OPTION_KIND).all()}

    changed = 0
    for name in sorted(active):
        if name not in have:
            db.add(models.MasterOption(kind=LOCATION_OPTION_KIND, value=name, sort=1))
            changed += 1
    # withdraw the ones that are ours and switched off
    for value, row in have.items():
        if value == NONE_VALUE:
            continue
        if value in every and value not in active:
            db.delete(row)
            changed += 1
    if changed:
        db.commit()
    return changed


def backfill_from_options(db: Session) -> int:
    """Turn branch names that predate these tables into Store rows.

    An install whose option list was edited directly in the database, or seeded
    by an earlier build, has real branch names in it. They become stores under a
    default warehouse so nothing that already names them is orphaned. NONE is
    not a place and is skipped.
    """
    names = [o.value for o in db.query(models.MasterOption).filter(
        models.MasterOption.kind == LOCATION_OPTION_KIND).all()
        if o.value and o.value.strip() and o.value.strip().upper() != NONE_VALUE]
    if not names:
        return 0

    have = {s.name.lower() for s in db.query(models.Store).all()}
    todo = [n for n in names if n.strip().lower() not in have]
    if not todo:
        return 0

    wh = ensure_default_warehouse(db)
    for name in todo:
        db.add(models.Store(name=name.strip(), warehouse_id=wh.id,
                            code=_next_code(db, models.Store, "ST"), active=True))
        db.flush()
    db.commit()
    return len(todo)


def ensure_default_warehouse(db: Session) -> models.Warehouse:
    """The warehouse a store goes under when nobody has said which.

    Every existing install IS one warehouse — that is the assumption the whole
    app was built on — so inventing one to hang its stores off is describing
    what is already true rather than adding a place.
    """
    wh = db.query(models.Warehouse).order_by(models.Warehouse.id).first()
    if wh:
        return wh
    # Filed under the company as it is created. This warehouse is made LAZILY —
    # on the first call that needs one, which is usually after the business seed
    # has already run — so waiting for that seed to backfill it would leave it
    # unfiled, and its business's document-numbering rules would not apply to it.
    from . import businesses
    biz = businesses.default_business(db)
    wh = models.Warehouse(name=DEFAULT_WAREHOUSE,
                          code=_next_code(db, models.Warehouse, "WH"),
                          business_id=biz.id if biz else None, active=True)
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


def sync(db: Session) -> dict:
    """Bring the tables and the legacy list into agreement. Safe to run again."""
    added = backfill_from_options(db)
    mirrored = mirror_to_options(db)
    return {"stores_created": added, "options_changed": mirrored}


# ---------------------------------------------------------------------------
#  reads
# ---------------------------------------------------------------------------
#: The identity columns models.LocationProfile puts on all three levels, in the
#: order the form asks for them. Named once so the serializer below, the
#: request schema in routers/locations.py and the writer that applies it cannot
#: drift apart — a field added to the model and forgotten in one of the three
#: is a field that saves and never comes back, or comes back and never saves.
PROFILE_FIELDS = ("loc_type", "address", "address2", "city", "district",
                  "state", "state_code", "country", "pincode",
                  "contact_person", "phone", "email", "gstin", "cin")


def profile_out(obj) -> dict:
    """The postal/statutory block, on every level's payload.

    Emitted whether or not it is filled in. A key that appears only when it has
    a value makes the editor's job "is this absent or is it empty", and the
    answer decides whether PATCHing it back clears the column.
    """
    return {f: getattr(obj, f, None) for f in PROFILE_FIELDS}


def warehouse_out(w: models.Warehouse, counts=True) -> dict:
    out = {"id": w.id, "name": w.name, "code": w.code,
           "active": bool(w.active), "business_id": w.business_id,
           # What this building trades in. Carried on every warehouse payload
           # because the screens that pick a warehouse — GRN, dispatch, the
           # phone's detail form — need to know which vocabulary follows from it.
           "catalogue_id": w.catalogue_id,
           "catalogue": w.catalogue.name if w.catalogue else None,
           "catalogue_code": w.catalogue.code if w.catalogue else None,
           **profile_out(w)}
    if counts:
        out["store_count"] = len([s for s in w.stores if s.active])
    return out


def store_out(s: models.Store, counts=True) -> dict:
    out = {"id": s.id, "name": s.name, "code": s.code,
           "active": bool(s.active), "warehouse_id": s.warehouse_id,
           "business_id": s.business_id,
           "warehouse_name": s.warehouse.name if s.warehouse else None,
           **profile_out(s)}
    if counts:
        out["terminal_count"] = len([t for t in s.terminals if t.active])
    return out


def terminal_out(t: models.PosTerminal) -> dict:
    return {"id": t.id, "name": t.name, "code": t.code, "active": bool(t.active),
            "store_id": t.store_id, "business_id": t.business_id,
            "store_name": t.store.name if t.store else None,
            "warehouse_id": t.store.warehouse_id if t.store else None,
            **profile_out(t)}


def tree(db: Session, allowed=None) -> list:
    """The whole hierarchy in one read — what the Locations screen draws.

    `allowed` narrows it to a set of warehouse ids — an account allotted certain
    buildings. This is the read the GRN receiving picker and the dispatch
    from/to pickers are built from, so narrowing it here is what stops a
    restricted manager being OFFERED a warehouse the server would then refuse.
    None means no narrowing, which is every unrestricted account.
    """
    out = []
    q = db.query(models.Warehouse)
    if allowed:
        q = q.filter(models.Warehouse.id.in_(list(allowed)))
    for w in q.order_by(models.Warehouse.name).all():
        node = warehouse_out(w, counts=False)
        node["stores"] = []
        for s in sorted(w.stores, key=lambda x: x.name):
            sn = store_out(s, counts=False)
            sn["terminals"] = [terminal_out(t) for t in
                               sorted(s.terminals, key=lambda x: x.name)]
            node["stores"].append(sn)
        out.append(node)
    # A store whose warehouse was never set would otherwise be invisible on a
    # screen built from the tree — and invisible is how a place keeps receiving
    # stock nobody can account for.
    # A store belonging to no warehouse is nobody's to claim, so an account
    # confined to particular buildings is not shown it either — it would be a
    # branch they cannot reach through any warehouse they hold.
    orphans = [] if allowed else db.query(models.Store).filter(
        models.Store.warehouse_id.is_(None)).order_by(models.Store.name).all()
    if orphans:
        out.append({"id": None, "name": "Not assigned to a warehouse",
                    "code": None, "address": None, "active": True,
                    "unassigned": True,
                    "stores": [dict(store_out(s, counts=False),
                                    terminals=[terminal_out(t) for t in s.terminals])
                               for s in orphans]})
    return out


def store_rows(db: Session, warehouse_id=None) -> list:
    """Every store as a FLAT row, with the warehouse that supplies it.

    The tree above answers "what does this warehouse supply"; this answers "what
    stores are there", which is the question a consolidated dashboard asks. Drawn
    from the same rows, so the two can never disagree — a second query shaped
    differently is how a store comes to exist on one screen and not the other.
    """
    q = db.query(models.Store)
    if warehouse_id:
        q = q.filter(models.Store.warehouse_id == warehouse_id)
    out = []
    for s in q.order_by(models.Store.name).all():
        wh = s.warehouse
        tills = [t for t in s.terminals if t.active]
        out.append({
            "id": s.id, "name": s.name, "code": s.code, "address": s.address,
            "active": bool(s.active),
            "warehouse_id": s.warehouse_id,
            "warehouse": wh.name if wh else None,
            "catalogue": wh.catalogue.name if (wh and wh.catalogue) else None,
            # A store with no till cannot bill, and that is worth seeing on a
            # list of stores rather than only by opening each one.
            "terminals": len(tills),
            "terminal_names": [t.name for t in tills],
            # POS is what the wireframe calls a till; the type column tells a
            # store that sells from one that only holds stock.
            "type": "POS" if tills else "Store",
        })
    return out


def rename_warning(old: str, new: str) -> Optional[str]:
    """Why renaming a store is not only a rename.

    The shop matches its own locations to ours by NAME. A rename here does not
    reach it: the old name stays down there holding that branch's stock and its
    bills, and the new one arrives as an empty second branch. Said out loud
    rather than blocked — the rename may well be the right thing to do, and the
    person doing it is the person who can fix the far side.
    """
    if not old or not new or old.strip() == new.strip():
        return None
    return (f"The retail shop matches branches by name, so it still knows this "
            f"one as “{old}”. Renaming it here will not rename it there — "
            f"rename it in the shop too, or its stock and bills stay filed "
            f"under the old name.")
