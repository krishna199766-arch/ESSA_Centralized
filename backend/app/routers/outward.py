from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import outward as svc
from ..services import stock_view

router = APIRouter(prefix="/api/outward", tags=["stock-outward"])


class OutwardLineIn(BaseModel):
    product_id: Optional[int] = None
    barcode: Optional[str] = None
    qty: float
    accepted_qty: Optional[float] = None


class OutwardIn(BaseModel):
    date: Optional[str] = None
    to_destination: Optional[str] = None
    packed_by: Optional[str] = None
    received_by: Optional[str] = None
    from_location: Optional[str] = "WAREHOUSE"
    lines: List[OutwardLineIn] = []


class ReceiveIn(BaseModel):
    """The Stock Inward side: who took the goods in, and how many of each line
    they accepted. Lines left out are accepted in full."""
    received_by: Optional[str] = None
    date: Optional[str] = None
    accepted: Optional[Dict[int, float]] = None      # outward_line_id -> qty


def _line_out(l, db: Session):
    prod = l.product
    return {
        "id": l.id, "product_id": l.product_id, "barcode": l.barcode,
        "description": l.description, "qty": l.qty,
        "accepted_qty": l.accepted_qty, "short_qty": l.short_qty,
        "rate": l.rate, "stock_on_hand": prod.stock_qty if prod else None,
        "value": round(float(l.qty or 0) * float(l.rate or 0), 2),
        # The whole product record — QR, name, size, colour, batch and the rest.
        # A dispatch or an acceptance is someone matching a row against a garment
        # in their hand; a barcode and a description cannot settle that, because
        # four sizes of one style share both.
        "product": stock_view.product_card(db, prod) if prod else None,
    }


def _out(o, db: Session = None, with_lines=False):
    d = {"id": o.id, "code": o.code, "date": o.date, "to_destination": o.to_destination,
         "from_company": o.from_company, "from_location": o.from_location,
         "packed_by": o.packed_by, "received_by": o.received_by,
         "received_date": o.received_date, "status": o.status,
         "total_qty": o.total_qty, "accepted_qty": o.total_accepted,
         "shortfall": o.shortfall, "line_count": len(o.lines),
         "created_at": o.created_at.isoformat() if o.created_at else None,
         "posted_at": o.posted_at.isoformat() if o.posted_at else None,
         "received_at": o.received_at.isoformat() if o.received_at else None}
    if with_lines:
        d["lines"] = [_line_out(l, db) for l in o.lines]
    return d


def _get(oid: int, db: Session):
    o = db.get(models.StockOutward, oid)
    if not o:
        raise HTTPException(404, "outward not found")
    return o


@router.get("")
def list_outwards(status: str = "all", db: Session = Depends(get_db)):
    """`status` filters the list: draft | posted | received. 'posted' is what the
    Stock Inward screen wants — dispatched, not yet accepted anywhere."""
    q = db.query(models.StockOutward)
    if status and status != "all":
        q = q.filter(models.StockOutward.status == status)
    return [_out(o) for o in q.order_by(models.StockOutward.id.desc()).all()]


@router.post("")
def create_outward(body: OutwardIn, db: Session = Depends(get_db)):
    o = svc.create_outward(db, body.model_dump())
    db.commit(); db.refresh(o)
    return _out(o, db, with_lines=True)


@router.get("/{oid}")
def get_outward(oid: int, db: Session = Depends(get_db)):
    return _out(_get(oid, db), db, with_lines=True)


@router.post("/{oid}/post")
def post_outward(oid: int, allow_negative: bool = False, db: Session = Depends(get_db)):
    o = _get(oid, db)
    res = svc.post_outward(db, o, allow_negative=allow_negative)
    if not res.get("ok"):
        db.rollback()
        raise HTTPException(400, res)
    db.commit()
    return res


@router.post("/{oid}/receive")
def receive_outward(oid: int, body: ReceiveIn, db: Session = Depends(get_db)):
    """Stock Inward — accept a dispatched transfer at the destination.

    Records the accepted quantity per line against the same document, so a short
    delivery is the difference between two columns rather than a second piece of
    paper nobody reconciles."""
    o = _get(oid, db)
    res = svc.receive_outward(db, o, accepted=body.accepted,
                              received_by=body.received_by, date=body.date)
    if not res.get("ok"):
        db.rollback()
        raise HTTPException(400, res.get("error"))
    db.commit()
    return res


@router.get("/{oid}/verify")
def verify_scan(oid: int, code: str, db: Session = Depends(get_db)):
    """Check a scanned garment against this transfer — is it on the note, and
    which line? Used by both ends: packing it out and counting it in."""
    o = _get(oid, db)
    res = svc.verify_code(db, o, code)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error"))
    product = db.get(models.Product, res["product_id"])
    res["product"] = stock_view.product_card(db, product)
    return res
