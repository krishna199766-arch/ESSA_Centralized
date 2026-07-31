from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import outward as svc

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


def _line_out(l):
    prod = l.product
    return {"id": l.id, "product_id": l.product_id, "barcode": l.barcode,
            "description": l.description, "qty": l.qty, "accepted_qty": l.accepted_qty,
            "rate": l.rate, "stock_on_hand": prod.stock_qty if prod else None}


def _out(o, with_lines=False):
    d = {"id": o.id, "code": o.code, "date": o.date, "to_destination": o.to_destination,
         "from_location": o.from_location, "packed_by": o.packed_by,
         "received_by": o.received_by, "status": o.status, "total_qty": o.total_qty,
         "line_count": len(o.lines),
         "created_at": o.created_at.isoformat() if o.created_at else None,
         "posted_at": o.posted_at.isoformat() if o.posted_at else None}
    if with_lines:
        d["lines"] = [_line_out(l) for l in o.lines]
    return d


@router.get("")
def list_outwards(db: Session = Depends(get_db)):
    return [_out(o) for o in db.query(models.StockOutward).order_by(models.StockOutward.id.desc()).all()]


@router.post("")
def create_outward(body: OutwardIn, db: Session = Depends(get_db)):
    o = svc.create_outward(db, body.model_dump())
    db.commit(); db.refresh(o)
    return _out(o, with_lines=True)


@router.get("/{oid}")
def get_outward(oid: int, db: Session = Depends(get_db)):
    o = db.get(models.StockOutward, oid)
    if not o:
        raise HTTPException(404, "outward not found")
    return _out(o, with_lines=True)


@router.post("/{oid}/post")
def post_outward(oid: int, allow_negative: bool = False, db: Session = Depends(get_db)):
    o = db.get(models.StockOutward, oid)
    if not o:
        raise HTTPException(404, "outward not found")
    res = svc.post_outward(db, o, allow_negative=allow_negative)
    if not res.get("ok"):
        db.rollback()
        raise HTTPException(400, res)
    db.commit()
    return res
