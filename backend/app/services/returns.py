"""
Purchase Return service (Warehouse / Purchase Return — debit note).

build_from_purchase(purchase): draft return pre-filled with what was RECEIVED on
    that invoice — one row per stock item, so a billed bundle that was broken
    down at GRN comes back as its variants (L / Red, M / Blue …) rather than as
    the bundle line, which never became stock and cannot be returned as itself.
    Return-qty defaults to 0; the user sets how many of each to send back.
post(ret): for each line with qty > 0, reverse stock (negative StockMovement,
    kind='return') and value the debit note. The debit total reduces the
    supplier's payable for the referenced invoice (see payments.invoice_outstanding).
Idempotent — a return posts once.

VALUATION — the rule this module exists to enforce
--------------------------------------------------
A debit note is valued at the PURCHASE / RECEIVED price: the GRN cost of the
exact item going back. Never the sale price, never the MRP. Two reasons, and both
bite in money:

  * A debit note settles a supplier account. The supplier invoiced us at their
    rate; we can only debit them back at that same rate, or the payable stops
    reconciling against their ledger and the difference surfaces as a dispute.
  * Valuing a return at the retail price would credit us the margin on goods we
    never sold, overstating both the settlement and the stock write-back.

`grn_cost()` below is the only place that decides the figure, and `post()`
re-derives every line from the GRN before valuing — so a draft raised before a
correction, or a rate that got stale in some other way, cannot reach a posted
debit note. Nothing in this module ever reads Product.sale_price or Product.mrp.

TWO KINDS OF LINE
-----------------
A debit note reduces the payable for two quite different reasons, and this module
carries both because the supplier's account does not care which it was:

  * **goods going back** — we received them, we are returning them, and posting
    reverses the stock they added;
  * **goods that never came** — the supplier billed fifty and sent forty. The ten
    were counted at the dock and recorded as a GrnShortage (services/shortages.py),
    and posting must NOT touch stock, because those units never entered it.
    `PurchaseReturnLine.shortage_id` is what tells the two apart.

The second kind is why shortages are recorded at receiving rather than worked out
later: the quantity is already a fact by the time a debit note is raised, so
`build_from_purchase` fills it in and nobody re-counts anything.
"""
import datetime as dt
from .. import models
from . import shortages as shortage_svc
from . import dates


def _next_code(db):
    n = db.query(models.PurchaseReturn).count() + 1
    return f"PR-{n:05d}"


def _effective_tax_rate(purchase):
    """tax / taxable from the reference purchase, so the debit note carries the
    same GST the invoice did."""
    if purchase and purchase.taxable_total:
        return (purchase.tax_total or 0) / purchase.taxable_total
    return 0.0


# ---------------------------------------------------------------------------
#  Cost basis
# ---------------------------------------------------------------------------
def grn_cost(line=None, split=None, product=None):
    """The unit price this item was RECEIVED at — the debit note's only basis.

    Preference order, most specific first:
      1. the variant's own rate from the GRN breakdown — a bundle billed as one
         rate can still have been broken down with a different rate per variant,
         and that is what this piece actually cost;
      2. the invoice line rate, i.e. what the supplier billed per unit;
      3. the product's weighted-average purchase cost, for a return line with no
         surviving GRN row behind it.

    Every one of those is a price we PAID. Product.sale_price and Product.mrp are
    deliberately absent: see the module docstring."""
    candidates = [
        split.effective_rate if split is not None else None,
        line.rate if line is not None else None,
        product.avg_cost if product is not None else None,
    ]
    for v in candidates:
        if v is not None and float(v) > 0:
            return round(float(v), 4)
    return 0.0


def cost_source(line=None, split=None, product=None):
    """Which of the three the rate came from — shown on screen so the basis of a
    debit note is visible, not just asserted."""
    if split is not None and split.rate is not None and float(split.rate) > 0:
        return "grn_variant_rate"
    if line is not None and line.rate and float(line.rate) > 0:
        return "invoice_line_rate"
    if product is not None and product.avg_cost and float(product.avg_cost) > 0:
        return "weighted_avg_cost"
    return "unknown"


def _receivers(purchase):
    """The rows of a GRN that actually became stock, as (line, split|None).

    A line that was broken down did NOT receive stock — its variants did — so
    returning "the line" would reverse a quantity against a product that never
    existed. This is what makes the returnable set the received set."""
    out = []
    for line in purchase.lines:
        if line.is_split:
            out.extend((line, sp) for sp in line.splits)
        else:
            out.append((line, None))
    return out


def source_of(db, l):
    """The GRN row a return line came back from: (purchase_line, split|None).

    A shortage claim resolves to its billed line and no variant: the supplier
    itemised a bundle, so the missing pieces are counted — and valued — against
    that bundle's rate.

    Falls back to matching on the product for rows created before the link
    columns existed, so an old draft still re-values from the right receipt."""
    if l.shortage_id:
        sh = db.get(models.GrnShortage, l.shortage_id)
        return (sh.line if sh else None), None
    line = db.get(models.PurchaseLine, l.purchase_line_id) if l.purchase_line_id else None
    split = db.get(models.PurchaseLineSplit, l.split_id) if l.split_id else None
    if line or split:
        return (line or (split.line if split else None)), split
    purchase = l.ret.purchase if l.ret else None
    if not (purchase and l.product_id):
        return None, None
    for pl, sp in _receivers(purchase):
        holder = sp if sp is not None else pl
        if holder.product_id == l.product_id:
            return pl, sp
    return None, None


def _source_key(l):
    """Identifies the received row a return line consumes, for tallying how much
    of it has already gone back across several debit notes."""
    if l.shortage_id:
        return ("shortage", l.shortage_id)
    if l.split_id:
        return ("split", l.split_id)
    if l.purchase_line_id:
        return ("line", l.purchase_line_id)
    return ("product", l.product_id)


def returnable(db, ret, l):
    """{purchased, already_returned, available} for one return line.

    'Already returned' counts POSTED debit notes other than this one against the
    same invoice, so two partial returns of the same item can't quietly add up to
    more than was bought.

    For a shortage claim the ceiling is the shortage, not the purchase: the
    supplier can only be debited for the ten pieces the dock counted missing, and
    only once."""
    if l.shortage_id:
        sh = db.get(models.GrnShortage, l.shortage_id)
        qty = float(sh.qty or 0) if sh else 0.0
        prior = shortage_svc.claimed_qty(db, sh, exclude_return_id=ret.id) if sh else 0.0
        return {"purchased": round(qty, 3), "already_returned": round(prior, 3),
                "available": round(qty - prior, 3)}
    line, split = source_of(db, l)
    holder = split if split is not None else line
    purchased = float(getattr(holder, "qty", 0) or 0) if holder is not None else 0.0
    prior = 0.0
    if ret.purchase_id:
        rows = db.query(models.PurchaseReturnLine).join(
            models.PurchaseReturn,
            models.PurchaseReturnLine.return_id == models.PurchaseReturn.id).filter(
            models.PurchaseReturn.purchase_id == ret.purchase_id,
            models.PurchaseReturn.status == "posted",
            models.PurchaseReturn.id != ret.id).all()
        key = _source_key(l)
        prior = sum(float(r.qty or 0) for r in rows if _source_key(r) == key)
    return {"purchased": round(purchased, 3), "already_returned": round(prior, 3),
            "available": round(purchased - prior, 3)}


# ---------------------------------------------------------------------------
#  Build / edit / post
# ---------------------------------------------------------------------------
def build_from_purchase(db, purchase, shortages_only=False):
    """A draft debit note against one invoice.

    Two sets of lines, and they behave differently on purpose. Goods we received
    are listed at qty 0 — how many go back is a decision someone still has to
    make. Shortages are listed at the quantity the dock counted, because that is
    not a decision: the pieces are missing, and by how many was settled when the
    boxes were opened. That is the payoff for recording shortages at receiving —
    this half of the debit note fills itself in.

    `shortages_only` raises a note for the missing goods alone, which is the
    common case: a short delivery is claimed on its own, well before anyone knows
    whether the goods that did arrive are any good."""
    existing = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.purchase_id == purchase.id,
        models.PurchaseReturn.status == "draft").first()
    if existing:
        # a shortage recorded (or re-opened) since the draft was raised still
        # belongs on it — otherwise the claim silently misses it
        sync_shortage_lines(db, existing)
        return apply_grn_rates(db, existing)

    ret = models.PurchaseReturn(
        code=_next_code(db), supplier_id=purchase.supplier_id,
        purchase_id=purchase.id, invoice_number=purchase.invoice_number,
        date=None, status="draft",
    )
    db.add(ret)
    db.flush()
    if not shortages_only:
        for pl, sp in _receivers(purchase):
            holder = sp if sp is not None else pl
            product = db.get(models.Product, holder.product_id) if holder.product_id else None
            label = sp.variant_label if sp is not None else None
            db.add(models.PurchaseReturnLine(
                return_id=ret.id, product_id=holder.product_id,
                purchase_line_id=pl.id, split_id=sp.id if sp is not None else None,
                # our SKU when the item has one — that is the code on the goods; the
                # supplier's printed code only when they gave us one
                barcode=(product.sku or product.barcode) if product else pl.barcode,
                description=f"{pl.description} · {label}" if label else pl.description,
                hsn=pl.hsn, uom=pl.uom,
                qty=0.0, rate=grn_cost(pl, sp, product), amount=0.0,
            ))
    sync_shortage_lines(db, ret)
    db.flush()
    return ret


def shortage_description(sh):
    """How a missing-goods line reads on the debit note. It has to say *why* the
    supplier is being debited for something we never sent back, because the
    obvious reading of a return line — "here are your goods" — is wrong here."""
    line = sh.line
    what = (line.description if line else None) or "(unnamed)"
    bits = [shortage_svc.KINDS.get(sh.kind, sh.kind).split(" — ")[0].lower()]
    if sh.variant:
        bits.insert(0, sh.variant)
    if sh.reason:
        bits.append(sh.reason.lower())
    return f"{what} · not received ({', '.join(bits)})"


def sync_shortage_lines(db, ret):
    """Put every unclaimed shortage on this draft, and take off the ones that no
    longer belong.

    Run whenever a draft is opened, so a shortage recorded — or waived — after the
    note was raised is reflected without anyone rebuilding it. Only untouched
    lines are removed: a quantity someone typed is theirs to change, not ours."""
    if ret.status == "posted" or not ret.purchase:
        return ret
    open_now = {sh.id: qty for sh, qty in
                shortage_svc.claimable(db, ret.purchase, exclude_return_id=ret.id)}
    have = {}
    for l in list(ret.lines):
        if not l.shortage_id:
            continue
        if l.shortage_id not in open_now:
            # waived, or already answered by another posted note
            if not (l.qty or 0):
                ret.lines.remove(l)
                db.delete(l)
            continue
        have[l.shortage_id] = l

    for sid, qty in open_now.items():
        sh = db.get(models.GrnShortage, sid)
        if not sh:
            continue
        rate = shortage_svc.unit_cost(sh.line)
        l = have.get(sid)
        if l is None:
            db.add(models.PurchaseReturnLine(
                return_id=ret.id, shortage_id=sh.id,
                # no product_id: these units never became one. A missing garment
                # has no SKU, no QR and no stock row, and pointing this line at
                # some other product's record would invite posting to move it.
                product_id=None,
                purchase_line_id=sh.line_id,
                barcode=(sh.line.barcode if sh.line else None),
                description=shortage_description(sh),
                hsn=(sh.line.hsn if sh.line else None),
                uom=(sh.line.uom if sh.line else None),
                qty=qty, rate=rate, amount=round(qty * rate, 2)))
        elif not (l.qty or 0):
            l.qty = qty                      # untouched line follows the shortage
            l.amount = round(qty * rate, 2)
    db.flush()
    return ret


def apply_grn_rates(db, ret):
    """Re-derive every line's rate from the GRN it came from.

    Run on open and again at post, so the rate a debit note is valued at is
    always the received price as the GRN records it TODAY — not whatever was
    copied onto the draft when it was raised."""
    for l in ret.lines:
        line, split = source_of(db, l)
        product = db.get(models.Product, l.product_id) if l.product_id else None
        rate = grn_cost(line, split, product)
        if rate and rate != l.rate:
            l.rate = rate
            l.amount = round(float(l.qty or 0) * rate, 2)
        if line is not None and not l.purchase_line_id:      # backfill the links
            l.purchase_line_id = line.id
        if split is not None and not l.split_id:
            l.split_id = split.id
    db.flush()
    return ret


def set_lines(db, ret, line_qtys):
    """line_qtys: {return_line_id: qty}. Recomputes each line amount.

    Quantities only — a rate is never accepted from the client. What an item cost
    is a fact of its GRN, so letting a screen post one would be letting it decide
    what the supplier is debited."""
    for l in ret.lines:
        if l.id in line_qtys:
            q = float(line_qtys[l.id] or 0)
            l.qty = q
            l.amount = round(q * (l.rate or 0), 2)
    db.flush()
    return ret


def post(db, ret, reason=None, date=None):
    if ret.status == "posted":
        return {"ok": False, "error": "already posted"}
    apply_grn_rates(db, ret)              # value at the GRN cost, always
    active = [l for l in ret.lines if (l.qty or 0) > 0]
    if not active:
        return {"ok": False, "error": "no lines to return (set a return qty > 0)"}

    # A debit note for more than the invoice delivered is not a settlement, it is
    # an overclaim — and it would reverse stock that this receipt never brought in.
    over = []
    for l in active:
        r = returnable(db, ret, l)
        if r["purchased"] and float(l.qty) > r["available"] + 1e-6:
            noun = "claiming" if l.is_shortage_claim else "returning"
            over.append(f"“{(l.description or '')[:40]}”: {noun} {float(l.qty):g} "
                        f"of {r['available']:g} available"
                        + (f" ({r['purchased']:g} {'short' if l.is_shortage_claim else 'received'}, "
                           f"{r['already_returned']:g} already claimed)"
                           if r["already_returned"] else ""))
    if over:
        return {"ok": False, "error": "more than was received — " + "; ".join(over)}

    taxable, short_lines, short_value = 0.0, 0, 0.0
    for l in active:
        qty = float(l.qty or 0)
        l.amount = round(qty * (l.rate or 0), 2)
        if l.is_shortage_claim:
            # goods that never arrived. The payable comes down at the GRN rate,
            # and the stock ledger is not touched — there is nothing to reverse,
            # and reversing "it" would take the shortage out of stock twice: once
            # by never receiving it, once again here.
            short_lines += 1
            short_value += l.amount or 0
        else:
            prod = db.get(models.Product, l.product_id) if l.product_id else None
            if prod:
                prod.stock_qty = round((prod.stock_qty or 0) - qty, 3)
                db.add(models.StockMovement(
                    product_id=prod.id, qty_delta=-qty, kind="return",
                    ref_type="purchase_return", ref_id=ret.id,
                    rate=l.rate or prod.avg_cost or 0, balance_after=prod.stock_qty,
                    note=f"Purchase return {ret.code} → {ret.supplier.name if ret.supplier else ''}".strip(),
                ))
        taxable += l.amount or 0

    rate = _effective_tax_rate(ret.purchase)
    ret.taxable_total = round(taxable, 2)
    ret.tax_total = round(taxable * rate, 2)
    ret.total = round(ret.taxable_total + ret.tax_total, 2)
    ret.reason = reason or ret.reason
    ret.date = dates.normalise(date) if date else ret.date
    ret.status = "posted"
    ret.posted_at = dt.datetime.utcnow()
    db.flush()
    return {"ok": True, "return_id": ret.id, "debit_total": ret.total,
            "taxable_total": ret.taxable_total, "tax_total": ret.tax_total,
            "cost_basis": "grn", "lines": len(active),
            # said separately because they settle the same way but mean opposite
            # things on the floor: one is stock leaving, one is stock that never came
            "shortage_lines": short_lines, "shortage_value": round(short_value, 2),
            "returned_lines": len(active) - short_lines}


def returns_against_purchase(db, purchase_id):
    rows = db.query(models.PurchaseReturn).filter(
        models.PurchaseReturn.purchase_id == purchase_id,
        models.PurchaseReturn.status == "posted").all()
    return round(sum(r.total or 0 for r in rows), 2)
