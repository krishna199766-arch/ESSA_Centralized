from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import returns as svc
from ..services import stock_view

router = APIRouter(prefix="/api/returns", tags=["purchase-returns"])


class SetLines(BaseModel):
    line_qtys: Dict[int, float]        # return_line_id -> qty


class PostReturn(BaseModel):
    reason: Optional[str] = None
    date: Optional[str] = None
    line_qtys: Optional[Dict[int, float]] = None
    # deliberately no rate/amount: a debit note is valued from the GRN, never
    # from what a client sends (see services/returns.py)


def _line_out(l, db: Session, purchase=None):
    line, split = svc.source_of(db, l)
    product = l.product
    limits = svc.returnable(db, l.ret, l)
    return {
        "id": l.id, "product_id": l.product_id, "barcode": l.barcode,
        "description": l.description, "hsn": l.hsn, "uom": l.uom,
        "qty": l.qty, "amount": l.amount,
        "on_hand": product.stock_qty if product else None,
        "purchased_qty": limits["purchased"],
        "already_returned": limits["already_returned"],
        "available_qty": limits["available"],
        # --- valuation: the received price, and where it came from ---
        "rate": l.rate,                       # kept: existing clients read this
        "grn_rate": l.rate,                   # named for what it actually is
        "cost_basis": "grn",
        "cost_source": svc.cost_source(line, split, product),
        # shown for identification only — a garment is found by its MRP tag. The
        # debit note is NOT valued on either of these; the names say so.
        "sale_price_reference": product.sale_price if product else None,
        "mrp_reference": product.mrp if product else None,
        # the full record, pinned to the invoice being returned against, so the
        # batch shown is that receipt and not merely the product's latest one
        "product": stock_view.product_card(db, product, purchase=purchase)
                   if product else None,
    }


def _out(r, db: Session = None, with_lines=False):
    d = {"id": r.id, "code": r.code, "supplier_id": r.supplier_id,
         "supplier_name": r.supplier.name if r.supplier else None,
         "purchase_id": r.purchase_id, "invoice_number": r.invoice_number,
         "grn_no": r.purchase.grn_no if r.purchase else None,
         "invoice_date": r.purchase.invoice_date if r.purchase else None,
         "date": r.date, "reason": r.reason, "taxable_total": r.taxable_total,
         "tax_total": r.tax_total, "total": r.total, "status": r.status,
         # stated on the document itself: this debit note is priced at what we
         # paid the supplier, not at what we sell the goods for
         "cost_basis": "grn",
         "cost_basis_label": "Purchase / GRN cost",
         "created_at": r.created_at.isoformat() if r.created_at else None,
         "posted_at": r.posted_at.isoformat() if r.posted_at else None}
    if with_lines:
        d["lines"] = [_line_out(l, db, purchase=r.purchase) for l in r.lines]
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
    return _out(ret, db, with_lines=True)


@router.get("/{rid}")
def get_return(rid: int, db: Session = Depends(get_db)):
    r = db.get(models.PurchaseReturn, rid)
    if not r:
        raise HTTPException(404, "return not found")
    if r.status != "posted":
        # a draft is re-valued from the GRN every time it is opened, so the
        # figure on screen is the figure it would post at
        svc.apply_grn_rates(db, r)
        db.commit()
    return _out(r, db, with_lines=True)


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
