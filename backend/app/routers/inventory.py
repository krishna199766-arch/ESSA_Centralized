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
    sale_discount_pct: Optional[float] = None
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


def _product_out(p: models.Product, ctx=None):
    """One product. `ctx` adds its provenance and whether its labels may print —
    passed in by the callers that already built one, so a list of 500 products
    doesn't re-derive the same lookup 500 times."""
    d = _product_fields(p)
    if ctx is not None:
        from ..services import integrity
        rep = integrity.product_report(ctx.db, p, ctx)
        d.update({"state": rep["state"], "live_units": rep["live_units"],
                  "expected_units": rep["expected_units"],
                  "units_match": rep["units_match"],
                  "can_print": rep["can_print"], "print_block": rep["print_block"],
                  "issues": rep["issues"]})
    return d


def _product_fields(p: models.Product):
    return {
        "id": p.id, "sku": p.sku, "barcode": p.barcode, "description": p.description,
        "hsn": p.hsn, "uom": p.uom, "mrp": p.mrp, "stock_qty": p.stock_qty,
        "avg_cost": p.avg_cost, "last_rate": p.last_rate, "stock_value": p.stock_value,
        "supplier_name": p.primary_supplier.name if p.primary_supplier else None,
        # physical-detail attributes
        "color": p.color, "size": p.size, "pattern": p.pattern, "fit": p.fit,
        "product_type": p.product_type, "material": p.material, "design_no": p.design_no,
        "sale_price": p.sale_price, "sale_discount_pct": p.sale_discount_pct,
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
    """status: all | pending (not yet detailed) | detailed | excluded. q: text search.

    Stock is only ever created by posting a GRN, so a product that traces back to
    no posted GRN is not stock and is kept out of every normal listing — it would
    otherwise sit there looking exactly like the real thing, scannable and
    printable. `status=excluded` shows precisely those, which is what the
    Inventory Repair screen lists; the count is on `/summary` either way, so the
    exclusion is never silent."""
    from ..services import integrity
    ctx = integrity.Context(db)
    query = db.query(models.Product)
    if status == "pending":
        query = query.filter((models.Product.detailed == False) | (models.Product.detailed.is_(None)))  # noqa: E712
    elif status == "detailed":
        query = query.filter(models.Product.detailed == True)  # noqa: E712
    ps = query.order_by(models.Product.description).all()
    if status == "excluded":
        ps = [p for p in ps if ctx.product_state(p) != integrity.POSTED]
    else:
        ps = [p for p in ps if ctx.product_state(p) == integrity.POSTED]
    if q:
        ql = q.lower()
        ps = [p for p in ps if ql in (p.description or "").lower()
              or ql in (p.sku or "").lower() or ql in (p.barcode or "").lower()
              or ql in (p.hsn or "").lower()]
    return [_product_out(p, ctx) for p in ps]


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
              "design_no", "mrp", "sale_price", "sale_discount_pct"):
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
    """Assign the product its SKU if it hasn't got one — the only identifier we
    issue, and what its QR resolves to. Requires the product to be fully detailed
    first. (Path kept for compatibility; no barcode is minted any more.)"""
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    if not p.detailed:
        raise HTTPException(400, "Set all product details first, then generate the code.")
    ident = barcode_svc.assign_identifiers(db, p)
    db.commit()
    db.refresh(p)
    out = _product_out(p)
    out["identifiers_generated"] = ident["generated"]
    return out


# ---------------------------------------------------------------------------
#  Per-piece codes — one identity per garment under a SKU that has many
# ---------------------------------------------------------------------------
class UnitsPrinted(BaseModel):
    ids: list[int]
    printed_by: Optional[str] = None


def _unit_out(u: models.ProductUnit):
    return {
        "id": u.id, "code": u.code, "seq": u.seq, "status": u.status,
        "product_id": u.product_id, "purchase_id": u.purchase_id,
        "print_count": u.print_count or 0,
        "last_printed_at": u.last_printed_at.isoformat() if u.last_printed_at else None,
        "last_printed_by": u.last_printed_by,
    }


@router.get("/products/{prod_id}/units")
def product_units(prod_id: int, db: Session = Depends(get_db)):
    """The individual pieces under one SKU — what the Inventory screen shows when
    the quantity is clicked. `serialisable` explains an empty list rather than
    leaving the screen to guess (fabric sold by the metre has no pieces).

    Each piece carries its own `state`, and the payload says outright whether the
    labels may print and why not. A dead code looks exactly like a live one on
    screen — same format, same QR, same product — so the difference has to be
    stated, not left for the eye."""
    from ..services import units as unit_svc
    from ..services import integrity
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    ctx = integrity.Context(db)
    rep = integrity.product_report(db, p, ctx)
    rows = db.query(models.ProductUnit).filter(
        models.ProductUnit.product_id == prod_id).order_by(models.ProductUnit.seq).all()
    ok, reason = unit_svc.can_serialise(p.uom, rep["expected_units"] or p.stock_qty)
    return {
        "product": {"id": p.id, "sku": p.sku, "description": p.description,
                    "uom": p.uom, "stock_qty": p.stock_qty, "mrp": p.mrp,
                    "size": p.size, "color": p.color, "category": p.category},
        "count": len(rows), "serialisable": ok, "reason": reason,
        # --- integrity: what may be printed, and what is merely left over ---
        "state": rep["state"], "live_units": rep["live_units"],
        "orphan_units": rep["orphan_units"], "expected_units": rep["expected_units"],
        "units_match": rep["units_match"],
        "can_print": rep["can_print"], "print_block": rep["print_block"],
        "issues": rep["issues"],
        "units": [{**_unit_out(u), "state": ctx.unit_state(u)} for u in rows],
    }


@router.post("/products/{prod_id}/units/generate")
def generate_units(prod_id: int, db: Session = Depends(get_db)):
    """Give stock received before per-piece codes existed its missing codes.

    Refused for a product that isn't stock: minting identities for garments no
    posted GRN ever brought in is how a phantom becomes scannable."""
    from ..services import units as unit_svc
    from ..services import integrity
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    rep = integrity.product_report(db, p)
    if rep["state"] != integrity.POSTED:
        raise HTTPException(400, "this product doesn't belong to a posted GRN, so it "
                                 "isn't stock — no piece codes can be issued for it")
    made, reason = unit_svc.backfill(db, p)
    if not made and reason:
        raise HTTPException(400, reason)
    db.commit()
    return {"ok": True, "created": len(made), "codes": [u.code for u in made]}


@router.get("/units/{uid}/qr.svg")
def unit_qr_svg(uid: int, scale: int = 4, db: Session = Depends(get_db)):
    from fastapi.responses import Response
    u = db.get(models.ProductUnit, uid)
    if not u:
        raise HTTPException(404, "piece not found")
    return Response(barcode_svc.unit_qr_svg(u, scale=max(1, min(scale, 10))),
                    media_type="image/svg+xml")


@router.get("/units/{uid}/qr.png")
def unit_qr_png(uid: int, scale: int = 6, db: Session = Depends(get_db)):
    from fastapi.responses import Response
    u = db.get(models.ProductUnit, uid)
    if not u:
        raise HTTPException(404, "piece not found")
    png = barcode_svc.unit_qr_png(u, scale=max(1, min(scale, 20)))
    if not png:
        raise HTTPException(503, "QR library not available on the server")
    return Response(png, media_type="image/png")


@router.get("/units/{uid}/label", response_class=HTMLResponse)
def unit_label(uid: int, db: Session = Depends(get_db)):
    """One piece's label — Print for a single garment, or Reprint a torn one.

    A single label is allowed where "print all" would be refused: reprinting one
    torn tag is exactly the job someone does while a count is being sorted out.
    What is refused is a code with no posted GRN behind it — that garment does not
    exist, so neither should its label."""
    from ..services import units as unit_svc
    from ..services import integrity
    u = db.get(models.ProductUnit, uid)
    if not u:
        raise HTTPException(404, "piece not found")
    if integrity.Context(db).unit_state(u) != integrity.POSTED:
        raise HTTPException(400, f"{u.code} belongs to no posted GRN — it is a left-over "
                                 f"code, not a garment. Run Inventory Repair.")
    unit_svc.mark_printed(db, [u])
    db.commit()
    return HTMLResponse(barcode_svc.unit_labels_sheet([u]))


@router.get("/unit-labels", response_class=HTMLResponse)
def unit_labels(ids: str = "", product_id: int = 0, db: Session = Depends(get_db)):
    """A sheet of per-piece labels: a selection (ids=1,2,3) or every piece of one
    SKU. Opening it counts as printing them, which is what makes the screen able
    to offer Reprint afterwards.

    **Print all is refused on a mismatch.** A label is a physical thing stuck on a
    garment, so the number printed can never exceed the number of garments — and
    when the piece codes and the stock figure disagree, neither number can be
    trusted until someone says which is right. The check lives here rather than
    only in the UI because this URL is openable directly, and a guard a client can
    skip is not a guard."""
    from ..services import units as unit_svc
    from ..services import integrity
    q = db.query(models.ProductUnit)
    if ids.strip():
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        rows = q.filter(models.ProductUnit.id.in_(want)).all()
        rows.sort(key=lambda u: want.index(u.id))
        # a hand-picked selection still may not include a dead code
        ctx = integrity.Context(db)
        dead = [u.code for u in rows if ctx.unit_state(u) != integrity.POSTED]
        if dead:
            raise HTTPException(400, f"{len(dead)} of these piece codes belong to no "
                                     f"posted GRN ({', '.join(dead[:4])}"
                                     f"{'…' if len(dead) > 4 else ''}) — run Inventory "
                                     f"Repair before printing")
    elif product_id:
        p = db.get(models.Product, product_id)
        if not p:
            raise HTTPException(404, "product not found")
        ctx = integrity.Context(db)
        ok, why = integrity.can_print(db, p, ctx)
        if not ok:
            raise HTTPException(400, why)
        # LIVE codes only. Passing the check is not the same as every row being
        # printable: a SKU can hold the right number of live codes and still carry
        # left-over ones beside them, and "print all" must mean all the garments,
        # never all the rows.
        rows = [u for u in q.filter(models.ProductUnit.product_id == product_id).order_by(
            models.ProductUnit.seq).all()
            if ctx.unit_state(u) == integrity.POSTED]
    else:
        raise HTTPException(400, "pass ids= or product_id=")
    if not rows:
        raise HTTPException(404, "no pieces to print")
    unit_svc.mark_printed(db, rows)
    db.commit()
    return HTMLResponse(barcode_svc.unit_labels_sheet(rows))


@router.post("/units/printed")
def units_printed(body: UnitsPrinted, db: Session = Depends(get_db)):
    """Record a print without rendering a sheet — for a label printer driven
    elsewhere."""
    from ..services import units as unit_svc
    rows = db.query(models.ProductUnit).filter(
        models.ProductUnit.id.in_(body.ids)).all()
    unit_svc.mark_printed(db, rows, body.printed_by)
    db.commit()
    return {"ok": True, "marked": len(rows)}


@router.get("/units/lookup")
def unit_lookup(code: str, db: Session = Depends(get_db)):
    """Resolve a scanned per-piece label: which piece, and which product it is."""
    from ..services import units as unit_svc
    u = unit_svc.resolve(db, code)
    if not u:
        raise HTTPException(404, f"no piece for code '{code}'")
    out = _unit_out(u)
    out["product"] = _product_out(u.product) if u.product else None
    return out


@router.get("/lookup")
def lookup(code: str, db: Session = Depends(get_db)):
    """System-wide fetch: resolve a scanned/typed barcode, sku or id to a product."""
    p = barcode_svc.resolve(db, code)
    if not p:
        raise HTTPException(404, f"no product for code '{code}'")
    return _product_out(p)


@router.get("/product-card")
def product_card(code: str = "", product_id: int = 0, db: Session = Depends(get_db)):
    """The full product record as the stock screens show it — QR, name, the
    attribute tuple, and the batches it was received on.

    Same projection Stock Outward, Stock Inward and Purchase Return use for their
    lines, reachable on its own so a scan can be verified before anything is
    added to a document."""
    from ..services import stock_view
    if product_id:
        p = db.get(models.Product, product_id)
        if not p:
            raise HTTPException(404, "product not found")
        return stock_view.product_card(db, p)
    card = stock_view.card_for_code(db, code)
    if not card:
        raise HTTPException(404, f"no product for code '{code}'")
    return card


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


@router.get("/products/{prod_id}/qr.png")
def product_qr_png(prod_id: int, scale: int = 6, db: Session = Depends(get_db)):
    """The product's QR as a PNG — what the phone app shows for a variant it has
    just created, since React Native cannot render the SVG above."""
    from fastapi.responses import Response
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    png = barcode_svc.product_qr_png(p, scale=max(1, min(scale, 20)))
    if not png:
        raise HTTPException(503, "QR library not available on the server")
    return Response(png, media_type="image/png")


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
    """Print-ready single QR label for one product."""
    from ..services import integrity
    p = db.get(models.Product, prod_id)
    if not p:
        raise HTTPException(404, "product not found")
    ok, why = integrity.can_print(db, p)
    if not ok:
        raise HTTPException(400, why)
    if not p.sku:
        barcode_svc.assign_identifiers(db, p)
        db.commit(); db.refresh(p)
    return HTMLResponse(barcode_svc.labels_sheet([p]))


@router.get("/labels", response_class=HTMLResponse)
def labels(ids: str = "", status: str = "detailed", db: Session = Depends(get_db)):
    """Print-ready QR-label sheet. Pass ids=1,2,3 for a selection, else all
    products matching status (default: detailed).

    Products that aren't stock are dropped from the sheet rather than failing the
    whole print — a bulk run should give you the labels it legitimately can, and
    say what it left out. An explicit id list is stricter: naming a record that is
    not stock is a mistake worth surfacing, not silently honouring."""
    from ..services import integrity
    ctx = integrity.Context(db)
    if ids.strip():
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        ps = db.query(models.Product).filter(models.Product.id.in_(want)).all()
        ps.sort(key=lambda p: want.index(p.id))
        bad = [p for p in ps if ctx.product_state(p) != integrity.POSTED]
        if bad:
            raise HTTPException(400, f"{len(bad)} of these products belong to no posted "
                                     f"GRN ({', '.join((p.sku or '?') for p in bad[:4])}"
                                     f"{'…' if len(bad) > 4 else ''}) — run Inventory Repair")
    else:
        q = db.query(models.Product)
        if status == "detailed":
            q = q.filter(models.Product.detailed == True)  # noqa: E712
        ps = [p for p in q.order_by(models.Product.description).all()
              if ctx.product_state(p) == integrity.POSTED]
    if not ps:
        raise HTTPException(404, "nothing to print — no product here belongs to a posted GRN")
    # make sure every product on the sheet is scannable
    changed = False
    for p in ps:
        if not p.sku:
            barcode_svc.assign_identifiers(db, p); changed = True
    if changed:
        db.commit()
    return HTMLResponse(barcode_svc.labels_sheet(ps))


# ---------------------------------------------------------------------------
#  Inventory repair — find and remove what isn't stock
# ---------------------------------------------------------------------------
@router.get("/integrity")
def integrity_scan(db: Session = Depends(get_db)):
    """Read-only: every inventory record that fails a check, and why.

    This is what the Repair screen shows *before* anyone agrees to delete
    anything — orphan products, orphan piece codes, orphan cartons, and any SKU
    whose piece codes don't match what it received. `unposted_products` is listed
    separately and is never offered for deletion: those are kept on purpose."""
    from ..services import integrity
    return integrity.scan(db)


@router.post("/repair")
def integrity_repair(dry_run: bool = False, db: Session = Depends(get_db)):
    """Delete the debris — records traceable to no GRN at all and carrying no
    ledger history of their own.

    `dry_run=true` returns exactly what would go without touching anything.
    Products kept at zero stock after an unpost are never removed: they hold
    detailing recorded by hand in the warehouse, and no cleanup is worth
    destroying that silently."""
    from ..services import integrity
    result = integrity.repair(db, dry_run=dry_run)
    if not dry_run:
        db.commit()
    return result


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
