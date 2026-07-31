import os
import hashlib
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..config import UPLOAD_DIR
from ..services import lr as lr_svc
from ..services import lr_link
from ..services import masters as masters_svc

router = APIRouter(prefix="/api/lr", tags=["lr-entry"])


class SaveLR(BaseModel):
    document_id: Optional[int] = None
    rows: List[Dict[str, Any]]


class SettleFreight(BaseModel):
    """Freight settlement, corrected or completed when the lorry actually
    delivers — mode and amount."""
    paid_topay: Optional[str] = None          # TOPAY | PAID | NO
    freight_amount: Optional[float] = None
    cash_cheque: Optional[str] = None         # CASH | CHEQUE | NO


class ReceiveConsignment(BaseModel):
    """Sent by the warehouse phone app when the packages are taken in."""
    received_by: str


@router.post("/extract")
async def extract(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload an LR register image/PDF → OCR/vision returns the consignment rows
    (no manual entry). Also stores the source document."""
    raw = await file.read()
    h = hashlib.sha256(raw).hexdigest()
    ext = os.path.splitext(file.filename)[1] or ".bin"
    stored = os.path.join(UPLOAD_DIR, f"{h[:16]}{ext}")
    if not os.path.exists(stored):
        with open(stored, "wb") as f:
            f.write(raw)
        os.chmod(stored, 0o644)
    doc = models.Document(filename=file.filename, stored_path=stored, content_hash=h,
                          mime=file.content_type, status="uploaded", document_type="lr_register")
    db.add(doc)
    db.commit()
    db.refresh(doc)
    res = lr_svc.extract_lr(stored)
    # flag rows that already exist in the DB (or repeat within this upload)
    rows, dup_summary = lr_link.annotate_duplicates(db, res.get("rows", []))
    res["rows"] = rows
    return {"document_id": doc.id, "duplicates": dup_summary, **res}


@router.post("/save")
def save(body: SaveLR, db: Session = Depends(get_db)):
    """Persist the (reviewed) LR rows and auto-create transport + supplier
    masters. Duplicates (already in the DB, or repeated within this batch) are
    skipped authoritatively on the server, even if the client didn't filter
    them, so a re-uploaded register never creates duplicate records."""
    saved = 0
    skipped = 0
    existing = lr_link._index_existing(db)
    seen = {}
    for r in body.rows:
        if not any(r.get(k) for k in ("lr_no", "supplier_name", "inv_no")):
            continue
        key = lr_link.dup_key(r)
        if key is not None:
            candidates = existing.get(key, []) + seen.get(key, [])
            if lr_link.is_exact_duplicate(r, candidates):   # skip only exact twins
                skipped += 1
                continue
            seen.setdefault(key, []).append(r)
        db.add(models.LREntry(
            document_id=body.document_id,
            recv_date=r.get("recv_date"), transport=r.get("transport"),
            bundle=r.get("bundle"), lr_no=r.get("lr_no"), lr_date=r.get("lr_date"),
            supplier_name=r.get("supplier_name"), inv_no=r.get("inv_no"),
            inv_date=r.get("inv_date"), qty=r.get("qty"), amount=r.get("amount"),
            paid_topay=r.get("paid_topay"), freight_amount=r.get("freight_amount"),
            cash_cheque=r.get("cash_cheque"),
            item=r.get("item")))
        # received_by is intentionally not taken from the imported page — the
        # warehouse sets it from the phone app when the goods actually arrive
        masters_svc.get_or_create_transport(db, r.get("transport"))
        # register the supplier name in the supplier master if unseen
        name = (r.get("supplier_name") or "").strip()
        if name and not db.query(models.Supplier).filter(models.Supplier.name == name).first():
            db.add(models.Supplier(name=name))
        saved += 1
    db.commit()
    return {"ok": True, "saved": saved, "skipped_duplicates": skipped}


def _row_out(e: models.LREntry):
    return {"id": e.id, "recv_date": e.recv_date, "transport": e.transport,
            "bundle": e.bundle, "lr_no": e.lr_no, "lr_date": e.lr_date,
            "supplier_name": e.supplier_name, "inv_no": e.inv_no, "inv_date": e.inv_date,
            "qty": e.qty, "amount": e.amount, "paid_topay": e.paid_topay,
            "freight_amount": e.freight_amount, "cash_cheque": e.cash_cheque,
            "item": e.item, "received_by": e.received_by,
            "matched": bool(e.matched), "invoice_document_id": e.invoice_document_id,
            "mismatches": e.mismatches or []}


@router.get("")
def list_lr(received: str = "all", limit: int = 500, db: Session = Depends(get_db)):
    """The register, newest first. `received=pending|received` filters by whether
    the warehouse has taken the consignment in — that's what the phone app lists,
    and it keeps the payload small as the register grows."""
    q = db.query(models.LREntry)
    if received == "pending":
        q = q.filter((models.LREntry.received_by == None)          # noqa: E711
                     | (models.LREntry.received_by == ""))
    elif received == "received":
        q = q.filter(models.LREntry.received_by != None,           # noqa: E711
                     models.LREntry.received_by != "")
    rows = q.order_by(models.LREntry.id.desc()).limit(max(1, min(limit, 2000))).all()
    return [_row_out(e) for e in rows]


@router.patch("/{entry_id}")
def settle_freight(entry_id: int, body: SettleFreight, db: Session = Depends(get_db)):
    """Record how the freight settled on delivery: Paid/ToPay, amount, cash or
    cheque. Only the fields sent are touched, so the extracted register values
    stay put."""
    e = db.get(models.LREntry, entry_id)
    if not e:
        raise HTTPException(404, "LR entry not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(e, k, v)
    db.commit()
    db.refresh(e)
    return _row_out(e)


@router.get("/receivers")
def receivers(db: Session = Depends(get_db)):
    """Names that have received consignments before, so the phone app can offer
    them instead of making everyone type a colleague's name from scratch."""
    rows = db.query(models.LREntry.received_by).filter(
        models.LREntry.received_by != None,                        # noqa: E711
        models.LREntry.received_by != "").distinct().all()
    return sorted({(r[0] or "").strip() for r in rows} - {""}, key=str.lower)


@router.post("/{entry_id}/receive")
def receive_consignment(entry_id: int, body: ReceiveConsignment,
                        db: Session = Depends(get_db)):
    """Record who took delivery of a consignment — sent by the warehouse phone app.

    This is the only way `received_by` is set: the register page doesn't carry it
    and the desktop doesn't type it, because the people who handle the packages are
    the ones who know who took them."""
    e = db.get(models.LREntry, entry_id)
    if not e:
        raise HTTPException(404, "LR entry not found")
    name = (body.received_by or "").strip()
    if not name:
        raise HTTPException(400, "who received it? a name is required")
    e.received_by = name
    db.commit()
    db.refresh(e)
    return _row_out(e)


@router.delete("/{entry_id}/receive")
def unreceive_consignment(entry_id: int, db: Session = Depends(get_db)):
    """Undo a receipt taken by mistake — the row goes back to pending. Kept
    reversible on purpose: a mis-tap on the floor shouldn't need a desk to fix."""
    e = db.get(models.LREntry, entry_id)
    if not e:
        raise HTTPException(404, "LR entry not found")
    e.received_by = None
    db.commit()
    db.refresh(e)
    return _row_out(e)
