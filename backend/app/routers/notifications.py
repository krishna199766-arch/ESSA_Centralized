"""The notification centre — the bell on the desktop, the tab on the phone.

Read from the same place both ends: one feed, one acknowledgement. Marking a
notice read at the counter clears it in the office too, because it is the same
queue and it has been seen.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..services import notifications as svc

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ReadIn(BaseModel):
    keys: List[str] = []
    by: Optional[str] = None


class MuteIn(BaseModel):
    key: str
    muted: bool = True
    by: Optional[str] = None


class RecipientIn(BaseModel):
    name: str
    mobile: Optional[str] = None
    role: Optional[str] = None
    levels: Optional[List[str]] = None
    active: Optional[bool] = True


@router.get("")
def feed(db: Session = Depends(get_db)):
    """Every open queue, ordered unread-first and then by how much it matters."""
    return svc.feed(db)


@router.get("/count")
def count(db: Session = Depends(get_db)):
    """Just the badge. Polled by the header, so it does the same read but sends
    back four numbers instead of the whole feed."""
    return svc.feed(db)["counts"]


@router.post("/read")
def read(body: ReadIn, db: Session = Depends(get_db)):
    return svc.mark_read(db, body.keys, by=body.by)


@router.post("/read-all")
def read_all(body: ReadIn, db: Session = Depends(get_db)):
    return svc.mark_all(db, by=body.by)


@router.post("/mute")
def mute(body: MuteIn, db: Session = Depends(get_db)):
    """Silence a queue this warehouse does not work by. It stays out of the list
    until it is unmuted — whatever its count does."""
    try:
        return svc.set_muted(db, body.key, body.muted, by=body.by)
    except KeyError:
        raise HTTPException(404, f"No notification rule called {body.key}")


@router.get("/muted")
def muted(db: Session = Depends(get_db)):
    return svc.muted_keys(db)


# ---- who is meant to be watching ------------------------------------------

@router.get("/recipients")
def list_recipients(db: Session = Depends(get_db)):
    rows = db.query(models.NotificationRecipient).order_by(
        models.NotificationRecipient.id).all()
    return [svc.recipient_out(r) for r in rows]


@router.post("/recipients")
def add_recipient(body: RecipientIn, db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "A name is required")
    r = models.NotificationRecipient(
        name=name, mobile=svc.clean_mobile(body.mobile), role=(body.role or "").strip() or None,
        levels=[l for l in (body.levels or svc.LEVELS) if l in svc.LEVELS] or svc.LEVELS,
        active=True if body.active is None else bool(body.active))
    db.add(r)
    db.commit()
    db.refresh(r)
    return svc.recipient_out(r)


@router.patch("/recipients/{rid}")
def edit_recipient(rid: int, body: RecipientIn, db: Session = Depends(get_db)):
    r = db.get(models.NotificationRecipient, rid)
    if not r:
        raise HTTPException(404, "Recipient not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        r.name = (data["name"] or "").strip() or r.name
    if "mobile" in data:
        r.mobile = svc.clean_mobile(data["mobile"])
    if "role" in data:
        r.role = (data["role"] or "").strip() or None
    if "levels" in data and data["levels"] is not None:
        r.levels = [l for l in data["levels"] if l in svc.LEVELS]
    if "active" in data and data["active"] is not None:
        r.active = bool(data["active"])
    db.commit()
    db.refresh(r)
    return svc.recipient_out(r)


@router.delete("/recipients/{rid}")
def delete_recipient(rid: int, db: Session = Depends(get_db)):
    r = db.get(models.NotificationRecipient, rid)
    if not r:
        raise HTTPException(404, "Recipient not found")
    db.delete(r)
    db.commit()
    return {"ok": True}
