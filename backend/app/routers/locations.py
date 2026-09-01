"""Warehouses, stores and POS terminals.

One router for the three levels because they are one tree and one screen edits
all of it. See services/locations.py for what these tables replace and why the
old `auto_transfer_location` option list is still written.

Nothing here deletes by default. A place that closed still has last year's
transfers and bills filed against it, and those rows name it as a STRING — so
removing the row to tidy a dropdown orphans them silently. `active=false` is the
close-a-branch operation; DELETE exists only for a place created by mistake and
refuses as soon as anything is filed under it.
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..services import locations as svc
from ..services import stock_locations as stock_loc
from ..services import pos_store_sales

router = APIRouter(prefix="/api/locations", tags=["locations"])

#: What sort of place this is, offered as a closed list so the same three words
#: come back on every row and a report can group by them. NOT the catalogue —
#: see models.LocationProfile.loc_type for why "Franchise" is not a business
#: line. Served to the UI at GET /api/locations/types so the vocabulary is
#: defined once, here, rather than typed again into a <select> that drifts.
LOCATION_TYPES = ["Garments", "Silks", "Franchise"]

#: 15 characters: two state digits, a ten-character PAN, an entity digit, a
#: fixed Z, and a check character. Worth checking because the first two digits
#: become `state_code`, and a number typed one character short would silently
#: file the place in the wrong state and make every sale from it inter-state.
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$")


class LocationProfileIn(BaseModel):
    """The postal and statutory block, identical at all three levels.

    Every field is Optional and defaults to None, which the writers below read
    as "not mentioned, leave it alone". That is what lets the same schema serve
    POST (where everything is mentioned) and PATCH (where the close-a-branch
    button sends nothing but `name` and `active`) without a second model whose
    field list would fall behind this one.
    """
    loc_type: Optional[str] = None
    address: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    #: Accepted but normally derived — see _apply_profile.
    state_code: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    gstin: Optional[str] = None
    cin: Optional[str] = None
    #: Whose place it is. Nullable throughout: an install that predates the
    #: Business table has exactly one company, and guessing at every read is
    #: what filling it in on the first run avoids.
    business_id: Optional[int] = None


class WarehouseIn(LocationProfileIn):
    name: str
    code: Optional[str] = None
    active: Optional[bool] = None
    #: Which business line this building trades in. Blank takes the default,
    #: which is what a single-catalogue company has always meant.
    catalogue_id: Optional[int] = None


class StoreIn(LocationProfileIn):
    name: str
    warehouse_id: Optional[int] = None
    code: Optional[str] = None
    active: Optional[bool] = None


class TerminalIn(LocationProfileIn):
    name: str
    store_id: Optional[int] = None
    code: Optional[str] = None
    active: Optional[bool] = None


def _clean(s: Optional[str]) -> Optional[str]:
    s = (s or "").strip()
    return s or None


def _apply_profile(obj, body: LocationProfileIn, db: Session) -> None:
    """Write the shared identity block onto a warehouse, store or terminal.

    One writer for all three, driven by services.locations.PROFILE_FIELDS, so a
    column added to models.LocationProfile reaches every level at once. Writing
    each level's fields out by hand is how a field comes to save on a warehouse
    and be quietly dropped on a store.

    `None` means "not mentioned" and leaves the column as it was; an empty
    string means "clear it". Those have to be told apart, because the toggle
    button PATCHes `{name, active}` alone and must not wipe an address.
    """
    if body.gstin is not None:
        g = (body.gstin or "").strip().upper()
        if g and not GSTIN_RE.match(g):
            raise HTTPException(400, f"“{g}” is not a GST number — it is 15 "
                                     f"characters, like 33AADCE6591N1Z7")
        obj.gstin = g or None
        # The first two digits ARE the state code. Derived rather than asked
        # for, the way services/businesses.py already derives the company's:
        # two fields that must agree, both typed by hand, disagree eventually,
        # and the wrong one decides whether a sale is inter-state.
        if g:
            obj.state_code = g[:2]

    if body.pincode is not None:
        p = (body.pincode or "").strip()
        if p and not re.fullmatch(r"\d{6}", p):
            raise HTTPException(400, "a PIN code is 6 digits")
        obj.pincode = p or None

    if body.email is not None:
        e = (body.email or "").strip()
        if e and "@" not in e:
            raise HTTPException(400, f"“{e}” is not an email address")
        obj.email = e or None

    if body.loc_type is not None:
        t = (body.loc_type or "").strip()
        if t and t not in LOCATION_TYPES:
            raise HTTPException(400, f"type must be one of "
                                     f"{', '.join(LOCATION_TYPES)}")
        obj.loc_type = t or None

    if body.business_id is not None:
        # Checked rather than trusted: foreign keys are enforced (database.py),
        # so an id that does not exist would surface as a bare 500 instead of
        # this sentence.
        if body.business_id and not db.get(models.Business, body.business_id):
            raise HTTPException(404, "that business does not exist")
        obj.business_id = body.business_id or None

    # everything else is plain text, minus the four handled above
    handled = {"gstin", "pincode", "email", "loc_type", "state_code"}
    for f in svc.PROFILE_FIELDS:
        if f in handled:
            continue
        v = getattr(body, f, None)
        if v is not None:
            setattr(obj, f, _clean(v))
    # An explicit state code still wins where no GSTIN was given — a place that
    # is not registered has a state all the same.
    if body.state_code is not None and not (body.gstin or "").strip():
        obj.state_code = _clean(body.state_code)


def _catalogue_id(db: Session, given):
    """A catalogue that certainly exists, defaulting when none was named."""
    from ..services import catalogues as cat_svc
    cat_svc.ensure_seed(db)
    if given:
        c = db.get(models.Catalogue, int(given))
        if not c:
            raise HTTPException(404, "catalogue not found")
        if not c.active:
            raise HTTPException(400, f"“{c.name}” is switched off — a warehouse "
                                     f"can't be opened against a retired line")
        return c.id
    return cat_svc.resolve_id(db, None)


# ---------------------------------------------------------------------------
#  the tree
# ---------------------------------------------------------------------------
def _allotted(request: Request):
    """The warehouse ids this caller is confined to, or None for all of them.

    Put on the request by security.auth_middleware, which is the one place that
    resolves the account. Read defensively: a route reached outside the
    middleware (a test client calling a public path) simply has no restriction.
    """
    return getattr(request.state, "warehouses", None) or None


@router.get("")
def get_tree(request: Request, db: Session = Depends(get_db)):
    """Every warehouse, its stores and their terminals — the Locations screen.

    Runs the backfill/mirror first so a database that predates these tables
    answers with its existing branches already turned into stores, rather than
    looking empty until somebody presses something.
    """
    svc.sync(db)
    return {"warehouses": svc.tree(db, allowed=_allotted(request))}


@router.post("/sync")
def resync(db: Session = Depends(get_db)):
    """Re-run the backfill and re-mirror the dropdown. Idempotent."""
    return svc.sync(db)


@router.get("/form-options")
def form_options(db: Session = Depends(get_db)):
    """The closed lists the location form picks from.

    Fetched rather than hard-coded into the UI so the three type words and the
    company list are defined in one place. A <select> that repeats a server
    vocabulary is a copy that goes stale the first time the vocabulary changes,
    and it goes stale silently — the form keeps offering a value the API has
    started refusing.
    """
    from ..services import businesses as biz_svc
    biz_svc.ensure_seed(db)
    rows = db.query(models.Business).order_by(models.Business.name).all()
    return {
        "types": LOCATION_TYPES,
        "businesses": [{"id": b.id, "code": b.code, "name": b.name,
                        "gstin": b.gstin, "state": b.state,
                        "state_code": b.state_code, "city": b.city,
                        "is_default": bool(b.is_default),
                        "active": bool(b.active)} for b in rows],
    }


# ---------------------------------------------------------------------------
#  stock, by where it is standing
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview(request: Request, days: int = 14, warehouse_id: Optional[int] = None,
             db: Session = Depends(get_db)):
    """The central dashboard: the company's totals, and the row per warehouse.

    One call rather than one per warehouse. The dashboard draws a table with a
    line for every building, two charts and six tiles, and asking the server
    separately for each of them turns opening a screen into N round trips —
    which is slow on a laptop and expensive on a deployment that charges per
    invocation.

    `warehouse_id` scopes the whole answer to one building — the tiles, the
    movement figures and the charts — which is what the screen's warehouse
    picker sends. The warehouse TABLE still lists every building either way: it
    is how somebody switches to another one, and a filter that hid the other
    rows would leave no way back.
    """
    import datetime as dt

    svc.sync(db)
    allowed = _allotted(request)
    every = stock_loc.warehouse_totals(db, warehouse_ids=allowed)
    rows = ([r for r in every if r["warehouse_id"] == warehouse_id]
            if warehouse_id else every)

    now = dt.datetime.utcnow()
    since = now - dt.timedelta(days=max(1, int(days or 14)))
    flow = stock_loc.movement_flow(db, since=since, warehouse_id=warehouse_id)
    today = stock_loc.movement_flow(
        db, since=now.replace(hour=0, minute=0, second=0, microsecond=0),
        warehouse_id=warehouse_id)

    stores_q = db.query(models.Store).filter(models.Store.active.is_(True))
    if allowed:
        stores_q = stores_q.filter(models.Store.warehouse_id.in_(list(allowed)))
    if warehouse_id:
        stores_q = stores_q.filter(models.Store.warehouse_id == warehouse_id)

    return {
        # every building, always — the picker is built from this
        "warehouses": every,
        "scope": {"warehouse_id": warehouse_id,
                  "name": (rows[0]["name"] if rows and warehouse_id else None)},
        "totals": {
            "warehouses": len([r for r in rows if r["active"]]),
            "stores": stores_q.count(),
            "qty": round(sum(r["qty"] for r in rows), 3),
            "value": round(sum(r["value"] for r in rows), 2),
            "items": sum(r["items"] for r in rows),
        },
        # Today's movement is the pair of figures the wireframe's tiles show;
        # the window figure is the total beneath the chart drawn from `series`.
        "today": today,
        "window": {**flow, "days": int(days or 14)},
        "series": stock_loc.movement_series(db, days=days,
                                            warehouse_id=warehouse_id, today=now),
        # Every store, flat, with the warehouse that supplies it — the dashboard
        # draws a Stores / POS section from this and needs no second call.
        # Narrowed to the allotted buildings as well, or a restricted manager
        # would read other warehouses' shops in a section beneath figures that
        # correctly exclude them.
        "stores": [s for s in svc.store_rows(db, warehouse_id)
                   if not allowed or s["warehouse_id"] in allowed],
        # What has moved between our own places over the window: sent, received,
        # and the quantity standing in neither building because it is in transit.
        "transfers": stock_loc.transfer_summary(db, since=since,
                                                warehouse_id=warehouse_id),
    }


@router.get("/sales")
def store_sales(request: Request, days: int = 14, date_from: str = None,
                date_to: str = None, warehouse_id: Optional[int] = None,
                db: Session = Depends(get_db)):
    """What each shop sold, and the warehouse that supplies it.

    A SEPARATE call from /overview, deliberately. /overview is one request by
    design, but this reads a second database — the till's — which can be absent,
    empty or mid-write. Folding it in would let a shop that is merely switched
    off take the whole consolidated dashboard down with it. Here, the dashboard
    draws and this section says why it is empty.
    """
    allowed = _allotted(request)
    res = pos_store_sales.by_warehouse(
        db, days=days, date_from=date_from, date_to=date_to,
        warehouse_id=warehouse_id)
    if allowed and res.get("available"):
        res["stores"] = [s for s in res["stores"]
                         if s.get("warehouse_id") in allowed]
        res["warehouses"] = [w for w in res.get("warehouses", [])
                             if w["warehouse_id"] in allowed]
        # A branch the two systems spell differently belongs to no warehouse we
        # can name, so a restricted account is not shown it either.
        res["unmatched"] = []
        res["totals"] = {
            "stores": len(res["stores"]),
            "bills": sum(s["bills"] for s in res["stores"]),
            "units": round(sum(s["units"] for s in res["stores"]), 3),
            "gross": round(sum(s["gross"] for s in res["stores"]), 2),
            "taxable": round(sum(s["taxable"] for s in res["stores"]), 2),
            "tax": round(sum(s["tax"] for s in res["stores"]), 2),
            "returns": round(sum(s["returns"] for s in res["stores"]), 2),
            "net": round(sum(s["net"] for s in res["stores"]), 2),
        }
    res["series"] = pos_store_sales.daily(db, days=days, warehouse_id=warehouse_id)
    return res


@router.get("/warehouses/{wid}/stock")
def warehouse_stock(wid: int, limit: Optional[int] = None,
                    db: Session = Depends(get_db)):
    """What is standing in one warehouse, most valuable first."""
    wh = db.get(models.Warehouse, wid)
    if not wh:
        raise HTTPException(404, "warehouse not found")
    rows = stock_loc.stock_at(db, wid, limit=limit)
    return {"warehouse": svc.warehouse_out(wh),
            "qty": round(sum(r["qty"] for r in rows), 3),
            "value": round(sum(r["value"] for r in rows), 2),
            "items": len(rows), "rows": rows}


@router.post("/rebuild-stock")
def rebuild_stock(db: Session = Depends(get_db)):
    """Recompute every per-warehouse balance from the movement ledger.

    The balances are a cache of a sum the ledger already holds, so this is always
    safe to run and always produces the same answer — it is the repair for a
    figure somebody has reason to doubt, and the proof that the cache is only
    ever a cache."""
    return {"products_rebuilt": stock_loc.rebuild_all(db)}


# ---------------------------------------------------------------------------
#  warehouses
# ---------------------------------------------------------------------------
@router.get("/warehouses")
def list_warehouses(request: Request, db: Session = Depends(get_db)):
    q = db.query(models.Warehouse)
    allowed = _allotted(request)
    if allowed:
        q = q.filter(models.Warehouse.id.in_(list(allowed)))
    return [svc.warehouse_out(w) for w in q.order_by(models.Warehouse.name).all()]


@router.post("/warehouses")
def create_warehouse(body: WarehouseIn, db: Session = Depends(get_db)):
    name = _clean(body.name)
    if not name:
        raise HTTPException(400, "a warehouse needs a name")
    if db.query(models.Warehouse).filter(models.Warehouse.name == name).first():
        raise HTTPException(409, f"there is already a warehouse called “{name}”")
    w = models.Warehouse(name=name,
                         code=_clean(body.code) or svc._next_code(db, models.Warehouse, "WH"),
                         catalogue_id=_catalogue_id(db, body.catalogue_id),
                         active=True if body.active is None else bool(body.active))
    _apply_profile(w, body, db)
    # Filed under this company unless another was named, so a warehouse created
    # on a multi-business install is never left unattached — its document
    # numbering comes from its business, and an unfiled one has none.
    if w.business_id is None:
        from ..services import businesses as biz_svc
        w.business_id = biz_svc.resolve_id(db, None)
    db.add(w)
    db.commit()
    db.refresh(w)
    return svc.warehouse_out(w)


@router.patch("/warehouses/{wid}")
def update_warehouse(wid: int, body: WarehouseIn, db: Session = Depends(get_db)):
    w = db.get(models.Warehouse, wid)
    if not w:
        raise HTTPException(404, "warehouse not found")
    name = _clean(body.name)
    if name and name != w.name:
        if db.query(models.Warehouse).filter(models.Warehouse.name == name).first():
            raise HTTPException(409, f"there is already a warehouse called “{name}”")
        w.name = name
    if body.code is not None:
        w.code = _clean(body.code)
    _apply_profile(w, body, db)
    if body.catalogue_id is not None:
        # Moving a warehouse to another business line is refused once it holds
        # goods. Its products, their categories and their attributes were all
        # created under the old line; re-pointing the building would leave them
        # filed in a master its own screens no longer show, and would let the
        # next receipt merge a saree into a t-shirt's stock record.
        held = db.query(models.Product).filter(
            models.Product.catalogue_id == w.catalogue_id).join(
            models.StockBalance,
            models.StockBalance.product_id == models.Product.id).filter(
            models.StockBalance.warehouse_id == w.id,
            models.StockBalance.qty > 0).count()
        if held and body.catalogue_id != w.catalogue_id:
            raise HTTPException(400, f"“{w.name}” is holding {held} item(s) of its "
                                     f"current catalogue — clear the stock before "
                                     f"moving it to another line")
        w.catalogue_id = _catalogue_id(db, body.catalogue_id)
    if body.active is not None:
        w.active = bool(body.active)
    db.commit()
    db.refresh(w)
    return svc.warehouse_out(w)


@router.delete("/warehouses/{wid}")
def delete_warehouse(wid: int, db: Session = Depends(get_db)):
    """Remove a warehouse that was created by mistake.

    Refuses as soon as ANYTHING is filed against it. It used to check only for
    stores, which was the check that existed when a warehouse held nothing but
    stores. Six other tables now point at warehouses.id — balances, the movement
    ledger, GRNs, invoices, LR rows and dispatches — and foreign keys are
    enforced (see database.py), so deleting a store-less warehouse that held any
    of them raised an IntegrityError that reached the client as a bare 500. The
    router promised a polite refusal and delivered a stack trace.
    """
    w = db.get(models.Warehouse, wid)
    if not w:
        raise HTTPException(404, "warehouse not found")
    for label, count in (
        ("stock balance", db.query(models.StockBalance).filter(
            models.StockBalance.warehouse_id == wid).count()),
        ("stock movement", db.query(models.StockMovement).filter(
            models.StockMovement.warehouse_id == wid).count()),
        ("GRN", db.query(models.Purchase).filter(
            models.Purchase.warehouse_id == wid).count()),
        ("invoice", db.query(models.Document).filter(
            models.Document.warehouse_id == wid).count()),
        ("LR entry", db.query(models.LREntry).filter(
            models.LREntry.warehouse_id == wid).count()),
        ("dispatch", db.query(models.StockOutward).filter(
            (models.StockOutward.from_warehouse_id == wid)
            | (models.StockOutward.to_warehouse_id == wid)).count()),
    ):
        if count:
            raise HTTPException(409, f"{count} {label}(s) are filed against "
                                     f"“{w.name}” — switch it off instead, so its "
                                     f"history stays readable")
    n = db.query(models.Store).filter(models.Store.warehouse_id == wid).count()
    if n:
        raise HTTPException(409, f"{n} store(s) belong to this warehouse — move "
                                 f"them first, or switch it off instead")
    db.delete(w)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
#  stores
# ---------------------------------------------------------------------------
@router.get("/stores")
def list_stores(warehouse_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(models.Store)
    if warehouse_id:
        q = q.filter(models.Store.warehouse_id == warehouse_id)
    return [svc.store_out(s) for s in q.order_by(models.Store.name).all()]


@router.post("/stores")
def create_store(body: StoreIn, db: Session = Depends(get_db)):
    name = _clean(body.name)
    if not name:
        raise HTTPException(400, "a store needs a name")
    if name.upper() == svc.NONE_VALUE:
        raise HTTPException(400, "“NONE” means “keep it in the warehouse” — "
                                 "it cannot be the name of a store")
    if db.query(models.Store).filter(models.Store.name == name).first():
        raise HTTPException(409, f"there is already a store called “{name}”. Store "
                                 f"names are unique company-wide because the retail "
                                 f"shop matches branches by name")
    wid = body.warehouse_id
    if wid and not db.get(models.Warehouse, wid):
        raise HTTPException(404, "that warehouse does not exist")
    if not wid:
        wid = svc.ensure_default_warehouse(db).id
    s = models.Store(name=name, warehouse_id=wid,
                     code=_clean(body.code) or svc._next_code(db, models.Store, "ST"),
                     active=True if body.active is None else bool(body.active))
    _apply_profile(s, body, db)
    # Defaults to the supplying warehouse's company rather than to the install's
    # default: on a multi-business install the branch of a Silks warehouse is a
    # Silks branch, and a franchise is the case where somebody says otherwise.
    if s.business_id is None:
        parent = db.get(models.Warehouse, wid)
        s.business_id = parent.business_id if parent else None
    db.add(s)
    db.commit()
    db.refresh(s)
    # the dropdowns the rest of the app reads, and the shop's next sync
    svc.mirror_to_options(db)
    return svc.store_out(s)


@router.patch("/stores/{sid}")
def update_store(sid: int, body: StoreIn, db: Session = Depends(get_db)):
    s = db.get(models.Store, sid)
    if not s:
        raise HTTPException(404, "store not found")
    warning = None
    name = _clean(body.name)
    if name and name != s.name:
        if db.query(models.Store).filter(models.Store.name == name).first():
            raise HTTPException(409, f"there is already a store called “{name}”")
        warning = svc.rename_warning(s.name, name)
        s.name = name
    if body.code is not None:
        s.code = _clean(body.code)
    _apply_profile(s, body, db)
    if body.warehouse_id is not None:
        if not db.get(models.Warehouse, body.warehouse_id):
            raise HTTPException(404, "that warehouse does not exist")
        s.warehouse_id = body.warehouse_id
    if body.active is not None:
        s.active = bool(body.active)
    db.commit()
    db.refresh(s)
    svc.mirror_to_options(db)
    out = svc.store_out(s)
    if warning:
        out["warning"] = warning
    return out


@router.delete("/stores/{sid}")
def delete_store(sid: int, db: Session = Depends(get_db)):
    s = db.get(models.Store, sid)
    if not s:
        raise HTTPException(404, "store not found")
    n = db.query(models.PosTerminal).filter(models.PosTerminal.store_id == sid).count()
    if n:
        raise HTTPException(409, f"{n} POS terminal(s) belong to this store — "
                                 f"remove them first, or switch it off instead")
    # A dispatch names its destination as text, so this is a name check rather
    # than a foreign key. It is still the right refusal: deleting a store that
    # has taken deliveries leaves those transfers pointing at nothing.
    sent = db.query(models.StockOutward).filter(
        models.StockOutward.to_destination == s.name).count()
    if sent:
        raise HTTPException(409, f"{sent} stock transfer(s) were sent to “{s.name}” — "
                                 f"switch it off instead, so its history stays readable")
    db.delete(s)
    db.commit()
    svc.mirror_to_options(db)
    return {"ok": True}


# ---------------------------------------------------------------------------
#  POS terminals
# ---------------------------------------------------------------------------
@router.get("/terminals")
def list_terminals(store_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(models.PosTerminal)
    if store_id:
        q = q.filter(models.PosTerminal.store_id == store_id)
    return [svc.terminal_out(t) for t in q.order_by(models.PosTerminal.name).all()]


@router.post("/terminals")
def create_terminal(body: TerminalIn, db: Session = Depends(get_db)):
    name = _clean(body.name)
    if not name:
        raise HTTPException(400, "a terminal needs a name")
    if not body.store_id:
        raise HTTPException(400, "a terminal belongs to a store — say which")
    store = db.get(models.Store, body.store_id)
    if not store:
        raise HTTPException(404, "that store does not exist")
    dupe = db.query(models.PosTerminal).filter(
        models.PosTerminal.store_id == store.id,
        models.PosTerminal.name == name).first()
    if dupe:
        raise HTTPException(409, f"“{store.name}” already has a terminal called “{name}”")
    t = models.PosTerminal(name=name, store_id=store.id,
                           code=_clean(body.code) or svc._next_code(db, models.PosTerminal, "POS"),
                           active=True if body.active is None else bool(body.active))
    _apply_profile(t, body, db)
    if t.business_id is None:
        t.business_id = store.business_id
    db.add(t)
    db.commit()
    db.refresh(t)
    return svc.terminal_out(t)


@router.patch("/terminals/{tid}")
def update_terminal(tid: int, body: TerminalIn, db: Session = Depends(get_db)):
    t = db.get(models.PosTerminal, tid)
    if not t:
        raise HTTPException(404, "terminal not found")
    name = _clean(body.name)
    store_id = body.store_id or t.store_id
    if name and (name != t.name or store_id != t.store_id):
        dupe = db.query(models.PosTerminal).filter(
            models.PosTerminal.store_id == store_id,
            models.PosTerminal.name == name,
            models.PosTerminal.id != tid).first()
        if dupe:
            raise HTTPException(409, "that store already has a terminal with this name")
    if name:
        t.name = name
    if body.store_id is not None:
        if not db.get(models.Store, body.store_id):
            raise HTTPException(404, "that store does not exist")
        t.store_id = body.store_id
    if body.code is not None:
        t.code = _clean(body.code)
    _apply_profile(t, body, db)
    if body.active is not None:
        t.active = bool(body.active)
    db.commit()
    db.refresh(t)
    return svc.terminal_out(t)


@router.delete("/terminals/{tid}")
def delete_terminal(tid: int, db: Session = Depends(get_db)):
    t = db.get(models.PosTerminal, tid)
    if not t:
        raise HTTPException(404, "terminal not found")
    db.delete(t)
    db.commit()
    return {"ok": True}
