from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import inventory as inv
from ..services import barcode_svc

router = APIRouter(prefix="/api/purchases", tags=["purchases"])


class SplitRows(BaseModel):
    """Attribute breakdown of one billed line. An empty list clears it."""
    rows: List[Dict[str, Any]]


class ScanCode(BaseModel):
    """A QR payload / barcode / SKU scanned to pin a line (or one of its variants)
    to an existing inventory product."""
    code: str
    split_id: Optional[int] = None


class LineEdit(BaseModel):
    """What can be set on a GRN line before posting. `category` decides the
    product's category master mapping instead of leaving it to auto-classification.
    Empty string clears it (back to auto)."""
    category: Optional[str] = None


def _split_out(s: models.PurchaseLineSplit):
    p = s.product
    d = {a: getattr(s, a) for a in inv.SPLIT_ATTRS}
    d.update({
        "id": s.id, "category": s.category,
        "qty": s.qty, "rate": s.effective_rate, "own_rate": s.rate,
        "mrp": s.mrp, "sale_price": s.sale_price, "sale_discount_pct": s.sale_discount_pct,
        "amount": s.amount, "code": s.code, "label": s.variant_label,
        "product_id": s.product_id, "is_new_product": s.is_new_product,
        "product_sku": p.sku if p else None,
        "product_barcode": p.barcode if p else None,
        "stock_after": p.stock_qty if p else None,
        # whether the warehouse has since inspected this item on the phone. The
        # breakdown supplies what the invoice implied (size, colour); detailing is
        # where fit, pattern, material and pricing come from someone holding it.
        "product_detailed": bool(p.detailed) if p else None,
    })
    return d


def _line_out(l: models.PurchaseLine, db: Session = None):
    st = inv.split_status(l)
    # what the description would map to, so the grid can show (and one-click accept)
    # the mapping instead of letting products land "unmapped" for someone to fix
    suggestion = None
    if db is not None and not l.category:
        from ..services import categorize
        s = categorize.suggest(db, l.description, limit=4)
        # `via` lets the screen say WHY: "rules" is the engine's own reading,
        # "alias" is a mapping someone already taught it for this wording
        suggestion = {"best": (s["best"] or {}).get("name"), "confident": s["confident"],
                      "via": s.get("via"), "candidates": [c["name"] for c in s["candidates"]]}
    return {
        "id": l.id, "product_id": l.product_id, "barcode": l.barcode,
        "description": l.description, "hsn": l.hsn, "qty": l.qty, "uom": l.uom,
        "rate": l.rate, "amount": l.amount, "is_new_product": l.is_new_product,
        "category": l.category, "category_suggestion": suggestion,
        "product_category": l.product.category if l.product else None,
        "product_sku": l.product.sku if l.product else None,
        "product_detailed": bool(l.product.detailed) if l.product else None,
        # The bundle itself is SPLIT: it is what the supplier billed, and it never
        # becomes stock — the rows below it do. Said explicitly rather than left to
        # each client to infer from a non-empty list, because "this row does not
        # move stock" is exactly the thing a receiving screen must not get wrong.
        "is_split": l.is_split,
        # the QR carries the whole product record; the code itself is the identity
        "qr_code": (l.product.barcode or l.product.sku) if l.product else None,
        "stock_after": l.product.stock_qty if l.product else None,
        "splits": [_split_out(s) for s in l.splits],
        "split_qty": st["split_qty"], "split_remainder": st["remainder"],
        "split_balanced": st["balanced"],
    }


def _purchase_out(p: models.Purchase, with_lines=False, db: Session = None):
    d = {
        "id": p.id, "document_id": p.document_id, "supplier_id": p.supplier_id,
        "supplier_name": p.supplier.name if p.supplier else None,
        "grn_no": p.grn_no, "invoice_number": p.invoice_number,
        "invoice_date": p.invoice_date, "taxable_total": p.taxable_total,
        "tax_total": p.tax_total, "grand_total": p.grand_total,
        "status": p.status, "line_count": len(p.lines),
        # a split line creates one product per size, not one for the line
        "new_products": (sum(1 for l in p.lines if not l.is_split and l.is_new_product)
                         + sum(1 for l in p.lines for s in l.splits if not s.product_id)),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "posted_at": p.posted_at.isoformat() if p.posted_at else None,
    }
    # Once posted, the receipt becomes a worklist: every item it created still has
    # to be looked at and detailed on the phone. Counting it here lets the GRN list
    # say so without opening each one.
    if p.status == "posted":
        holders = [h for l in p.lines for h in (l.splits if l.is_split else [l])]
        prods = [h.product for h in holders if h.product]
        d["items"] = len(holders)
        d["items_pending_detail"] = sum(1 for x in prods if not x.detailed)
    if with_lines:
        # category suggestions are only useful while the GRN can still be edited
        d["lines"] = [_line_out(l, db if p.status != "posted" else None) for l in p.lines]
        d["unbalanced_splits"] = [l.id for l in p.lines
                                  if l.is_split and not inv.split_status(l)["balanced"]]
    return d


@router.get("")
def list_purchases(db: Session = Depends(get_db)):
    ps = db.query(models.Purchase).order_by(models.Purchase.id.desc()).all()
    return [_purchase_out(p) for p in ps]


@router.post("/from-document/{doc_id}")
def build_from_document(doc_id: int, db: Session = Depends(get_db)):
    """Create (or fetch) a DRAFT GRN from a confirmed document. Matches each
    line to inventory so the UI can show matched vs new products before posting."""
    doc = db.get(models.Document, doc_id)
    if not doc:
        raise HTTPException(404, "document not found")
    if doc.status not in ("confirmed", "posted") and not doc.latest_extraction:
        raise HTTPException(400, "confirm the extraction before creating a GRN")
    purchase = inv.build_grn_from_document(db, doc)
    db.commit()
    db.refresh(purchase)
    return _purchase_out(purchase, with_lines=True, db=db)


@router.get("/{pid}")
def get_purchase(pid: int, db: Session = Depends(get_db)):
    p = db.get(models.Purchase, pid)
    if not p:
        raise HTTPException(404, "purchase not found")
    return _purchase_out(p, with_lines=True, db=db)


def _draft_line(line_id: int, db: Session) -> models.PurchaseLine:
    line = db.get(models.PurchaseLine, line_id)
    if not line:
        raise HTTPException(404, "GRN line not found")
    if line.purchase.status == "posted":
        raise HTTPException(400, "this GRN is posted — its lines can no longer change")
    return line


@router.put("/lines/{line_id}/splits")
def set_splits(line_id: int, body: SplitRows, db: Session = Depends(get_db)):
    """Break one billed line into variants (or clear the breakdown with []).

    The supplier bills a bundle with no detail; the warehouse enters what actually
    arrived — 50 S cotton, 50 M cotton, 70 L printed — and posting makes each
    distinct attribute combination its own product."""
    line = _draft_line(line_id, db)
    try:
        inv.set_line_splits(db, line, body.rows)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(line)
    return _line_out(line, db)


@router.patch("/lines/{line_id}")
def edit_line(line_id: int, body: LineEdit, db: Session = Depends(get_db)):
    """Set the category master mapping for a line, so its product is created
    already mapped rather than landing 'unmapped' in Inventory. Send "" to clear
    it and fall back to auto-classification from the description.

    The mapping is also *learned*: this is the one moment someone states what a
    supplier's wording means, so the same wording maps itself on the next invoice
    instead of being asked again. Clearing the category forgets it."""
    line = _draft_line(line_id, db)
    fields = body.model_dump(exclude_unset=True)
    if "category" in fields:
        name = (fields["category"] or "").strip()
        if name and not db.query(models.Category).filter(
                models.Category.name == name).first():
            raise HTTPException(400, f"“{name}” is not in the category master")
        line.category = name or None
        from ..services import categorize
        categorize.learn_alias(db, line.description, name or None)
    db.commit()
    db.refresh(line)
    return _line_out(line, db)


@router.post("/lines/{line_id}/scan")
def scan_line_code(line_id: int, body: ScanCode, db: Session = Depends(get_db)):
    """Attach a scanned QR code (or barcode / SKU) to a line, or to one variant of
    it, so the row posts against that exact product instead of relying on the
    description match."""
    line = _draft_line(line_id, db)
    product = barcode_svc.resolve(db, body.code)
    if not product:
        raise HTTPException(404, "no product matches that code")

    if body.split_id is not None:
        sp = db.get(models.PurchaseLineSplit, body.split_id)
        if not sp or sp.line_id != line.id:
            raise HTTPException(404, "variant row not found on this line")
        sp.code = body.code
        sp.product_id = product.id
    else:
        if line.is_split:
            raise HTTPException(400, "this line has a breakdown — scan into one of its rows")
        line.product_id = product.id
        line.is_new_product = False
    db.commit()
    db.refresh(line)
    return _line_out(line, db)


@router.post("/{pid}/unpost")
def unpost_purchase(pid: int, db: Session = Depends(get_db)):
    """Reverse a posted GRN and return it to draft so it can be corrected.

    Refused while a payment, a debit note, or a dispatch depends on it — see
    `GET /{pid}/unpost-check` for what is in the way before committing."""
    p = db.get(models.Purchase, pid)
    if not p:
        raise HTTPException(404, "purchase not found")
    result = inv.unpost_grn(db, p)
    if not result.get("ok"):
        db.rollback()
        raise HTTPException(400, result.get("error", "unpost failed"))
    db.commit()
    return result


@router.get("/{pid}/unpost-check")
def unpost_check(pid: int, db: Session = Depends(get_db)):
    """What would stop this GRN being unposted ([] = nothing), so the UI can warn
    before the user commits to it."""
    p = db.get(models.Purchase, pid)
    if not p:
        raise HTTPException(404, "purchase not found")
    if p.status != "posted":
        return {"posted": False, "blockers": []}
    return {"posted": True, "blockers": inv.unpost_blockers(db, p)}


@router.delete("/{pid}")
def delete_purchase(pid: int, db: Session = Depends(get_db)):
    """Delete a DRAFT GRN — for a duplicate or a GRN built from the wrong invoice.
    Unpost it first if it is posted. The source document is left alone, so a fresh
    GRN can be built from it (or the document itself deleted)."""
    p = db.get(models.Purchase, pid)
    if not p:
        raise HTTPException(404, "purchase not found")
    if p.status == "posted":
        raise HTTPException(400, "this GRN is posted — unpost it before deleting")
    db.delete(p)                      # cascades its lines and their breakdowns
    db.commit()
    return {"ok": True, "deleted_purchase": pid}


@router.post("/{pid}/post")
def post_purchase(pid: int, db: Session = Depends(get_db)):
    """Commit the GRN to inventory: create new products, post inward stock
    movements, update quantities and weighted-average cost."""
    p = db.get(models.Purchase, pid)
    if not p:
        raise HTTPException(404, "purchase not found")
    result = inv.post_grn(db, p)
    if not result.get("ok"):
        db.rollback()
        raise HTTPException(400, result.get("error", "post failed"))
    db.commit()
    return result
