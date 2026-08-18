"""
One product, described the way a warehouse screen has to describe it.

Stock Outward (dispatch), Stock Inward (the destination accepting that dispatch)
and Purchase Return all move goods that somebody is holding in their hands and
has to identify before the movement is committed. A row saying "T-SHIRT · 40" is
not enough to do that with: the same description covers four sizes in three
colours, and picking the wrong one is a mis-dispatch nobody notices until the
destination counts the box.

So all three read their line detail from here rather than each picking its own
subset of columns — the QR (which is what actually gets scanned), the name, the
attribute tuple that tells one variant from another, and the batch the stock was
received under. One projection also means the three screens can never drift into
showing different things about the same product.

Money is deliberately labelled by ROLE, not just by name:
  * `grn_cost` — what we PAID (weighted-average purchase cost). This is the only
    figure a debit note may be valued at; see services/returns.py.
  * `mrp` / `sale_price` — what we SELL for. Shown for identification (the tag on
    the garment says the MRP), never used to value a movement.
"""
from .. import models
from . import barcode_svc


# The attribute tuple that makes one variant a different stock item from another
# (same list as inventory.SPLIT_ATTRS), in the order a person reads them off a
# garment. `label` is what the screens print, so the wording is set once here.
ATTRIBUTES = [
    ("brand", "Brand"), ("size", "Size"), ("color", "Colour"),
    ("material", "Material"), ("pattern", "Pattern"), ("fit", "Fit"),
    ("style", "Style"), ("sleeve", "Sleeve"), ("product_type", "Type"),
    ("design_no", "Design No"),
]


def display_name(product) -> str:
    """What this product is CALLED, everywhere one is named.

    The category, not the invoice wording. `description` is whatever the supplier
    printed on their bill — "TISSOT Lycra", "IND SMART LYCRA" — which is a style
    or a mill name and tells nobody in the warehouse, the shop or at the till
    what the article actually is. The category is the answer to that question:
    LADIES-SHORTS, KIDS-KURTA SET. It is also the one field somebody deliberately
    SETS, on the GRN line, before the product is created.

    The supplier's wording is not thrown away — it stays on `description`, is
    still returned beside this, and is still what a re-buy is matched on. It just
    stops being the name. A product with no category falls back to it, because a
    row with no name at all is worse than a row named after a mill."""
    if not product:
        return ""
    return (getattr(product, "category", None) or "").strip() \
        or (getattr(product, "description", None) or "")


def _batch_label(purchase, bundle_code=None):
    bits = []
    if purchase.grn_no:
        bits.append(f"GRN {purchase.grn_no}")
    if purchase.invoice_number:
        bits.append(f"Inv {purchase.invoice_number}")
    if bundle_code:
        bits.append(bundle_code)
    return " · ".join(bits) or f"GRN #{purchase.id}"


def _bundle_code(db, purchase, product):
    """The carton this product arrived in on that receipt, if it was bundled."""
    if not (purchase and product):
        return None
    for b in db.query(models.Bundle).filter(
            models.Bundle.purchase_id == purchase.id).all():
        if any(p is not None and p.id == product.id for p in b.products):
            return b.code
    return None


def batch_of(db, purchase, product=None, rate=None, received_at=None):
    """The receipt a piece of stock came in on — this system's notion of a batch.

    Goods are not tracked in lots here; what a warehouse can actually verify a
    garment against is the GRN that received it, the supplier invoice behind that
    GRN, and the carton it was put away in. That triple is the batch."""
    if not purchase:
        return None
    code = _bundle_code(db, purchase, product)
    at = received_at or purchase.posted_at
    return {
        "purchase_id": purchase.id,
        "grn_no": purchase.grn_no,
        "invoice_number": purchase.invoice_number,
        "invoice_date": purchase.invoice_date,
        "supplier": purchase.supplier.name if purchase.supplier else None,
        "bundle_code": code,
        "received_at": at.isoformat() if at else None,
        # what this batch cost per piece, when the caller knows (an outward line
        # can span batches bought at different rates)
        "rate": round(float(rate), 4) if rate is not None else None,
        "label": _batch_label(purchase, code),
    }


def receipt_batches(db, product, limit=5):
    """The receipts that put this product on the floor, newest first.

    Stock is held as one pooled quantity per SKU, so a dispatch of 40 can draw on
    more than one GRN. Listing them (rather than inventing a single 'the' batch)
    is the honest answer, and it is what lets someone reading a dispatch note say
    which consignment the goods came from."""
    if not product:
        return []
    seen, out = set(), []
    for mv in sorted(product.movements, key=lambda m: m.id, reverse=True):
        if mv.kind != "inward" or mv.ref_type != "purchase" or mv.ref_id in seen:
            continue
        seen.add(mv.ref_id)
        purchase = db.get(models.Purchase, mv.ref_id)
        if not purchase:
            continue
        out.append(batch_of(db, purchase, product, rate=mv.rate,
                            received_at=mv.created_at))
        if len(out) >= limit:
            break
    return out


def attribute_list(product):
    """The filled-in attributes, in reading order, ready to print as chips."""
    return [{"key": k, "label": label, "value": getattr(product, k, None)}
            for k, label in ATTRIBUTES if getattr(product, k, None)]


def variant_label(product):
    """"L · Red · Cotton" — what tells this product from its siblings."""
    return " · ".join(str(a["value"]) for a in attribute_list(product))


def product_card(db, product, purchase=None, with_batches=True):
    """Everything a person needs to identify one product at a stock movement.

    `purchase` pins the batch to a known receipt (a purchase return is always
    against one invoice, so its batch is exact rather than inferred)."""
    if not product:
        return None
    code = product.sku or product.barcode or str(product.id)
    card = {
        # --- identity: what a scan yields and what is printed under the QR ---
        "product_id": product.id,
        "sku": product.sku,
        "code": code,
        "supplier_barcode": product.barcode,
        "qr_payload": barcode_svc.qr_payload(product),
        "qr_svg_url": f"/api/inventory/products/{product.id}/qr.svg",
        "qr_png_url": f"/api/inventory/products/{product.id}/qr.png",
        "label_url": f"/api/inventory/products/{product.id}/label",
        # --- what it is ---
        # `name` is what it is CALLED (the category); `description` is what the
        # supplier called it. Both travel, so a screen can lead with the one and
        # still show the other underneath — see display_name.
        "name": display_name(product),
        "description": product.description,
        "hsn": product.hsn,
        "uom": product.uom,
        "category": product.category,
        "category_section": product.category_section,
        "variant": variant_label(product),
        "attributes": attribute_list(product),
        # --- what it costs us, and what it sells for (never interchangeable) ---
        "grn_cost": round(float(product.avg_cost or 0), 2),
        "last_rate": product.last_rate,
        "mrp": product.mrp,
        "sale_price": product.sale_price,
        "sale_discount_pct": product.sale_discount_pct,
        # --- where it stands ---
        "stock_qty": product.stock_qty,
        "stock_value": product.stock_value,
        "supplier": (product.primary_supplier.name
                     if product.primary_supplier else None),
        "detailed": bool(product.detailed),
    }
    for key, _ in ATTRIBUTES:                 # flat too, for simple table cells
        card[key] = getattr(product, key, None)
    if purchase is not None:
        card["batch"] = batch_of(db, purchase, product)
        card["batches"] = [card["batch"]]
    elif with_batches:
        card["batches"] = receipt_batches(db, product)
        card["batch"] = card["batches"][0] if card["batches"] else None
    else:
        card["batch"], card["batches"] = None, []
    return card


def card_for_code(db, code):
    """Resolve a scanned QR / piece label / SKU / supplier barcode to a card.

    Accepts anything `barcode_svc.resolve` accepts, which includes a per-piece
    garment label — so someone can scan the tag on the item itself rather than
    hunting for the shelf code."""
    from . import units as unit_svc
    product = barcode_svc.resolve(db, code)
    if not product:
        return None
    card = product_card(db, product)
    unit = unit_svc.resolve(db, code)
    if unit:
        card["scanned_unit"] = {"id": unit.id, "code": unit.code,
                                "seq": unit.seq, "status": unit.status}
    return card
