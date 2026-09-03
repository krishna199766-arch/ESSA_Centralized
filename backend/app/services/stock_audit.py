"""Counting the shelf against the books.

WHAT A COUNT IS FOR. The ledger says what should be there. A count says what is.
The gap between them is the finding, and the whole value of this module is that
it records that gap **as it was at the moment somebody looked** — which is why
every figure on an `AuditScan` is copied at scan time and never re-derived. A
count that reported today's stock figures would be a report, not a count.

WHAT IT DELIBERATELY DOES NOT DO. It never moves stock. Scanning a shelf and
finding it empty is evidence; deciding the books are wrong is a separate act,
made deliberately, through `inventory.adjust_stock`, which writes a real movement
with a reason on it. Wiring a count straight into the ledger would make the one
record meant to be independent of the books derived from them — and it would do
it silently, from a phone, in a warehouse.

THREE RESULTS, NOT TWO. The request asks for green/red, and that is what the
screen shows, but the data underneath keeps three:

    available      the code resolved and this warehouse holds some
    not_available  the code resolved and this warehouse holds none
    unknown        the code resolved to nothing at all

The third is not a shelf problem, it is a master-data problem — an unrecognised
label means a product that was never created, a tag from another system, or a
misprint. Folding it into "not available" would send somebody to look for stock
that was never supposed to be there.

RE-SCANNING DOES NOT DOUBLE-COUNT. One row per distinct code per session; a
repeat bumps `times_seen` and comes back flagged, so the screen can say "already
counted" instead of the register quietly growing a second row for one garment.
"""
import datetime as dt

from sqlalchemy.orm import Session

from .. import models
from . import barcode_svc, numbering, scope, stock_locations

#: What a scan can conclude. Ordered as the screen groups them.
RESULTS = ["available", "not_available", "unknown"]


def next_code(db: Session, warehouse_id=None):
    """The next session number, stepping over anything already issued."""
    used = {c for (c,) in db.query(models.AuditSession.code).filter(
        models.AuditSession.code.isnot(None)).all()}
    return numbering.next_number(db, "stock_audit", warehouse_id=warehouse_id,
                                 is_taken=lambda code: code in used)


def open_session(db: Session, warehouse_id=None, by=None, note=None):
    """Start a count. One open session per warehouse at a time.

    Reusing an already-open session rather than refusing is deliberate: the phone
    is put down, the screen is closed, the app is reopened half an hour later,
    and the person expects to carry on counting — not to be told they already
    have a session and be left to find it.
    """
    existing = current_session(db, warehouse_id)
    if existing:
        return existing
    row = models.AuditSession(code=next_code(db, warehouse_id),
                              warehouse_id=warehouse_id, status="open",
                              started_by=by, note=note)
    db.add(row)
    db.flush()
    return row


def current_session(db: Session, warehouse_id=None):
    """The count in progress here, or None."""
    q = db.query(models.AuditSession).filter(models.AuditSession.status == "open")
    q = scope.audit_sessions(q, warehouse_id)
    return q.order_by(models.AuditSession.id.desc()).first()


def close_session(db: Session, session: models.AuditSession, by=None):
    if session.status == "closed":
        return session
    session.status = "closed"
    session.closed_at = dt.datetime.utcnow()
    session.closed_by = by
    return session


def _location_of(db: Session, product, warehouse_id):
    """Where this product is, if the warehouse records that at all.

    Best-effort by design: a bundle's rack is the nearest thing this system has
    to a shelf address, and many installs put nothing there. A blank comes back
    as None and the screen says nothing rather than an empty label, because a
    field that is always blank teaches people that blank is normal.
    """
    if product is None or not warehouse_id:
        return None
    row = (db.query(models.Bundle)
             .join(models.Purchase, models.Bundle.purchase_id == models.Purchase.id)
             .filter(models.Purchase.warehouse_id == warehouse_id,
                     models.Bundle.location.isnot(None),
                     models.Bundle.location != "")
             .order_by(models.Bundle.id.desc()).first())
    return row.location if row else None


def scan(db: Session, session: models.AuditSession, code: str, by=None):
    """Read one tag into a count and say what was found.

    Returns {scan, duplicate, product} — `duplicate` is what the screen turns
    into "already counted", and it is decided here rather than by the client so
    two handsets counting the same rack agree.
    """
    code = (code or "").strip()
    if not code:
        raise ValueError("nothing was scanned")
    if session.status != "open":
        raise ValueError(f"{session.code} is closed — start a new count")

    product = barcode_svc.resolve(db, code)
    # A per-piece label (ESSA-00008-003) is a garment, and resolve() answers it
    # with its product — which is the right answer for a count: the shelf holds
    # SKUs, and the piece is how one of them was labelled.
    if product is None:
        from . import units as unit_svc
        unit = unit_svc.resolve(db, code)
        product = unit.product if unit is not None else None

    if product is None:
        result, qty, location = "unknown", None, None
    else:
        qty = stock_locations.qty_at(db, product.id, session.warehouse_id)
        result = "available" if (qty or 0) > 0 else "not_available"
        location = _location_of(db, product, session.warehouse_id)

    existing = db.query(models.AuditScan).filter(
        models.AuditScan.session_id == session.id,
        models.AuditScan.code == code).first()
    if existing is not None:
        # Counted already. The row is not duplicated and the FINDINGS are not
        # rewritten — what the counter saw the first time is the record. Only
        # the fact that it was seen again is new.
        existing.times_seen = (existing.times_seen or 1) + 1
        existing.last_seen_at = dt.datetime.utcnow()
        return {"scan": existing, "duplicate": True, "product": product}

    row = models.AuditScan(
        session_id=session.id, code=code,
        product_id=product.id if product else None,
        result=result,
        # `description` is what this app calls a product's name — there is no
        # `name` column. Copied, not joined, so a product renamed or removed
        # afterwards cannot rewrite what the counter saw.
        product_name=(product.description if product else None),
        sku=(product.sku if product else None),
        stock_qty=qty, location=location, times_seen=1, scanned_by=by)
    db.add(row)
    db.flush()
    return {"scan": row, "duplicate": False, "product": product}


def totals(db: Session, session: models.AuditSession) -> dict:
    """The running tally the scan screen shows above the list."""
    counts = {r: 0 for r in RESULTS}
    for (result,) in db.query(models.AuditScan.result).filter(
            models.AuditScan.session_id == session.id).all():
        if result in counts:
            counts[result] += 1
    counts["scanned"] = sum(counts[r] for r in RESULTS)
    return counts


# ---------------------------------------------------------------------------
#  serialisation
# ---------------------------------------------------------------------------
def scan_out(s: models.AuditScan) -> dict:
    return {
        "id": s.id, "code": s.code, "product_id": s.product_id,
        "result": s.result,
        # Said in words as well as carried as a status, because the screen must
        # not depend on colour alone — see the request's §7 and the app's own
        # .pill convention.
        "label": {"available": "AVAILABLE", "not_available": "NOT AVAILABLE",
                  "unknown": "NOT FOUND"}.get(s.result, s.result),
        "product_name": s.product_name, "sku": s.sku,
        "stock_qty": s.stock_qty, "location": s.location,
        "times_seen": s.times_seen or 1,
        "scanned_at": s.scanned_at.isoformat() if s.scanned_at else None,
        "scanned_by": s.scanned_by,
    }


def session_out(db: Session, session: models.AuditSession, with_scans=True) -> dict:
    d = {
        "id": session.id, "code": session.code, "status": session.status,
        "warehouse_id": session.warehouse_id,
        # Named, not just numbered. A phone that sends no warehouse header has
        # one resolved for it, and the one thing a counter must be able to see is
        # which building the readings are being filed against.
        "warehouse": session.warehouse.name if session.warehouse else None,
        "note": session.note,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "started_by": session.started_by,
        "closed_at": session.closed_at.isoformat() if session.closed_at else None,
        "closed_by": session.closed_by,
        "totals": totals(db, session),
    }
    if with_scans:
        # Newest first: the scan just taken is the one being looked at, and a
        # counter should not have to scroll past two hundred rows to see it.
        d["scans"] = [scan_out(s) for s in reversed(session.scans)]
    return d
