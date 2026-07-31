from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import returns as svc

router = APIRouter(prefix="/api/returns", tags=["purchase-returns"])


class SetLines(BaseModel):
    line_qtys: Dict[int, float]        # return_line_id -> qty


class PostReturn(BaseModel):
    reason: Optional[str] = None
    date: Optional[str] = None
    line_qtys: Optional[Dict[int, float]] = None


def _line_out(l):
    return {"id": l.id, "product_id": l.product_id, "barcode": l.barcode,
            "description": l.description, "hsn": l.hsn, "qty": l.qty,
            "rate": l.rate, "amount": l.amount,
            "on_hand": l.product.stock_qty if l.product else None,
            "purchased_qty": None}


def _out(r, with_lines=False):
    d = {"id": r.id, "code": r.code, "supplier_id": r.supplier_id,
         "supplier_name": r.supplier.name if r.supplier else None,
         "purchase_id": r.purchase_id, "invoice_number": r.invoice_number,
         "date": r.date, "reason": r.reason, "taxable_total": r.taxable_total,
         "tax_total": r.tax_total, "total": r.total, "status": r.status,
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "posted_at": r.posted_at.isoformat() if r.posted_at else None}
    if with_lines:
        d["lines"] = [_line_out(l) for l in r.lines]
    return d


@router.get("")
def list_returns(db: Session = Depends(get_db)):
    return [_out(r) for r in db.query(models.PurchaseReturn).order_by(models.PurchaseReturn.id.desc()).all()]


@router.post("/from-purchase/{purchase_id}")
def build_from_purchase(purchase_id: int, db: Session = Depends(get_db)):
    p = db.get(models.Purchase, purchase_id)
    if not p:
        raise HTTPException(404, "purchase not found")
    if p.status != "posted":
        raise HTTPException(400, "reference invoice must be a posted GRN")
    ret = svc.build_from_purchase(db, p)
    db.commit(); db.refresh(ret)
    return _out(ret, with_lines=True)


@router.get("/{rid}")
def get_return(rid: int, db: Session = Depends(get_db)):
    r = db.get(models.PurchaseReturn, rid)
    if not r:
        raise HTTPException(404, "return not found")
    return _out(r, with_lines=True)


@router.post("/{rid}/post")
def post_return(rid: int, body: PostReturn, db: Session = Depends(get_db)):
    r = db.get(models.PurchaseReturn, rid)
    if not r:
        raise HTTPException(404, "return not found")
    if body.line_qtys:
        svc.set_lines(db, r, body.line_qtys)
    res = svc.post(db, r, reason=body.reason, date=body.date)
    if not res.get("ok"):
        db.rollback()
        raise HTTPException(400, res.get("error"))
    db.commit()
    return res
