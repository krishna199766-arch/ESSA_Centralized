"""
Bundles — the cartons goods arrive in, and the first of the two labels they carry.

Two labels exist because there are two moments and two questions:

  * **At GRN**, the goods are on the floor and have to go somewhere. What matters
    is "which box is this, what is in it, where does it live" — so every posted
    line gets a `Bundle` with its own code and a big carton label. Nothing is
    counted twice: a bundle is a handling unit, never a stock row.

  * **Later**, when a box is opened to be packed or put out for sale, each garment
    needs its own tag: "ESSA-00004, L, Red, MRP 899". That is `labels_sheet` over
    the products the bundle holds, and doing it here — at tagging — rather than at
    GRN is the point. Tagging 50 loose garments that are about to sit in a carton
    for a fortnight is wasted work, and a label printed before the item was
    inspected carries less than one printed after.

So: `create_for_purchase` at post, `locate` when it is put away, `tag` when it is
opened and its contents labelled. The bundle's own life ends at `tagged`, because
from then on the items are handled individually and the carton is just cardboard.
"""
import datetime as dt
from .. import models
from . import barcode_svc

STATUSES = ("stored", "opened", "tagged")


def next_code(db):
    n = db.query(models.Bundle).count() + 1
    while db.query(models.Bundle).filter(
            models.Bundle.code == f"{barcode_svc.BUNDLE_PREFIX}{n:05d}").first():
        n += 1
    return f"{barcode_svc.BUNDLE_PREFIX}{n:05d}"


def create_for_purchase(db, purchase):
    """Give every line of a just-posted GRN its carton label. Idempotent.

    Called from post_grn, so the labels exist the moment the goods are receipted —
    which is when the warehouse actually needs them. A line that already has a
    bundle (a GRN unposted and posted again keeps none, but be safe) is skipped."""
    made = []
    for line in purchase.lines:
        if line.bundle:
            continue
        # counted by product_id, not through the `product` relationship: post_grn
        # has just assigned the ids in this same session, and the loaded
        # relationship still reads None until it is refreshed
        holders = line.splits if line.is_split else [line]
        items = {h.product_id for h in holders if h.product_id}
        # A carton label describes what is IN the carton, so it carries the
        # received quantity — never the billed one. A line the supplier invoiced
        # and did not deliver gets no label at all: there is no box to stick it on.
        qty = line.received_qty
        if not items or qty <= 0:
            continue
        b = models.Bundle(
            code=next_code(db),
            purchase_id=purchase.id, line_id=line.id,
            supplier_id=purchase.supplier_id,
            description=line.description or "(unnamed)",
            hsn=line.hsn, uom=line.uom or "PCS", qty=qty,
            item_count=len(items),
            grn_no=purchase.grn_no, invoice_number=purchase.invoice_number,
            status="stored",
        )
        db.add(b)
        # flush per bundle, not once at the end: next_code() counts rows, and a
        # pending insert is invisible to that count — so a two-line GRN handed both
        # cartons the same code and tripped the unique index
        db.flush()
        made.append(b)
    return made


def remove_for_purchase(db, purchase):
    """Drop a GRN's bundles when it is unposted.

    A bundle describes a receipt that, after unposting, did not happen — and its
    label points at products that may have just been deleted. Keeping it would
    leave a code on a rack resolving to nothing."""
    rows = db.query(models.Bundle).filter(
        models.Bundle.purchase_id == purchase.id).all()
    for b in rows:
        db.delete(b)
    db.flush()
    return [b.code for b in rows]


def resolve(db, code):
    """Find a bundle by scanned QR payload or by its printed code."""
    if not code:
        return None
    code = str(code).strip()
    payload = barcode_svc.parse_bundle_payload(code)
    if payload:
        code = payload.get("code") or ""
    if not code:
        return None
    return db.query(models.Bundle).filter(
        models.Bundle.code.ilike(code)).first()


def locate(db, bundle, location, by=None):
    """Put the carton away (or move it). This is what the bundle label is for."""
    location = (location or "").strip()
    if not location:
        raise ValueError("where the bundle is put away can't be blank")
    bundle.location = location
    bundle.located_at = dt.datetime.utcnow()
    bundle.located_by = by or bundle.located_by
    db.flush()
    return bundle


def open_bundle(db, bundle):
    """Mark the carton opened — its contents are being handled individually now."""
    if bundle.status == "stored":
        bundle.status = "opened"
        bundle.opened_at = dt.datetime.utcnow()
        db.flush()
    return bundle


def tag(db, bundle, by=None):
    """The second label: the carton's items are being tagged for sale.

    Returns the products whose labels should print. Refuses while any of them is
    still undetailed — an item label printed before someone has looked at the
    garment carries a thinner QR and a blank MRP, and the whole reason this step is
    separate from GRN is that by now the information exists."""
    prods = bundle.products
    if not prods:
        raise ValueError("this bundle has no items to tag")
    missing = [p for p in prods if not p.detailed]
    if missing:
        raise ValueError(
            f"{len(missing)} of {len(prods)} item(s) aren't detailed yet — "
            f"record size, colour and pricing first so the tags carry them "
            f"({', '.join((p.sku or '?') for p in missing[:4])}"
            f"{'…' if len(missing) > 4 else ''})")
    bundle.status = "tagged"
    bundle.tagged_at = dt.datetime.utcnow()
    bundle.tagged_by = by or bundle.tagged_by
    if not bundle.opened_at:
        bundle.opened_at = bundle.tagged_at
    db.flush()
    return prods


def summary(db):
    rows = db.query(models.Bundle).all()
    return {
        "total": len(rows),
        "stored": sum(1 for b in rows if b.status == "stored"),
        "opened": sum(1 for b in rows if b.status == "opened"),
        "tagged": sum(1 for b in rows if b.status == "tagged"),
        "unlocated": sum(1 for b in rows if not b.location and b.status != "tagged"),
    }
