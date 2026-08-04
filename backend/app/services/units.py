"""
Per-piece identities under a SKU.

Receiving 8 of ESSA-00008 makes ONE inventory record with a stock of 8 — the
master, the valuation and the ledger stay at SKU level, because that is what a
weighted-average cost and a stock report are about. This module adds the layer
below: ESSA-00008-001 … -008, one identity and one QR per garment, all pointing at
the same SKU.

Why not simply print the SKU label eight times? Because eight identical tags
cannot answer "which of these came back", "which one is missing", or "where did
this particular piece go". A serial per piece is the difference between counting
stock and tracking it.

Two limits are deliberate:

  * **Only countable units of measure.** Fabric billed in metres has no "piece" to
    serialise — 43.5 MTR is not 43 or 44 of anything — so nothing is generated for
    MTR/KG and the caller is told why rather than left wondering.
  * **A ceiling per receipt** (`MAX_PER_RECEIPT`). Serialising is for goods a human
    will physically tag; a 5,000-piece receipt is a different workflow, and quietly
    writing 5,000 rows and offering 5,000 labels would be the wrong answer to it.

`status` records where a piece is. Receipt sets it, and nothing consumes it yet:
dispatching by unit is the next step, and until it exists the honest reading of
this table is "the pieces this SKU received", not "the pieces still on the shelf".
"""
import datetime as dt
from .. import models

# a "piece" has to be a thing you can hold. Anything measured continuously is not.
COUNTABLE_UOM = {"PCS", "PC", "NOS", "NO", "SET", "SETS", "BOX", "PAIR", "PAIRS", "EA"}
MAX_PER_RECEIPT = 500


def can_serialise(uom, qty):
    """(ok, reason) — whether this receipt line becomes per-piece identities."""
    q = float(qty or 0)
    u = (uom or "PCS").strip().upper()
    if u not in COUNTABLE_UOM:
        return False, f"{u} is measured, not counted — no per-piece codes"
    if q <= 0:
        return False, "nothing received"
    if abs(q - round(q)) > 1e-6:
        return False, f"{q:g} is not a whole number of pieces"
    if round(q) > MAX_PER_RECEIPT:
        return False, (f"{round(q)} pieces is over the {MAX_PER_RECEIPT}-per-receipt "
                       f"limit for individual codes")
    return True, ""


def next_seq(db, product):
    """Piece numbers continue across receipts, so a re-buy adds -009, -010 …
    rather than starting again at -001 and colliding."""
    top = db.query(models.ProductUnit).filter(
        models.ProductUnit.product_id == product.id).order_by(
        models.ProductUnit.seq.desc()).first()
    return (top.seq + 1) if top and top.seq else 1


def unit_code(product, seq):
    return f"{product.sku or ('P' + str(product.id))}-{seq:03d}"


def create_for_receipt(db, product, qty, purchase=None, bundle=None):
    """Mint one identity per piece received. Returns the new ProductUnits ([] when
    the line isn't serialisable — see can_serialise)."""
    ok, _ = can_serialise(product.uom, qty)
    if not ok:
        return []
    seq = next_seq(db, product)
    made = []
    for i in range(int(round(float(qty)))):
        u = models.ProductUnit(
            product_id=product.id, code=unit_code(product, seq + i), seq=seq + i,
            purchase_id=purchase.id if purchase else None,
            bundle_id=bundle.id if bundle else None,
            status="in_stock", print_count=0,
        )
        db.add(u)
        made.append(u)
    db.flush()
    return made


def remove_for_purchase(db, purchase):
    """Drop the pieces a GRN created when it is unposted — those garments were
    never received, so their codes must not stay live."""
    rows = db.query(models.ProductUnit).filter(
        models.ProductUnit.purchase_id == purchase.id).all()
    for u in rows:
        db.delete(u)
    db.flush()
    return len(rows)


def backfill(db, product, purchase=None):
    """Give an existing product the piece codes it never got — for stock received
    before per-piece codes existed. Tops up to the product's stock on hand."""
    have = db.query(models.ProductUnit).filter(
        models.ProductUnit.product_id == product.id).count()
    want = float(product.stock_qty or 0)
    ok, reason = can_serialise(product.uom, want)
    if not ok:
        return [], reason
    missing = int(round(want)) - have
    if missing <= 0:
        return [], f"already has {have} piece code(s) for a stock of {want:g}"
    return create_for_receipt(db, product, missing, purchase), ""


def mark_printed(db, units, by=None):
    """Record that these labels went to a printer, so the screen can tell a first
    print from a reprint."""
    now = dt.datetime.utcnow()
    for u in units:
        u.print_count = (u.print_count or 0) + 1
        u.last_printed_at = now
        u.last_printed_by = by or u.last_printed_by
    db.flush()
    return units


def resolve(db, code):
    """The piece a scanned label refers to."""
    from . import barcode_svc
    return barcode_svc._resolve_unit_row(db, code)
