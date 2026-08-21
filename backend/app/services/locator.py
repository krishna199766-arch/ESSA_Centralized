"""Item Locator: the parts of one item's account that live in other modules.

Somebody is holding a garment. `routers/inventory.locate` already answers what it
is, where it came from, where it is, where it went and what has moved — the five
questions that used to need five screens. This is the rest of the answer, and
every block in it is a question the floor actually asks while holding the piece:

  * **the consignment** — which lorry brought it, on whose LR, through which
    agent. That is in the transport register, keyed at the office days before the
    invoice was ever read, and it is what somebody chasing a delivery needs.
  * **stock age** — how long it has been standing. A number of days is the single
    most useful thing you can say about a garment nobody has sold, and the LR
    register already carries the holding period it was bought against, so the
    figure can say whether it is late as well as how old it is.
  * **the money** — cost, cost with the purchase tax on it, what it sells for,
    and the margin between them. Four figures that exist on four screens and are
    never seen together, which is exactly when a margin gets quietly lost.
  * **the count, taken apart** — purchased, transferred out, returned, adjusted,
    short and damaged. The stock figure is one number and it is the end of a
    story; these are the story.
  * **where the pieces are** — the destinations they were sent to, what each was
    sent and accepted, and what the till has sold there.

Nothing here writes. Every block degrades to None or an empty list rather than
raising: an item with no consignment, no sales and no transfers is a perfectly
ordinary item, and a locator that refuses to open because one module has nothing
to say about a garment is worse than one that says so.
"""
import datetime as dt

from .. import models

#: kinds a stock movement is written under, and what each means to a person
#: standing in the warehouse. See inventory._receive_into_stock and its siblings.
MOVEMENT_KINDS = [
    ("inward", "Purchased", "received against a GRN"),
    ("outward", "Transferred", "dispatched to a store or customer"),
    ("return", "Returned", "sent back to the supplier on a debit note"),
    ("adjustment", "Journal", "physical-count correction"),
    ("reversal", "Reversed", "a GRN that was unposted"),
]


def _day(v):
    """A date out of a column that may hold a date, a datetime or a string."""
    if not v:
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()
    return str(v)[:10] or None


def _parse_day(v):
    d = _day(v)
    if not d:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(d, fmt).date()
        except ValueError:
            continue
    return None


def _num(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
#  Where the lorry came from
# ---------------------------------------------------------------------------
def consignment_of(db, purchase):
    """The transport-register row behind a receipt, or None.

    The register is keyed at the office when the delivery arrives, days before
    anybody reads the invoice, and `lr_link` ties the two together by document.
    So a garment can be traced back past its own invoice to the lorry that
    brought it — which is the question asked when a delivery is being chased,
    and the one thing the GRN itself cannot answer.
    """
    if not purchase or not purchase.document_id:
        return None
    lr = (db.query(models.LREntry)
            .filter(models.LREntry.invoice_document_id == purchase.document_id)
            .order_by(models.LREntry.id.desc()).first())
    if not lr:
        return None
    return {
        "id": lr.id,
        "lr_entry_no": lr.lr_entry_no, "lr_entry_date": _day(lr.lr_entry_date),
        "lr_no": lr.lr_no, "lr_date": _day(lr.lr_date),
        "mode": lr.lr_mode, "transport": lr.transport,
        "recv_date": _day(lr.recv_date),
        "supplier_name": lr.supplier_name,
        "agent": lr.agent, "agent_commission": lr.agent_commission,
        "inv_no": lr.inv_no, "inv_date": _day(lr.inv_date),
        "qty": lr.qty, "amount": lr.amount,
        "bundle": lr.bundle, "boxes": lr.boxes,
        "purchase_manager": lr.purchase_manager,
        "stock_holding_days": lr.stock_holding_days,
        "auto_transfer_location": lr.auto_transfer_location,
        "additional_margin": lr.additional_margin,
        "paid_topay": lr.paid_topay,
    }


def stock_age(purchase, consignment=None, today=None):
    """How long this has been standing, and whether that is longer than intended.

    Counted from the day the goods were RECEIVED, not the day the supplier dated
    the invoice: a bill written on the 11th and delivered on the 18th is seven
    days of somebody else's time, and charging it to this garment's age would
    make every consignment look stale on arrival.

    `stock_holding_days` comes off the transport register — the period this
    delivery was bought against — so "48 days" can also say "and it was meant to
    move in 30".
    """
    today = today or dt.date.today()
    received = None
    if purchase is not None:
        received = (purchase.posted_at.date() if purchase.posted_at
                    else _parse_day(purchase.invoice_date))
    if received is None and consignment:
        received = (_parse_day(consignment.get("recv_date"))
                    or _parse_day(consignment.get("lr_entry_date"))
                    or _parse_day(consignment.get("lr_date")))
    if received is None:
        return None
    days = (today - received).days
    holding = None
    if consignment and consignment.get("stock_holding_days"):
        try:
            holding = int(float(consignment["stock_holding_days"]))
        except (TypeError, ValueError):
            holding = None
    return {
        "received_on": received.isoformat(),
        "days": max(0, days),
        "holding_days": holding,
        "overdue_by": (max(0, days - holding) if holding else None),
    }


# ---------------------------------------------------------------------------
#  What it cost, and what that leaves
# ---------------------------------------------------------------------------
def pricing_of(product, line=None, purchase=None):
    """Cost, cost with purchase tax on it, selling price, and the gap between.

    Four figures kept on four screens. A margin is the difference between two of
    them and nobody can check one they cannot see, so they are put side by side
    here with the arithmetic between them written out rather than implied.

    The tax rate is the reference invoice's own — tax over taxable, exactly as
    `returns._effective_tax_rate` derives it for a debit note — because that is
    the rate this consignment was actually billed at, not a rate looked up
    against an HSN that may have moved since.
    """
    cost = _num(product.avg_cost)
    last = _num(line.rate) if line is not None and line.rate is not None else _num(product.last_rate)
    rate = 0.0
    if purchase is not None and purchase.taxable_total:
        rate = (purchase.tax_total or 0) / purchase.taxable_total
    tax_pct = round(rate * 100, 2)
    net_cost = round(cost * (1 + rate), 2)

    mrp = _num(product.mrp) or None
    sell = _num(product.sale_price) or None
    disc_pct = product.sale_discount_pct
    discount = round(mrp - sell, 2) if (mrp and sell) else None

    # Margin on the SELLING price, which is how this trade states it: 770 that
    # cost 540 is a 29.9% margin, not a 42.6% mark-up on cost. Both are true and
    # they are different numbers, so the one shown says which it is.
    margin = round(sell - cost, 2) if (sell and cost) else None
    margin_pct = round((margin / sell) * 100, 2) if (margin is not None and sell) else None
    net_margin = round(sell - net_cost, 2) if (sell and net_cost) else None
    net_margin_pct = (round((net_margin / sell) * 100, 2)
                      if (net_margin is not None and sell) else None)
    markup_pct = round((margin / cost) * 100, 2) if (margin is not None and cost) else None
    return {
        "cost": cost or None,
        "last_rate": last or None,
        "purchase_tax_pct": tax_pct or None,
        "net_cost": net_cost or None,
        "mrp": mrp, "sale_price": sell,
        "sale_discount_pct": disc_pct,
        "discount": discount,
        "margin": margin, "margin_pct": margin_pct,
        "net_margin": net_margin, "net_margin_pct": net_margin_pct,
        "markup_pct": markup_pct,
    }


# ---------------------------------------------------------------------------
#  The stock figure, taken apart
# ---------------------------------------------------------------------------
def warehouse_stock(db, product):
    """Where the stock figure came from: every movement, totalled by what it was.

    `stock_qty` is one number and it is the END of a story — 40 pieces says
    nothing about the 50 that came, the 8 that went to a store and the 2 counted
    away. These are that story, and they add up to it.

    Short and damaged are counted separately and are NOT part of the sum: they
    never became stock (see models.GrnShortage). They are here because "the
    invoice said 50" is the next question after "why is it 40".
    """
    totals = {}
    for kind, _, _ in MOVEMENT_KINDS:
        rows = (db.query(models.StockMovement)
                  .filter(models.StockMovement.product_id == product.id,
                          models.StockMovement.kind == kind).all())
        totals[kind] = round(sum(float(m.qty_delta or 0) for m in rows), 3)

    short = damaged = excess = 0.0
    lines = (db.query(models.PurchaseLine)
               .filter(models.PurchaseLine.product_id == product.id).all())
    line_ids = [l.id for l in lines]
    if line_ids:
        for sh in (db.query(models.GrnShortage)
                     .filter(models.GrnShortage.line_id.in_(line_ids)).all()):
            qty = float(sh.qty or 0)
            if sh.kind == "short":
                short += qty
            elif sh.kind == "damaged":
                damaged += qty
            elif sh.kind == "excess":
                excess += qty

    return {
        "kinds": [{"kind": k, "label": label, "why": why,
                   "qty": abs(totals.get(k, 0.0)), "signed": totals.get(k, 0.0)}
                  for k, label, why in MOVEMENT_KINDS],
        "purchased": abs(totals.get("inward", 0.0)),
        "transferred": abs(totals.get("outward", 0.0)),
        "returned": abs(totals.get("return", 0.0)),
        "adjusted": totals.get("adjustment", 0.0),
        "reversed": abs(totals.get("reversal", 0.0)),
        "short": round(short, 3),
        "damaged": round(damaged, 3),
        "excess": round(excess, 3),
        "stock": round(float(product.stock_qty or 0), 3),
    }


# ---------------------------------------------------------------------------
#  Where the pieces went, by destination
# ---------------------------------------------------------------------------
def locations_of(db, product, sold=None):
    """Retail stock: one row per destination these pieces were sent to.

    Sent and accepted are kept apart on purpose. They differ exactly when a
    transfer went wrong, and averaging them into a single "stock" figure is how
    a discrepancy stops being visible the moment it is recorded.

    `sold` is the till's count for this item, which belongs to the shop and not
    to us — so it is shown against the destination only when the shop is
    actually there to be asked.
    """
    rows = {}
    for ol in (db.query(models.StockOutwardLine)
                 .filter(models.StockOutwardLine.product_id == product.id).all()):
        ow = ol.outward
        if ow is None or ow.status == "draft":
            continue                      # not dispatched yet: nothing is there
        where = ow.to_destination or "(unnamed destination)"
        row = rows.setdefault(where, {
            "location": where, "sent": 0.0, "accepted": 0.0, "notes": 0,
        })
        row["sent"] = round(row["sent"] + float(ol.qty or 0), 3)
        acc = ol.accepted_qty if ol.accepted_qty is not None else ol.qty
        row["accepted"] = round(row["accepted"] + float(acc or 0), 3)
        row["notes"] += 1
    out = []
    for row in rows.values():
        row["short_by"] = round(row["sent"] - row["accepted"], 3)
        row["price"] = product.mrp
        row["discount"] = (round(float(product.mrp) - float(product.sale_price), 2)
                           if product.mrp and product.sale_price else None)
        row["selling_price"] = product.sale_price
        out.append(row)
    out.sort(key=lambda r: (-r["accepted"], r["location"]))
    if sold is not None and out:
        # the shop is one shop; when there is exactly one destination the till's
        # count belongs to it, and when there are several we cannot say which
        if len(out) == 1:
            out[0]["sold"] = sold
    return out


def transfers_of(db, product, limit=25):
    """Every dispatch of this item, packed and received, newest first."""
    out = []
    for ol in (db.query(models.StockOutwardLine)
                 .filter(models.StockOutwardLine.product_id == product.id)
                 .order_by(models.StockOutwardLine.id.desc()).limit(limit).all()):
        ow = ol.outward
        if ow is None:
            continue
        acc = ol.accepted_qty if ol.accepted_qty is not None else None
        out.append({
            "outward_id": ow.id, "code": ow.code, "status": ow.status,
            "from": ow.from_location, "to": ow.to_destination,
            "packed_on": _day(ow.date),
            "packed_by": ow.packed_by,
            "packed_qty": ol.qty,
            "received_on": _day(ow.received_date or ow.received_at),
            "received_by": ow.received_by,
            "received_qty": acc,
            "short_by": (round(float(ol.qty or 0) - float(acc), 3)
                         if acc is not None else None),
            "rate": ol.rate,
        })
    return out
