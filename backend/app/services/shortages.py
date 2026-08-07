"""
Shortage Entry — the gap between what a supplier billed and what the boxes held.

This is the receiving step that has to happen BEFORE a GRN posts, and it exists
because the alternative is a lie. A supplier bills 50 pieces and 40 arrive. The
breakdown screen demands that the rows add up to 50, so the person on the dock
either types ten pieces that do not exist — and inventory carries phantom stock
for ever after, priced, scannable and undispatchable — or leaves the receipt
unpostable and the goods unbooked. Recording the ten as short is the third
answer, and it is the true one.

Recorded here, at the dock, the difference is a document. Recorded nowhere, it
becomes invisible the instant the GRN posts: stock says 40, the invoice says 50,
and no screen in the system remembers that the two ever disagreed. That is the
whole argument for putting this in the Receive flow rather than in Inventory —
by the time anyone reaches Inventory, the only honest record of a shortage has
already been destroyed.

WHAT IT CHANGES
---------------
One number, everywhere:

    received = billed − short − damaged + excess          (PurchaseLine.received_qty)

`received` is what the attribute breakdown must add up to, what posting turns
into stock, and what goes in the carton. `billed` is left exactly as the supplier
wrote it, so invoice arithmetic and the payables side keep reconciling against
their own document — the same reason a breakdown never rewrites its line either.

WHAT IT IS WORTH
----------------
Not stored. A shortage is a fact about a *count*; its value is a fact about the
GRN, and lives on the line rate. `unit_cost()` is the one place that reads it,
and it is deliberately the same basis services/returns.py uses for a debit note:
what the supplier charged us per unit. Freezing a rate onto the shortage row
would put the same number in two places and let them drift; a posted GRN can no
longer change its rates anyway, so deriving is both simpler and stable.

Excess is taken into stock at that same billed rate rather than by spreading the
invoice total over the larger count. It is a choice, so it is worth stating: an
over-delivery is normally either invoiced later or collected back, and in both
cases the piece is worth what that supplier charges for it. Re-averaging the
invoice across the extra units would instead mark every piece down on the
strength of a packing mistake, and then mark them back up when the supplementary
invoice arrives.

WHAT HAPPENS NEXT
-----------------
A shortage is a claim waiting to be made. `claimable()` hands the open ones to
services/returns.py, which raises them as debit-note lines at that same rate —
lines that reduce the payable without touching the stock ledger, because the
units they debit never entered it. Nobody re-counts anything: the count was done
once, by the person holding the box.

A shortage nobody intends to claim is `waive()`d — the supplier is re-sending, or
it is too small to be worth the paperwork — and stays on the record either way.
"""
import datetime as dt
from .. import models

#: quantities are floats; compare with the tolerance the GRN posts with
QTY_TOLERANCE = 0.001

#: kind -> how it reads on screen. Order is the order the UI offers them in.
KINDS = {
    "short": "Short — billed but not in the box",
    "damaged": "Damaged — arrived unusable, rejected at the dock",
    "excess": "Excess — more arrived than was billed",
}

#: reasons offered on the phone; free text is accepted too, so the list is a
#: convenience and never a constraint
REASONS = ["Not in box", "Short packed", "Torn / cut", "Wet / stained",
           "Wrong item sent", "Soiled", "Broken packing", "Short in transit"]


def _f(v, default=0.0):
    if v is None or v == "":
        return default
    return float(v)


# ---------------------------------------------------------------------------
#  Reading
# ---------------------------------------------------------------------------
def line_totals(line):
    """The shortage arithmetic for one GRN line.

    `missing` is what will never become stock (short + damaged) and is therefore
    what can be claimed; `net` is the signed effect on the count, so a line with
    both a shortage and an excess nets out the way the boxes actually did."""
    short = damaged = excess = 0.0
    for s in line.shortages:
        q = _f(s.qty)
        if s.kind == "excess":
            excess += q
        elif s.kind == "damaged":
            damaged += q
        else:
            short += q
    billed = _f(line.qty)
    missing = short + damaged
    return {
        "billed_qty": round(billed, 3),
        "short_qty": round(short, 3),
        "damaged_qty": round(damaged, 3),
        "excess_qty": round(excess, 3),
        "missing_qty": round(missing, 3),
        "net_qty": round(excess - missing, 3),
        "received_qty": round(billed - missing + excess, 3),
        "rows": len(line.shortages),
    }


def unit_cost(line):
    """What one missing unit cost — the rate the supplier billed on that line.

    The same basis a debit note is valued at, and for the same reason: a claim
    against a supplier can only carry what that supplier charged. There is no
    variant rate to prefer here, because a shortage is counted against the billed
    bundle, which is the only thing the supplier itemised."""
    return round(_f(line.rate if line is not None else 0), 4)


def value(sh):
    """What this shortage is worth against the supplier (0 for excess — that is
    goods in our favour, not a claim)."""
    if sh is None or not sh.claimable:
        return 0.0
    line = sh.line
    return round(_f(sh.qty) * unit_cost(line), 2)


def claimed_qty(db, sh, exclude_return_id=None):
    """How much of this shortage has already been put on a POSTED debit note.

    Counted across every debit note against the invoice, so raising two of them
    cannot quietly claim the same ten pieces twice."""
    if sh is None:
        return 0.0
    q = db.query(models.PurchaseReturnLine).join(
        models.PurchaseReturn,
        models.PurchaseReturnLine.return_id == models.PurchaseReturn.id).filter(
        models.PurchaseReturnLine.shortage_id == sh.id,
        models.PurchaseReturn.status == "posted")
    if exclude_return_id is not None:
        q = q.filter(models.PurchaseReturn.id != exclude_return_id)
    return round(sum(_f(r.qty) for r in q.all()), 3)


def status_of(db, sh):
    """open | claimed | part-claimed | waived — what has become of this shortage.

    'claimed' is derived from the posted debit notes rather than stored, so the
    two can never disagree; 'waived' is the one part a human states outright."""
    if not sh.claimable:
        return "excess"
    done = claimed_qty(db, sh)
    if done >= _f(sh.qty) - QTY_TOLERANCE:
        return "claimed"
    if sh.waived:
        return "waived"
    return "part-claimed" if done > 0 else "open"


def shortage_out(db, sh):
    """One shortage as the API and both UIs read it."""
    done = claimed_qty(db, sh)
    return {
        "id": sh.id, "purchase_id": sh.purchase_id, "line_id": sh.line_id,
        "kind": sh.kind, "kind_label": KINDS.get(sh.kind, sh.kind),
        "qty": _f(sh.qty), "variant": sh.variant, "reason": sh.reason,
        "note": sh.note, "recorded_by": sh.recorded_by,
        "recorded_at": sh.recorded_at.isoformat() if sh.recorded_at else None,
        "claimable": sh.claimable,
        # valued at the GRN line rate — the same basis as the debit note it feeds
        "rate": unit_cost(sh.line), "amount": value(sh),
        "claimed_qty": done,
        "open_qty": round(max(0.0, _f(sh.qty) - done), 3) if sh.claimable else 0.0,
        "waived": bool(sh.waived), "waived_reason": sh.waived_reason,
        "waived_by": sh.waived_by,
        "status": status_of(db, sh),
        "description": sh.line.description if sh.line else None,
    }


def purchase_summary(db, purchase):
    """Every shortage on a GRN, plus what the lot is worth. Drives the receiving
    screen's banner and the shortage register."""
    rows = [sh for line in purchase.lines for sh in line.shortages]
    claimable = [s for s in rows if s.claimable]
    open_rows = [s for s in claimable
                 if status_of(db, s) in ("open", "part-claimed")]
    return {
        "rows": [shortage_out(db, s) for s in rows],
        "count": len(rows),
        "short_qty": round(sum(_f(s.qty) for s in rows if s.kind == "short"), 3),
        "damaged_qty": round(sum(_f(s.qty) for s in rows if s.kind == "damaged"), 3),
        "excess_qty": round(sum(_f(s.qty) for s in rows if s.kind == "excess"), 3),
        "claimable_qty": round(sum(_f(s.qty) for s in claimable), 3),
        "claimable_value": round(sum(value(s) for s in claimable), 2),
        "open_qty": round(sum(_f(s.qty) - claimed_qty(db, s) for s in open_rows), 3),
        "open_value": round(sum((_f(s.qty) - claimed_qty(db, s)) * unit_cost(s.line)
                                for s in open_rows), 2),
        "editable": purchase.status != "posted",
    }


def claimable(db, purchase, exclude_return_id=None):
    """The shortages on this GRN still owed by the supplier, as
    (shortage, qty_to_claim). This is what makes the debit note write itself:
    the count was done at the dock, so nothing here asks anyone to count again.

    Waived rows are left out — someone decided not to claim them — as is anything
    a posted debit note has already answered."""
    out = []
    for line in purchase.lines:
        for sh in line.shortages:
            if not sh.claimable or sh.waived:
                continue
            left = round(_f(sh.qty) - claimed_qty(db, sh, exclude_return_id), 3)
            if left > QTY_TOLERANCE:
                out.append((sh, left))
    return out


# ---------------------------------------------------------------------------
#  Writing
# ---------------------------------------------------------------------------
def set_line_shortages(db, line, rows, by=None):
    """Replace a line's shortage rows ([] clears them). ValueError on bad input.

    Only a draft can be edited. Once a GRN posts, its shortages are part of a
    financial record — the stock it did *not* take in was decided by them — so
    they change the same way everything else on a posted GRN does: unpost, fix,
    post again."""
    if line.purchase.status == "posted":
        raise ValueError("this GRN is posted — its shortages can no longer change; "
                         "unpost it to correct them")

    billed = _f(line.qty)
    clean = []
    for r in rows or []:
        kind = (str(r.get("kind") or "short").strip().lower())
        if kind not in KINDS:
            raise ValueError(f"“{kind}” isn't a shortage kind "
                             f"({', '.join(KINDS)})")
        try:
            qty = _f(r.get("qty"))
        except (TypeError, ValueError):
            raise ValueError("shortage quantity must be a number")
        if qty <= 0:
            raise ValueError("a shortage of zero isn't a shortage — "
                             "enter how many were missing, damaged or extra")
        clean.append(dict(
            kind=kind, qty=qty,
            variant=(str(r.get("variant") or "").strip() or None),
            reason=(str(r.get("reason") or "").strip() or None),
            note=(str(r.get("note") or "").strip() or None),
            recorded_by=(str(r.get("recorded_by") or by or "").strip() or None),
        ))

    # Claiming more than was billed is not a shortage, it is an arithmetic error —
    # and it would drive the received quantity negative, which no breakdown could
    # then balance against.
    missing = sum(c["qty"] for c in clean if c["kind"] != "excess")
    if missing > billed + QTY_TOLERANCE:
        raise ValueError(f"{missing:g} short or damaged out of {billed:g} billed — "
                         f"that is more than the supplier invoiced on this line")

    line.shortages.clear()          # delete-orphan drops the previous rows
    db.flush()
    made = []
    for c in clean:
        sh = models.GrnShortage(purchase_id=line.purchase_id,
                                recorded_at=dt.datetime.utcnow(), **c)
        # appended to the relationship rather than inserted by line_id, so
        # `line.received_qty` is right immediately — a caller that goes straight
        # on to post (as the phone does) must not read a stale collection
        line.shortages.append(sh)
        made.append(sh)
    db.flush()
    return made


def waive(db, sh, reason=None, by=None):
    """Accept a shortage instead of claiming it — the supplier is sending the
    balance, or it is too small to raise a debit note for. The row stays on the
    record; it just stops being offered for claim."""
    if not sh.claimable:
        raise ValueError("an excess isn't a claim, so there is nothing to waive")
    if claimed_qty(db, sh) > QTY_TOLERANCE:
        raise ValueError("a debit note has already claimed this shortage")
    sh.waived = True
    sh.waived_reason = (reason or "").strip() or None
    sh.waived_by = (by or "").strip() or None
    sh.waived_at = dt.datetime.utcnow()
    db.flush()
    return sh


def unwaive(db, sh):
    """Put a waived shortage back in play — the supplier never did send it."""
    sh.waived = False
    sh.waived_reason = None
    sh.waived_by = None
    sh.waived_at = None
    db.flush()
    return sh


def register(db, supplier_id=None, status=None):
    """Every shortage across every GRN — the Shortage Register report.

    Draft GRNs are included on purpose: a shortage recorded at the dock is worth
    seeing before the receipt is posted, which is exactly when someone can still
    ring the supplier about it."""
    q = db.query(models.GrnShortage).join(
        models.Purchase, models.GrnShortage.purchase_id == models.Purchase.id)
    if supplier_id:
        q = q.filter(models.Purchase.supplier_id == supplier_id)
    rows = []
    for sh in q.order_by(models.GrnShortage.id).all():
        st = status_of(db, sh)
        if status and st != status:
            continue
        p = sh.purchase
        d = shortage_out(db, sh)
        d.update({
            "supplier": p.supplier.name if p and p.supplier else "",
            "invoice_number": p.invoice_number if p else "",
            "invoice_date": p.invoice_date if p else "",
            "grn_no": p.grn_no if p else "",
            "grn_status": p.status if p else "",
        })
        rows.append(d)
    return rows
