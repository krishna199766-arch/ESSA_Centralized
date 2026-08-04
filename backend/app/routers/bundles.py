from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import bundles as bundle_svc
from ..services import barcode_svc

router = APIRouter(prefix="/api/bundles", tags=["bundles"])


class Locate(BaseModel):
    location: str
    located_by: Optional[str] = None


class TagIn(BaseModel):
    tagged_by: Optional[str] = None


def _item_out(p: models.Product):
    return {
        "id": p.id, "sku": p.sku, "description": p.description,
        "size": p.size, "color": p.color, "material": p.material,
        "category": p.category, "stock_qty": p.stock_qty, "mrp": p.mrp,
        "detailed": bool(p.detailed), "detailed_by": p.detailed_by,
    }


def _out(b: models.Bundle, with_items=False):
    d = {
        "id": b.id, "code": b.code, "description": b.description,
        "qty": b.qty, "uom": b.uom, "hsn": b.hsn, "item_count": b.item_count,
        "grn_no": b.grn_no, "invoice_number": b.invoice_number,
        "supplier_name": b.supplier.name if b.supplier else None,
        "purchase_id": b.purchase_id, "line_id": b.line_id,
        "location": b.location, "status": b.status,
        "located_by": b.located_by, "tagged_by": b.tagged_by,
        "received_at": b.received_at.isoformat() if b.received_at else None,
        "tagged_at": b.tagged_at.isoformat() if b.tagged_at else None,
    }
    if with_items:
        items = b.products
        d["items"] = [_item_out(p) for p in items]
        d["items_pending_detail"] = sum(1 for p in items if not p.detailed)
        # the mix, which is the whole reason a carton label is worth reading
        d["mix"] = [{"label": s.variant_label, "qty": s.qty,
                     "sku": s.product.sku if s.product else None}
                    for s in (b.line.splits if b.line and b.line.is_split else [])]
    return d


@router.get("")
def list_bundles(status: str = "", location: str = "", q: str = "",
                 db: Session = Depends(get_db)):
    """Cartons in the warehouse. status: stored | opened | tagged."""
    query = db.query(models.Bundle)
    if status:
        query = query.filter(models.Bundle.status == status)
    if location:
        query = query.filter(models.Bundle.location == location)
    rows = query.order_by(models.Bundle.id.desc()).all()
    if q:
        ql = q.lower()
        rows = [b for b in rows
                if ql in (b.description or "").lower() or ql in (b.code or "").lower()
                or ql in (b.grn_no or "").lower() or ql in (b.location or "").lower()
                or ql in (b.invoice_number or "").lower()]
    return [_out(b) for b in rows]


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return bundle_svc.summary(db)


@router.get("/locations")
def locations(db: Session = Depends(get_db)):
    """Racks/bins already in use, so put-away offers them instead of inviting a
    typo that makes a second location out of one."""
    seen = {v[0] for v in db.query(models.Bundle.location).distinct() if v[0]}
    return sorted(seen)


@router.get("/lookup")
def lookup(code: str, db: Session = Depends(get_db)):
    """Resolve a scanned carton QR (or a typed bundle code) to its bundle."""
    b = bundle_svc.resolve(db, code)
    if not b:
        raise HTTPException(404, f"no bundle for code '{code}'")
    return _out(b, with_items=True)


@router.get("/labels", response_class=HTMLResponse)
def bundle_labels(ids: str = "", purchase_id: int = 0, db: Session = Depends(get_db)):
    """Carton labels for a selection, or for every bundle of one GRN — which is
    what gets printed the moment a receipt is posted.

    Declared before `/{bid}`: a literal path has to be registered ahead of the
    parameterised one, or "labels" is read as a bundle id and 422s."""
    q = db.query(models.Bundle)
    if ids.strip():
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        rows = q.filter(models.Bundle.id.in_(want)).all()
        rows.sort(key=lambda b: want.index(b.id))
    elif purchase_id:
        rows = q.filter(models.Bundle.purchase_id == purchase_id).order_by(
            models.Bundle.id).all()
    else:
        rows = q.order_by(models.Bundle.id.desc()).all()
    return HTMLResponse(barcode_svc.bundle_labels_sheet(rows))


@router.get("/{bid}")
def get_bundle(bid: int, db: Session = Depends(get_db)):
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    return _out(b, with_items=True)


@router.post("/{bid}/locate")
def locate(bid: int, body: Locate, db: Session = Depends(get_db)):
    """Record where the carton was put away — or move it somewhere else."""
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    try:
        bundle_svc.locate(db, b, body.location, body.located_by)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(b)
    return _out(b, with_items=True)


@router.post("/{bid}/open")
def open_bundle(bid: int, db: Session = Depends(get_db)):
    """Mark the carton opened — its contents are being handled individually now."""
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    bundle_svc.open_bundle(db, b)
    db.commit()
    db.refresh(b)
    return _out(b, with_items=True)


@router.post("/{bid}/tag")
def tag(bid: int, body: TagIn, db: Session = Depends(get_db)):
    """The second label: mark the carton's items tagged for sale and return the
    products whose labels should print (see `/{bid}/item-labels`).

    Refused while any item is still undetailed — printing a tag before someone has
    looked at the garment is exactly what this two-step exists to avoid."""
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    try:
        prods = bundle_svc.tag(db, b, body.tagged_by)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    db.commit()
    db.refresh(b)
    out = _out(b, with_items=True)
    out["label_ids"] = [p.id for p in prods]
    return out


@router.get("/{bid}/label", response_class=HTMLResponse)
def bundle_label(bid: int, db: Session = Depends(get_db)):
    """Print-ready CARTON label — the one that goes on the box at GRN."""
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    return HTMLResponse(barcode_svc.bundle_labels_sheet([b]))


@router.get("/{bid}/item-labels", response_class=HTMLResponse)
def item_labels(bid: int, db: Session = Depends(get_db)):
    """The second label set: a garment tag per PIECE in this carton.

    A carton of 8 gets 8 distinguishable tags, not 8 copies of one — so the sheet
    is built from the pieces when they exist, and falls back to one tag per SKU
    only for goods that cannot be serialised (fabric by the metre)."""
    from ..services import units as unit_svc
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    prods = b.products
    if not prods:
        raise HTTPException(400, "this bundle has no items")
    changed = False
    for p in prods:
        if not p.sku:
            barcode_svc.assign_identifiers(db, p)
            changed = True
    if changed:
        db.commit()
    pieces = db.query(models.ProductUnit).filter(
        models.ProductUnit.product_id.in_([p.id for p in prods])).order_by(
        models.ProductUnit.product_id, models.ProductUnit.seq).all()
    if pieces:
        unit_svc.mark_printed(db, pieces)
        db.commit()
        return HTMLResponse(barcode_svc.unit_labels_sheet(pieces))
    return HTMLResponse(barcode_svc.labels_sheet(prods))


@router.get("/{bid}/qr.svg")
def bundle_qr_svg(bid: int, scale: int = 4, db: Session = Depends(get_db)):
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    return Response(barcode_svc.bundle_qr_svg(b, scale=max(1, min(scale, 10))),
                    media_type="image/svg+xml")


@router.get("/{bid}/qr.png")
def bundle_qr_png(bid: int, scale: int = 6, db: Session = Depends(get_db)):
    """PNG for the phone app, which cannot render the SVG above."""
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    png = barcode_svc.bundle_qr_png(b, scale=max(1, min(scale, 20)))
    if not png:
        raise HTTPException(503, "QR library not available on the server")
    return Response(png, media_type="image/png")


@router.get("/{bid}/qr-payload")
def bundle_qr_payload(bid: int, db: Session = Depends(get_db)):
    b = db.get(models.Bundle, bid)
    if not b:
        raise HTTPException(404, "bundle not found")
    payload = barcode_svc.bundle_qr_payload(b)
    return {"bundle_id": b.id, "bytes": len(payload.encode()), "payload": payload,
            "decoded": barcode_svc.parse_bundle_payload(payload)}
