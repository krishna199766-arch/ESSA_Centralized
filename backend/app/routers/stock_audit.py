"""Stock Audit — counting the shelf against the books.

The scan endpoint is the whole module in one call: read a tag, say what the
system holds, record the finding, and hand back the running tally. It is one
round trip because it is used standing at a rack with a phone, and a screen that
had to make three calls to answer one scan would feel broken on warehouse wifi
long before it was.

Nothing here moves stock. See services/stock_audit for why that is a rule rather
than an omission.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import scope, stock_audit as audit

router = APIRouter(prefix="/api/stock-audit", tags=["stock-audit"])


class ScanIn(BaseModel):
    code: str


class OpenIn(BaseModel):
    note: Optional[str] = None


def _who(request: Request):
    """Who is counting. Set on the request by the auth middleware as a dict — see
    security.auth_middleware and the same read in routers/users — so a scan is
    attributed without the phone having to say who it is. A warehouse handset is
    shared and put down; asking it to name the counter would credit every count
    to whoever was last left signed in anyway."""
    return (getattr(request.state, "user", None) or {}).get("username")


def _session_or_404(db, session_id):
    row = db.get(models.AuditSession, session_id)
    if not row:
        raise HTTPException(404, "audit session not found")
    return row


@router.get("/current")
def current(request: Request, db: Session = Depends(get_db),
            wid: Optional[int] = Depends(scope.current)):
    """The count in progress in this warehouse, or null.

    What the phone asks on opening the screen: a counter who put the handset down
    mid-rack gets their session and their tally back, rather than a blank screen
    that quietly starts a second count of the same shelves.
    """
    # Resolved the same way `open` does, so the phone — which sends no warehouse
    # header — finds the count it started rather than a blank screen that would
    # let it begin a second one over the same shelves.
    if not wid:
        ids = [w.id for w in db.query(models.Warehouse).limit(2).all()]
        wid = ids[0] if len(ids) == 1 else None
    row = audit.current_session(db, wid) if wid else None
    return audit.session_out(db, row) if row else None


def _counting_warehouse(db, wid):
    """Which building is being counted.

    The phone does not send a warehouse header — it never has, and every other
    screen on it works company-wide quite correctly. A COUNT cannot: somebody
    physically walked one building's racks, and a count that did not know which
    is a count of nothing.

    So a blank is resolved, but only when the answer is not a guess. One
    warehouse on file means there IS only one and adopting it states what is
    already true. Two or more and it refuses, because silently picking the first
    would file Erode's count against Karur and nothing on the screen would say
    so — the kind of wrong that is only found months later, if ever.
    """
    if wid:
        return wid
    ids = [w.id for w in db.query(models.Warehouse).limit(2).all()]
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise HTTPException(400, "There are no warehouses yet — add one first.")
    raise HTTPException(
        400, "Say which warehouse is being counted — this company has more than "
             "one, and a count belongs to the building whose shelves were walked.")


@router.post("/open")
def open_session(body: OpenIn, request: Request, db: Session = Depends(get_db),
                 wid: Optional[int] = Depends(scope.current)):
    """Start counting — or hand back the count already open here."""
    wid = _counting_warehouse(db, wid)
    row = audit.open_session(db, wid, by=_who(request), note=body.note)
    db.commit()
    db.refresh(row)
    return audit.session_out(db, row)


@router.post("/{session_id}/scan")
def scan(session_id: int, body: ScanIn, request: Request,
         db: Session = Depends(get_db)):
    """One tag. Returns the finding, whether it was already counted, and the tally."""
    session = _session_or_404(db, session_id)
    try:
        res = audit.scan(db, session, body.code, by=_who(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(res["scan"])
    return {"scan": audit.scan_out(res["scan"]), "duplicate": res["duplicate"],
            "totals": audit.totals(db, session)}


@router.post("/{session_id}/close")
def close(session_id: int, request: Request, db: Session = Depends(get_db)):
    session = _session_or_404(db, session_id)
    audit.close_session(db, session, by=_who(request))
    db.commit()
    db.refresh(session)
    return audit.session_out(db, session)


@router.get("/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db)):
    return audit.session_out(db, _session_or_404(db, session_id))


@router.delete("/{session_id}/scans/{scan_id}")
def remove_scan(session_id: int, scan_id: int, db: Session = Depends(get_db)):
    """Take one reading back out of an open count.

    A miss-scan happens — a neighbouring label, a tag read twice from a box that
    was already counted — and the alternative to removing it is a count somebody
    knows is wrong and cannot correct, which is a count nobody trusts. Only while
    the session is open: a closed count is a record.
    """
    session = _session_or_404(db, session_id)
    if session.status != "open":
        raise HTTPException(400, f"{session.code} is closed — its readings stand")
    row = db.get(models.AuditScan, scan_id)
    if not row or row.session_id != session.id:
        raise HTTPException(404, "that reading is not in this count")
    db.delete(row)
    db.commit()
    return {"ok": True, "totals": audit.totals(db, session)}


@router.get("")
def list_sessions(limit: int = 50, db: Session = Depends(get_db),
                  wid: Optional[int] = Depends(scope.current)):
    """Past counts of this warehouse, newest first."""
    q = scope.audit_sessions(db.query(models.AuditSession), wid)
    rows = q.order_by(models.AuditSession.id.desc()).limit(
        max(1, min(limit, 500))).all()
    return [audit.session_out(db, r, with_scans=False) for r in rows]
