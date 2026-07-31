import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import inventory as inv
from ..services import barcode_svc

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


class StockAdjust(BaseModel):
    new_qty: float
    note: Optional[str] = "manual adjustment"


class ProductDetail(BaseModel):
    """Physical-inspection attributes submitted from the warehouse mobile app."""
    color: Optional[str] = None
    size: Optional[str] = None
    pattern: Optional[str] = None
    fit: Optional[str] = None
    product_type: Optional[str] = None
    material: Optional[str] = None
    design_no: Optional[str] = None
    mrp: Optional[float] = None
    sale_price: Optional[float] = None
    margin_pct: Optional[float] = None
    detailed_by: Optional[str] = None


# sensible starter option sets for the mobile dropdowns (garment/textile domain).
# The app also allows free-text, and we merge in any values already in the DB.
BASE_OPTIONS = {
    "color": ["White", "Black", "Grey", "Kavi", "Medium Kavi", "Light Kavi", "Marvel Grey",
              "Yellow", "Orange", "Blue", "Green", "Red", "Cream", "Maroon", "Multi"],
    "size": ["S", "M", "L", "XL", "XXL", "XXXL", "Free Size", "127 X 200", "127 X 225"],
    "pattern": ["Plain", "Printed", "Checked", "Striped", "Embroidered", "Dobby", "Jacquard", "Border"],
    "fit": ["Regular", "Slim", "Loose", "Comfort"],
    "product_type": ["Dhoti", "Shirt", "Kurti", "Suit", "Panel", "Fabric", "Saree", "Toy", "Other"],
    "material": ["Cotton", "Silk", "Rayon", "Polyester", "Linen", "Chanderi", "Blend", "Balatan"],
    "uom": ["PCS", "MTR", "SET", "BOX"],
}


def _product_out(p: models.Product):
    return {
        "id": p.id, "sku": p.sku, "barcode": p.barcode, "description": p.description,
        "hsn": p.hsn, "uom": p.uom, "mrp": p.mrp, "stock_qty": p.stock_qty,
        "avg_cost": p.avg_cost, "last_rate": p.last_rate, "stock_value": p.stock_value,
        "supplier_name": p.primary_supplier.name if p.primary_supplier else None,
        # physical-detail attributes
        "color": p.color, "size": p.size, "pattern": p.pattern, "fit": p.fit,
        "product_type": p.product_type, "material": p.material, "design_no": p.design_no,
        "sale_price": p.sale_price, "margin_pct": p.margin_pct,
        "category": p.category, "category_section": p.category_section,
        "detailed": bool(p.detailed), "detailed_by": p.detailed_by,
        "detailed_at": p.detailed_at.isoformat() if p.detailed_at else None,
    }


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    s = inv.inventory_summary(db)
    s["detailed"] = db.query(models.Product).filter(models.Product.detailed == True).count()  # noqa: E712
    s["pending_detail"] = s["product_count"] - s["detailed"]
    return s


@router.get("/product-options")
def product_options(db: Session = Depends(get_db)):
    """Dropdown option sets for the mobile detail form — base list merged with
    any distinct values already recorded."""
    opts = {k: list(v) for k, v in BASE_OPTIONS.items()}
    fields = {"color": models.Product.color, "size": models.Product.size,
              "pattern": models.Product.pattern, "fit": models.Product.fit,
              "product_type": models.Product.product_type, "material": models.Product.material,
              "uom": models.Product.uom}
    for key, col in fields.items():
        seen = {v[0] for v in db.query(col).distinct() if v[0]}
        for val in sorted(seen):
            if val not in opts[key]:
                opts[key].append(val)
    return opts


@router.get("/products")
def list_products(status: str = "all", q: str = "", db: Session = Depends(get_db)):
    """status: all | pending (not yet detailed) | detailed. q: text search."""
    query = db.query(models.Product)
    if status == "pending":
        query = query.filter((models.Product.detailed == False) | (models.Product.detailed.is_(None)))  # noqa: E712
    elif status == "detailed":
        query = query.filter(models.Product.detailed == True)  # noqa: E712
    ps = query.order_by(models.Product.description).all()
    if q:
        ql = q.lower()
        ps = [p for p in ps if ql in (p.description or "").lower()
              or ql in (p.sku or "").lower() or ql in (p.barcode or "").lower()
              or ql in (p.hsn or "").lower()]
    return [_product_out(p) for p in ps]


@router.get("/products/{prod_id}")
def get_product(prod_id: int, db: Session = Depends(get_db)):
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    out = _product_out(p)
    out["movements"] = [{
        "id": m.id, "qty_delta": m.qty_delta, "kind": m.kind,
        "ref_type": m.ref_type, "ref_id": m.ref_id, "rate": m.rate,
        "balance_after": m.balance_after, "note": m.note,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    } for m in p.movements]
    return out


# There is deliberately NO product-edit endpoint here. Inventory is a view of what
# the GRN produced, not a second entry form: description / HSN / UOM come off the
# invoice, category and prices are set on the GRN breakdown, and the physical
# attributes are recorded in the phone app below. Correcting any of it means
# unposting the GRN, fixing the line and posting again — so a product's history and
# its master data can never disagree.


@router.post("/products/{prod_id}/detail")
def detail_product(prod_id: int, body: ProductDetail, db: Session = Depends(get_db)):
    """Save physical-inspection attributes (from the mobile app) and mark the
    product as detailed. Feeds straight into the shared inventory database."""
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    data = body.model_dump(exclude_none=True)
    for k in ("color", "size", "pattern", "fit", "product_type", "material",
              "design_no", "mrp", "sale_price", "margin_pct"):
        if k in data:
            setattr(p, k, data[k])
    p.detailed = True
    p.detailed_at = dt.datetime.utcnow()
    p.detailed_by = data.get("detailed_by") or p.detailed_by or "mobile"
    # all details are set → mint sku + barcode so the product is now scannable
    ident = barcode_svc.assign_identifiers(db, p)
    db.commit()
    db.refresh(p)
    out = _product_out(p)
    out["identifiers_generated"] = ident["generated"]
    return out


class GenBarcode(BaseModel):
    force: Optional[bool] = False


@router.post("/products/{prod_id}/generate-barcode")
def generate_barcode(prod_id: int, db: Session = Depends(get_db)):
    """Assign the product its sku + internal barcode if it doesn't have them.
    Requires the product to be fully detailed first."""
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    if not p.detailed:
        raise HTTPException(400, "Set all product details first, then generate the barcode.")
    ident = barcode_svc.assign_identifiers(db, p)
    db.commit()
    db.refresh(p)
    out = _product_out(p)
    out["identifiers_generated"] = ident["generated"]
    return out


@router.get("/lookup")
def lookup(code: str, db: Session = Depends(get_db)):
    """System-wide fetch: resolve a scanned/typed barcode, sku or id to a product."""
    p = barcode_svc.resolve(db, code)
    if not p:
        raise HTTPException(404, f"no product for code '{code}'")
    return _product_out(p)


@router.get("/products/{prod_id}/barcode.svg")
def product_barcode_svg(prod_id: int, db: Session = Depends(get_db)):
    """Bare SVG of the product's barcode — for inline preview in the UI."""
    from fastapi.responses import Response
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    svg = barcode_svc.barcode_svg(p.barcode or p.sku or str(p.id))
    return Response(svg, media_type="image/svg+xml")


@router.get("/products/{prod_id}/qr.svg")
def product_qr_svg(prod_id: int, scale: int = 4, db: Session = Depends(get_db)):
    """Bare SVG of the product's QR code — for inline preview in the UI."""
    from fastapi.responses import Response
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    return Response(barcode_svc.product_qr_svg(p, scale=max(1, min(scale, 10))),
                    media_type="image/svg+xml")


@router.get("/products/{prod_id}/qr-payload")
def product_qr_payload(prod_id: int, db: Session = Depends(get_db)):
    """Exactly what is encoded in this product's QR — for debugging a label and
    for confirming what a scanner will receive."""
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    payload = barcode_svc.qr_payload(p)
    return {"product_id": p.id, "schema": barcode_svc.QR_SCHEMA,
            "bytes": len(payload.encode()), "payload": payload,
            "decoded": barcode_svc.parse_qr_payload(payload)}


@router.get("/categorize")
def categorize_description(description: str, section: Optional[str] = None,
                           limit: int = 5, db: Session = Depends(get_db)):
    """Map a free-text description onto the category master.

    `confident` says whether this would be applied automatically; `candidates`
    are the ranked alternatives for a human to choose from when it wouldn't.
    Pass `section` to force OVERALL/KIDS/LADIES/MENS instead of the detected one."""
    from ..services import categorize
    return categorize.suggest(db, description, limit=max(1, min(limit, 25)),
                              section=section)


@router.post("/products/{prod_id}/categorize")
def categorize_product(prod_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Re-run category mapping for one product. By default won't overwrite a
    category that is already set — pass force=true to re-map it."""
    from ..services import categorize
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    res = categorize.categorise_product(db, p, force=force)
    db.commit()
    db.refresh(p)
    return {"product": _product_out(p), "suggestion": res}


@router.post("/categorize-all")
def categorize_all(force: bool = False, db: Session = Depends(get_db)):
    """Backfill categories across the product master — for the products that
    already existed before category mapping was added. Returns what changed and
    what needs a human, so an unmapped product is never just silently skipped."""
    from ..services import categorize
    applied, needs_review = 0, []
    for p in db.query(models.Product).all():
        if p.category and not force:
            continue
        res = categorize.categorise_product(db, p, force=force)
        if res["applied"]:
            applied += 1
        else:
            needs_review.append({
                "id": p.id, "sku": p.sku, "description": p.description,
                "best": res["best"], "candidates": res["candidates"]})
    db.commit()
    return {"ok": True, "applied": applied, "needs_review": len(needs_review),
            "unmapped": needs_review}


@router.get("/products/{prod_id}/label", response_class=HTMLResponse)
def product_label(prod_id: int, db: Session = Depends(get_db)):
    """Print-ready single barcode label for one product."""
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    if not p.barcode and not p.sku:
        barcode_svc.assign_identifiers(db, p)
        db.commit(); db.refresh(p)
    return HTMLResponse(barcode_svc.labels_sheet([p]))


@router.get("/labels", response_class=HTMLResponse)
def labels(ids: str = "", status: str = "detailed", db: Session = Depends(get_db)):
    """Print-ready barcode-label sheet. Pass ids=1,2,3 for a selection, else all
    products matching status (default: detailed)."""
    if ids.strip():
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        ps = db.query(models.Product).filter(models.Product.id.in_(want)).all()
        ps.sort(key=lambda p: want.index(p.id))
    else:
        q = db.query(models.Product)
        if status == "detailed":
            q = q.filter(models.Product.detailed == True)  # noqa: E712
        ps = q.order_by(models.Product.description).all()
    # make sure every product on the sheet is scannable
    changed = False
    for p in ps:
        if not p.barcode or not p.sku:
            barcode_svc.assign_identifiers(db, p); changed = True
    if changed:
        db.commit()
    return HTMLResponse(barcode_svc.labels_sheet(ps))


@router.post("/products/{prod_id}/adjust-stock")
def adjust_stock(prod_id: int, body: StockAdjust, db: Session = Depends(get_db)):
    """Correct a product's stock to an exact figure (writes an adjustment
    movement — the ledger stays the source of truth)."""
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    inv.adjust_stock(db, p, body.new_qty, body.note or "manual adjustment")
    db.commit()
    db.refresh(p)
    return _product_out(p)
