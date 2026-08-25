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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..services import locations as svc

router = APIRouter(prefix="/api/locations", tags=["locations"])


class WarehouseIn(BaseModel):
    name: str
    code: Optional[str] = None
    address: Optional[str] = None
    active: Optional[bool] = None


class StoreIn(BaseModel):
    name: str
    warehouse_id: Optional[int] = None
    code: Optional[str] = None
    address: Optional[str] = None
    active: Optional[bool] = None


class TerminalIn(BaseModel):
    name: str
    store_id: Optional[int] = None
    code: Optional[str] = None
    active: Optional[bool] = None


def _clean(s: Optional[str]) -> Optional[str]:
    s = (s or "").strip()
    return s or None


# ---------------------------------------------------------------------------
#  the tree
# ---------------------------------------------------------------------------
@router.get("")
def get_tree(db: Session = Depends(get_db)):
    """Every warehouse, its stores and their terminals — the Locations screen.

    Runs the backfill/mirror first so a database that predates these tables
    answers with its existing branches already turned into stores, rather than
    looking empty until somebody presses something.
    """
    svc.sync(db)
    return {"warehouses": svc.tree(db)}


@router.post("/sync")
def resync(db: Session = Depends(get_db)):
    """Re-run the backfill and re-mirror the dropdown. Idempotent."""
    return svc.sync(db)


# ---------------------------------------------------------------------------
#  warehouses
# ---------------------------------------------------------------------------
@router.get("/warehouses")
def list_warehouses(db: Session = Depends(get_db)):
    return [svc.warehouse_out(w) for w in
            db.query(models.Warehouse).order_by(models.Warehouse.name).all()]


@router.post("/warehouses")
def create_warehouse(body: WarehouseIn, db: Session = Depends(get_db)):
    name = _clean(body.name)
    if not name:
        raise HTTPException(400, "a warehouse needs a name")
    if db.query(models.Warehouse).filter(models.Warehouse.name == name).first():
        raise HTTPException(409, f"there is already a warehouse called “{name}”")
    w = models.Warehouse(name=name, address=_clean(body.address),
                         code=_clean(body.code) or svc._next_code(db, models.Warehouse, "WH"),
                         active=True if body.active is None else bool(body.active))
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
    if body.address is not None:
        w.address = _clean(body.address)
    if body.active is not None:
        w.active = bool(body.active)
    db.commit()
    db.refresh(w)
    return svc.warehouse_out(w)


@router.delete("/warehouses/{wid}")
def delete_warehouse(wid: int, db: Session = Depends(get_db)):
    w = db.get(models.Warehouse, wid)
    if not w:
        raise HTTPException(404, "warehouse not found")
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
    s = models.Store(name=name, warehouse_id=wid, address=_clean(body.address),
                     code=_clean(body.code) or svc._next_code(db, models.Store, "ST"),
                     active=True if body.active is None else bool(body.active))
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
    if body.address is not None:
        s.address = _clean(body.address)
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
