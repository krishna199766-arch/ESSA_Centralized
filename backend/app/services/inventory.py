"""
GRN + inventory service — turns a confirmed extraction into stock.

Two phases, deliberately separated so a human can review before stock moves:

  build_grn_from_document(doc)  -> a DRAFT Purchase whose lines are matched to
                                   existing Products (by barcode, else by
                                   description+HSN+supplier). Lines with no match
                                   are flagged is_new_product.

  post_grn(purchase)            -> creates the new Products, appends one inward
                                   StockMovement per line, and updates each
                                   product's stock_qty + weighted-average cost.
                                   Idempotent: a purchase can only post once.

Weighted-average cost keeps valuation stable across purchases at different rates:
    new_avg = (old_qty*old_avg + in_qty*in_rate) / (old_qty + in_qty)
"""
import datetime as dt
from rapidfuzz import fuzz
from .. import models
from . import shortages as shortage_svc


def _norm(s):
    return (s or "").strip().lower()


# The attributes that make a breakdown row a distinct stock item. Same list the
# phone detail form and the QR payload carry, so a variant created at GRN is
# already the record the warehouse and the label expect.
SPLIT_ATTRS = ("size", "color", "material", "pattern", "fit", "product_type", "design_no")
# per-variant money fields — these describe price, never identity
SPLIT_PRICES = ("rate", "mrp", "sale_price", "sale_discount_pct")


def match_product(db, barcode, description, hsn, supplier_id, attrs=None):
    """Find an existing product for a purchase line. Returns Product or None.

    `attrs` is passed when matching a variant row from an attribute breakdown.
    Identity is then the WHOLE attribute tuple, compared exactly (blank included):
    the same description in a different colour or material is a different stock
    item, so an exact re-buy merges and anything new is created. Matching on only
    the attributes that happen to be filled in would quietly fold "L" into
    "L / Red" for whichever arrived first."""
    if barcode:
        p = db.query(models.Product).filter(models.Product.barcode == barcode).first()
        if p:
            return p
    # no barcode (AMS / Matoshree / Mehak): match on description + HSN, prefer same supplier
    q = db.query(models.Product)
    if hsn:
        q = q.filter(models.Product.hsn == hsn)
    candidates = q.all()
    best, best_score = None, 0
    for c in candidates:
        if attrs is not None and any(_norm(getattr(c, a, None)) != _norm(attrs.get(a))
                                     for a in SPLIT_ATTRS):
            continue
        score = fuzz.token_sort_ratio(_norm(description), _norm(c.description))
        if c.primary_supplier_id == supplier_id:
            score += 5  # tie-break toward the same supplier
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 90 else None


def _next_sku(db):
    n = db.query(models.Product).count() + 1
    # skip numbers already taken, so deleted rows can't cause a collision
    while db.query(models.Product).filter(models.Product.sku == f"ESSA-{n:05d}").first():
        n += 1
    return f"ESSA-{n:05d}"


def build_grn_from_document(db, doc):
    """Create (or return existing) a draft GRN for a confirmed document."""
    existing = db.query(models.Purchase).filter(
        models.Purchase.document_id == doc.id).first()
    if existing:
        return existing

    ex = doc.latest_extraction
    data = ex.data if ex else {}
    inv = data.get("invoice", {}) or {}
    tot = data.get("totals", {}) or {}
    meta = data.get("meta", {}) or {}

    purchase = models.Purchase(
        document_id=doc.id, supplier_id=doc.supplier_id,
        grn_no=meta.get("grn_no"), invoice_number=inv.get("number"),
        invoice_date=inv.get("date"),
        taxable_total=tot.get("taxable_total") or 0.0,
        tax_total=tot.get("tax_total") or 0.0,
        grand_total=tot.get("grand_total") or 0.0,
        status="draft",
    )
    db.add(purchase)
    db.flush()

    for it in data.get("line_items", []):
        match = match_product(db, it.get("barcode"), it.get("description"),
                              it.get("hsn"), doc.supplier_id)
        db.add(models.PurchaseLine(
            purchase_id=purchase.id,
            product_id=match.id if match else None,
            barcode=it.get("barcode"), description=it.get("description"),
            hsn=it.get("hsn"), qty=it.get("qty"), uom=it.get("uom") or "PCS",
            rate=it.get("rate"),
            amount=it.get("amount") if it.get("amount") is not None else it.get("taxable_value"),
            is_new_product=match is None,
        ))
    db.flush()
    return purchase


# ---------------------------------------------------------------------------
#  Size splits — one billed bundle line becomes one product per size
# ---------------------------------------------------------------------------
# quantities are floats, so "adds up" needs a tolerance rather than ==
SPLIT_TOLERANCE = 0.001


def _opt_float(v, default=None):
    if v is None or v == "":
        return default
    return float(v)


def split_status(line):
    """How far a line's attribute breakdown has got: totals plus whether it balances.

    The target is what ARRIVED, not what was billed. A supplier invoices 50 and
    40 come out of the boxes; the ten short are recorded as a GrnShortage and the
    breakdown then has to add up to 40. Balancing against the billed 50 would
    leave the receiver to either invent ten pieces or never post the GRN — see
    services/shortages.py.

    A line with no breakdown counts as balanced — there is nothing to add up.
    Reporting False there reads as "this line is broken" to anything consuming
    the API, when it simply isn't broken down."""
    line_qty = float(line.qty or 0)
    short = shortage_svc.line_totals(line)
    target = short["received_qty"]
    base = {"line_qty": line_qty, "received_qty": target,
            "short_qty": short["missing_qty"], "excess_qty": short["excess_qty"],
            "has_shortage": short["rows"] > 0}
    if not line.is_split:
        return {**base, "split_qty": 0.0, "remainder": 0.0, "balanced": True}
    return {**base, "split_qty": line.split_qty,
            "remainder": round(target - line.split_qty, 3),
            "balanced": abs(target - line.split_qty) <= SPLIT_TOLERANCE}


def set_line_splits(db, line, rows):
    """Replace a line's attribute breakdown ([] clears it). ValueError on bad input.

    Only a draft can be broken down — once stock has moved, changing the breakdown
    would mean rewriting posted movements. Rows need not add up to the billed
    quantity yet: the breakdown is worked on incrementally and only has to balance
    at post."""
    if line.purchase.status == "posted":
        raise ValueError("this GRN is posted — its breakdown can no longer change")

    clean, seen = [], set()
    for r in rows or []:
        attrs = {a: (str(r.get(a) or "").strip() or None) for a in SPLIT_ATTRS}
        if not any(attrs.values()):
            raise ValueError("every row needs at least one attribute "
                             "(size, colour, material, pattern, fit, type or design no)")
        key = tuple(_norm(attrs[a]) for a in SPLIT_ATTRS)
        label = " · ".join(v for v in attrs.values() if v)
        if key in seen:
            raise ValueError(f"“{label}” appears twice — merge those rows")
        seen.add(key)
        try:
            qty = _opt_float(r.get("qty"), 0) or 0
            prices = {p: _opt_float(r.get(p), line.rate if p == "rate" else None)
                      for p in SPLIT_PRICES}
        except (TypeError, ValueError):
            raise ValueError(f"“{label}”: quantity and prices must be numbers")
        if qty <= 0:
            raise ValueError(f"“{label}”: quantity must be greater than zero")
        clean.append(dict(qty=qty, code=(r.get("code") or None),
                          category=(str(r.get("category") or "").strip() or None),
                          **attrs, **prices))

    line.splits.clear()          # delete-orphan drops the previous breakup
    db.flush()
    for r in clean:
        db.add(models.PurchaseLineSplit(line_id=line.id, **r))
    if clean:
        # a split line no longer receives stock itself, so it must not look like
        # it maps to one product — the sizes do
        line.product_id = None
        line.is_new_product = False
    else:
        # breakup removed: fall back to matching the bundle line as a whole
        match = match_product(db, line.barcode, line.description, line.hsn,
                              line.purchase.supplier_id)
        line.product_id = match.id if match else None
        line.is_new_product = match is None
    db.flush()
    return line


def _receive_into_stock(db, product, qty, rate, purchase, note=None):
    """Append one inward movement and roll the product's weighted-average cost."""
    qty = float(qty or 0)
    rate = float(rate or 0)
    old_qty = float(product.stock_qty or 0)
    old_avg = float(product.avg_cost or 0)
    new_qty = old_qty + qty
    product.avg_cost = round(((old_qty * old_avg) + (qty * rate)) / new_qty, 4) if new_qty else rate
    product.stock_qty = new_qty
    product.last_rate = rate
    ref = f"GRN {purchase.grn_no or ''} / Inv {purchase.invoice_number or ''}".strip()
    db.add(models.StockMovement(
        product_id=product.id, qty_delta=qty, kind="inward",
        ref_type="purchase", ref_id=purchase.id, rate=rate,
        balance_after=product.stock_qty,
        note=f"{ref} · {note}" if note else ref,
    ))


def apply_category(db, product, name):
    """Set a product's category from the master and keep its section in step.
    Returns True when a category was applied.

    The master lists most names twice — once under their gender section and once
    under OVERALL — so the specific section is the one worth keeping: a
    LADIES-T-SHIRT belongs to LADIES, and reporting it as OVERALL would lose the
    only thing the section column is for."""
    if not name:
        return False
    product.category = name
    rows = db.query(models.Category).filter(models.Category.name == name).all()
    chosen = next((c for c in rows if c.section and c.section != "OVERALL"),
                  rows[0] if rows else None)
    product.category_section = chosen.section if chosen else None
    return True


def _create_product(db, purchase, line, split=None, mint_codes=False):
    """Create the inventory master row for a received line (or one of its variants).

    `mint_codes` assigns the SKU *and* an internal EAN-13 straight away, which is
    what variants need: the supplier never printed a code for "L / Red" alone, so
    the label has to be ours before the goods can be scanned or dispatched."""
    product = models.Product(
        # a variant carries no supplier barcode — the bundle's code covered the
        # whole bundle — so it gets one of ours below instead
        sku=None if mint_codes else _next_sku(db),
        barcode=None if split else line.barcode,
        description=line.description or "(unnamed)", hsn=line.hsn,
        uom=line.uom or "PCS", primary_supplier_id=purchase.supplier_id,
        stock_qty=0.0, avg_cost=0.0,
    )
    if split is not None:
        for a in SPLIT_ATTRS:
            setattr(product, a, getattr(split, a, None))
        product.mrp = split.mrp
        product.sale_price = split.sale_price
        product.sale_discount_pct = split.sale_discount_pct
    db.add(product)
    db.flush()
    if mint_codes:
        from . import barcode_svc
        barcode_svc.assign_identifiers(db, product)     # sku + internal EAN-13 → QR
    # A category chosen on the GRN row wins — someone looked at the goods. Only
    # when none was chosen do we classify from the invoice description, and then
    # only a confident match is applied; the rest stay blank for review.
    chosen = (getattr(split, "category", None) if split is not None else None) or line.category
    if not apply_category(db, product, chosen):
        from . import categorize
        categorize.categorise_product(db, product)
    db.flush()
    return product


def post_grn(db, purchase):
    """Commit a draft GRN to inventory. Returns a summary dict."""
    if purchase.status == "posted":
        return {"ok": False, "error": "already posted", "purchase_id": purchase.id}

    # A half-finished breakdown must never reach stock: if the rows don't add up
    # to what actually arrived, posting would quietly lose or invent units. The
    # target is the RECEIVED quantity — billed less any shortage recorded at the
    # dock — so a short delivery balances honestly instead of forcing the receiver
    # to type pieces that were never in the box.
    off = []
    for l in purchase.lines:
        st = split_status(l)
        if st["received_qty"] < -SPLIT_TOLERANCE:
            return {"ok": False, "purchase_id": purchase.id,
                    "error": f"“{(l.description or '')[:32]}”: more recorded short "
                             f"than was billed ({float(l.qty or 0):g})"}
        if l.is_split and not st["balanced"]:
            off.append(f"“{(l.description or ('line ' + str(l.id)))[:32]}”: rows total "
                       f"{l.split_qty:g} of {st['received_qty']:g} received"
                       + (f" ({float(l.qty or 0):g} billed, {st['short_qty']:g} short)"
                          if st["has_shortage"] else ""))
    if off:
        return {"ok": False, "purchase_id": purchase.id,
                "error": "breakdown doesn't add up — " + "; ".join(off)}

    from . import units as unit_svc
    created, updated, split_rows, pieces = 0, 0, 0, 0
    short_qty, short_value, nothing_arrived = 0.0, 0.0, 0
    # one identity per physical piece, under the SKU that receives it — the stock
    # figure stays at SKU level, this is the layer a garment tag hangs off
    def _serialise(product, qty):
        nonlocal pieces
        made = unit_svc.create_for_receipt(db, product, qty, purchase)
        pieces += len(made)
        return made

    for line in purchase.lines:
        st = split_status(line)
        for sh in line.shortages:
            if sh.claimable:
                short_qty += float(sh.qty or 0)
                short_value += shortage_svc.value(sh)
        # billed, and not one piece of it turned up. There is no product to create,
        # no stock to move and no carton to label — the whole of this line is the
        # claim recorded against it.
        if st["received_qty"] <= SPLIT_TOLERANCE and st["has_shortage"]:
            nothing_arrived += 1
            continue
        if line.is_split:
            for sp in line.splits:
                product = db.get(models.Product, sp.product_id) if sp.product_id else None
                if not product and sp.code:
                    from . import barcode_svc      # a scanned QR/barcode wins
                    product = barcode_svc.resolve(db, sp.code)
                if not product:
                    attrs = {a: getattr(sp, a, None) for a in SPLIT_ATTRS}
                    product = match_product(db, None, line.description, line.hsn,
                                            purchase.supplier_id, attrs=attrs)
                if not product:
                    product = _create_product(db, purchase, line, split=sp,
                                              mint_codes=True)
                    sp.is_new_product = True
                    created += 1
                else:
                    sp.is_new_product = False
                    updated += 1
                    # an existing variant can still pick up prices set on this GRN,
                    # and any attribute it was missing (e.g. matched by scanned QR)
                    for f in ("mrp", "sale_price", "sale_discount_pct"):
                        if getattr(sp, f) is not None:
                            setattr(product, f, getattr(sp, f))
                    for a in SPLIT_ATTRS:
                        if getattr(sp, a) and not getattr(product, a):
                            setattr(product, a, getattr(sp, a))
                    if not product.category:
                        apply_category(db, product, sp.category or line.category)
                sp.product_id = product.id
                if line.hsn and not product.hsn:
                    product.hsn = line.hsn
                _receive_into_stock(db, product, sp.qty, sp.effective_rate,
                                    purchase, note=sp.variant_label or None)
                _serialise(product, sp.qty)
                split_rows += 1
            continue

        product = db.get(models.Product, line.product_id) if line.product_id else None
        if not product:
            product = _create_product(db, purchase, line)      # whole line, no variants
            line.product_id = product.id
            line.is_new_product = True
            created += 1
        else:
            updated += 1
            if not product.category:
                apply_category(db, product, line.category)
        if line.hsn and not product.hsn:
            product.hsn = line.hsn
        # what arrived, not what was billed — the difference is the shortage
        recv = st["received_qty"]
        note = (f"received {recv:g} of {float(line.qty or 0):g} billed"
                if st["has_shortage"] else None)
        _receive_into_stock(db, product, recv, line.rate, purchase, note=note)
        _serialise(product, recv)

    purchase.status = "posted"
    purchase.posted_at = dt.datetime.utcnow()
    if purchase.document:
        purchase.document.status = "posted"
    db.flush()
    # the goods are now on the floor and have to go somewhere, so each carton gets
    # its handling label here — the item labels come later, at tagging
    from . import bundles as bundle_svc
    made = bundle_svc.create_for_purchase(db, purchase)
    return {"ok": True, "purchase_id": purchase.id,
            "products_created": created, "products_updated": updated,
            "lines": len(purchase.lines), "size_rows": split_rows,
            "bundles": [b.code for b in made], "pieces": pieces,
            # what the supplier billed and never delivered. Reported at post
            # because this is the moment it stops being a note on a screen and
            # becomes a claim: the stock figure is now final and does not include it.
            "short_qty": round(short_qty, 3),
            "short_value": round(short_value, 2),
            "lines_not_received": nothing_arrived}


# ---------------------------------------------------------------------------
#  Unpost — reverse a posted GRN so it can be corrected and posted again
# ---------------------------------------------------------------------------
#  A GRN can be posted, unposted and posted again, so its footprint in the ledger
#  is every row it ever wrote: the inward rows AND the reversal rows from earlier
#  unposts. All of them come out of the replay together. Excluding only the inwards
#  would leave a stale reversal behind to be counted twice — reporting a shortfall
#  that isn't there and leaving the average cost inflated by an inward whose
#  compensating row was ignored.
GRN_REF_TYPES = ("purchase", "purchase_unpost")


def _grn_movements(db, purchase):
    """Every stock row this GRN has written, across all of its posts and unposts."""
    return db.query(models.StockMovement).filter(
        models.StockMovement.ref_type.in_(GRN_REF_TYPES),
        models.StockMovement.ref_id == purchase.id).order_by(models.StockMovement.id).all()


def _replay_stock(product, exclude_ids):
    """Recompute (qty, avg_cost) from a product's ledger, ignoring `exclude_ids`.

    A weighted average can't be un-mixed arithmetically — it depends on the order
    things arrived — so subtracting one purchase back out of `avg_cost` would only
    be right when nothing else happened since. Replaying the append-only ledger
    without those rows gives the exact figures the product would carry if the GRN
    had never posted, which is what the ledger is for."""
    qty, avg = 0.0, 0.0
    for mv in sorted(product.movements, key=lambda m: m.id):
        if mv.id in exclude_ids:
            continue
        delta = float(mv.qty_delta or 0)
        if delta > 0 and mv.kind == "inward":
            new_qty = qty + delta
            rate = float(mv.rate or 0)
            avg = round(((qty * avg) + (delta * rate)) / new_qty, 4) if new_qty else rate
            qty = new_qty
        else:
            # outward / return / adjustment / earlier reversal: quantity moves,
            # the valuation of what remains does not
            qty = round(qty + delta, 3)
    return qty, avg


def unpost_blockers(db, purchase):
    """Everything that has to be cleared before this GRN can be unposted."""
    out = []
    for r in db.query(models.PurchaseReturn).filter(
            models.PurchaseReturn.purchase_id == purchase.id,
            models.PurchaseReturn.status == "posted").all():
        out.append(f"debit note {r.code} was raised against this invoice")
    for a in db.query(models.PaymentAllocation).filter(
            models.PaymentAllocation.purchase_id == purchase.id).all():
        out.append(f"payment {a.payment.receipt_no if a.payment else ''} is settled "
                   f"against this invoice".replace("  ", " "))
    # stock that has already left the warehouse cannot be un-received
    rows = _grn_movements(db, purchase)
    exclude = {m.id for m in rows}
    for pid in _movements_by_product(rows):
        product = db.get(models.Product, pid)
        if not product:
            continue
        qty, _ = _replay_stock(product, exclude)
        if qty < -SPLIT_TOLERANCE:
            out.append(f"“{product.description}” ({product.sku}) would go to {qty:g} — "
                       f"{-qty:g} of it has already been dispatched or returned")
    return out


def _movements_by_product(movements):
    out = {}
    for mv in movements:
        out.setdefault(mv.product_id, []).append(mv)
    return out


def _net_and_rate(mvs):
    """(net quantity this GRN still contributes, the cost it came in at).

    Net, because an earlier unpost may already have compensated part of it — and a
    product can legitimately receive twice from one GRN if two breakdown rows
    resolve to it. The rate is quantity-weighted across the inward rows, so a
    single compensating row values correctly whatever mix it undoes."""
    net = round(sum(float(m.qty_delta or 0) for m in mvs), 3)
    inwards = [m for m in mvs if m.kind == "inward" and float(m.qty_delta or 0) > 0]
    qty = sum(float(m.qty_delta or 0) for m in inwards)
    rate = (sum(float(m.qty_delta or 0) * float(m.rate or 0) for m in inwards) / qty
            if qty else 0.0)
    return net, round(rate, 4)


def _product_is_orphan(db, product, purchase):
    """True when a product this GRN created carries nothing else.

    Those are removed outright, so unposting really does undo the post instead of
    leaving zero-stock ghosts behind with SKUs burnt. Anything with its own history
    — phone-recorded details, another GRN, a dispatch, a return — is kept (at zero
    stock), because deleting it would destroy data this GRN never created."""
    if product.detailed or round(float(product.stock_qty or 0), 3) != 0:
        return False
    mine = ("purchase", "purchase_unpost")
    foreign_mv = db.query(models.StockMovement).filter(
        models.StockMovement.product_id == product.id,
        ~((models.StockMovement.ref_id == purchase.id)
          & (models.StockMovement.ref_type.in_(mine)))).count()
    if foreign_mv:
        return False
    other_line = db.query(models.PurchaseLine).filter(
        models.PurchaseLine.product_id == product.id,
        models.PurchaseLine.purchase_id != purchase.id).count()
    other_split = db.query(models.PurchaseLineSplit).join(
        models.PurchaseLine,
        models.PurchaseLineSplit.line_id == models.PurchaseLine.id).filter(
        models.PurchaseLineSplit.product_id == product.id,
        models.PurchaseLine.purchase_id != purchase.id).count()
    outward = db.query(models.StockOutwardLine).filter(
        models.StockOutwardLine.product_id == product.id).count()
    returned = db.query(models.PurchaseReturnLine).filter(
        models.PurchaseReturnLine.product_id == product.id).count()
    return not (other_line or other_split or outward or returned)


def unpost_grn(db, purchase):
    """Reverse a posted GRN and put it back to draft so it can be corrected.

    For products that existed before this GRN, nothing is erased: each inward row
    gets a compensating `reversal` row and the product's stock and weighted-average
    cost are replayed from the remaining ledger. Products this GRN created (and
    nothing else has touched) are removed along with their rows — for those, "as if
    never posted" is the honest outcome and leaves no zero-stock ghosts."""
    if purchase.status != "posted":
        return {"ok": False, "error": "this GRN isn't posted", "purchase_id": purchase.id}
    blockers = unpost_blockers(db, purchase)
    if blockers:
        return {"ok": False, "purchase_id": purchase.id,
                "error": "can't unpost — " + "; ".join(blockers)}

    rows = _grn_movements(db, purchase)
    exclude = {m.id for m in rows}
    qty_reversed, n_reversed = 0.0, 0
    for pid, mvs in _movements_by_product(rows).items():
        product = db.get(models.Product, pid)
        if not product:
            continue
        net, rate = _net_and_rate(mvs)
        if net == 0:                       # an earlier unpost already undid this
            continue
        final_qty, final_avg = _replay_stock(product, exclude)
        db.add(models.StockMovement(
            product_id=product.id, qty_delta=-net, kind="reversal",
            ref_type="purchase_unpost", ref_id=purchase.id, rate=rate,
            balance_after=final_qty,
            note=f"Unposted GRN {purchase.grn_no or ''} / "
                 f"Inv {purchase.invoice_number or ''}".strip()))
        qty_reversed += net
        n_reversed += 1
        product.stock_qty = final_qty
        product.avg_cost = final_avg
    db.flush()

    removed, kept = [], []
    for line in purchase.lines:
        holders = list(line.splits) if line.is_split else [line]
        for h in holders:
            product = db.get(models.Product, h.product_id) if h.product_id else None
            if product and h.is_new_product and _product_is_orphan(db, product, purchase):
                removed.append(product.sku or f"#{product.id}")
                db.delete(product)                  # cascades its movements
                h.product_id = None
                if h is line:                       # draft shows "new" again
                    line.is_new_product = True
            else:
                if product and h.is_new_product:
                    kept.append(product.sku or f"#{product.id}")
                # the product exists now, so a re-post updates rather than creates
                h.is_new_product = False

    purchase.status = "draft"
    purchase.posted_at = None
    if purchase.document and purchase.document.status == "posted":
        purchase.document.status = "confirmed"
    # the cartons describe a receipt that, as of now, did not happen — and their
    # labels point at products this unpost may have just deleted. The per-piece
    # codes go the same way: those garments were never received.
    from . import bundles as bundle_svc
    from . import units as unit_svc
    pieces_dropped = unit_svc.remove_for_purchase(db, purchase)
    dropped = bundle_svc.remove_for_purchase(db, purchase)
    db.flush()
    return {"ok": True, "purchase_id": purchase.id, "movements_reversed": n_reversed,
            "qty_reversed": round(qty_reversed, 3),
            "products_removed": removed, "products_kept": kept,
            "bundles_removed": dropped, "pieces_removed": pieces_dropped}


def adjust_stock(db, product, new_qty, note="manual adjustment"):
    """Set a product's stock to an exact figure via an adjustment movement
    (never edit stock_qty directly — the ledger stays the source of truth)."""
    delta = round(float(new_qty) - float(product.stock_qty or 0), 3)
    if delta == 0:
        return None
    product.stock_qty = float(new_qty)
    mv = models.StockMovement(
        product_id=product.id, qty_delta=delta, kind="adjustment",
        ref_type="adjustment", ref_id=None, rate=product.avg_cost or 0,
        balance_after=product.stock_qty, note=note,
    )
    db.add(mv)
    db.flush()
    return mv


# There is no update_product() any more, on purpose. A product is whatever its GRN
# made it: description / HSN / UOM off the invoice, category and prices from the
# breakdown, physical attributes from the phone app. Editing it here would let the
# master data disagree with the document and the stock ledger that produced it, so
# corrections go through unpost → fix the GRN line → post again. Stock is corrected
# by adjust_stock (a movement, never a silent overwrite) and identifiers by
# barcode_svc.assign_identifiers.


def inventory_summary(db):
    """Headline stock figures — over records that ARE stock.

    Only products traceable to a posted GRN are counted. Anything else is either
    debris or a record deliberately kept at zero after an unpost, and neither is
    stock; including them would put a valuation on goods no receipt ever brought
    in. The excluded count is reported rather than swallowed, so a number that
    quietly dropped can always be explained."""
    from . import integrity
    ctx = integrity.Context(db)
    everything = db.query(models.Product).all()
    products = [p for p in everything if ctx.product_state(p) == integrity.POSTED]
    excluded = len(everything) - len(products)
    total_value = round(sum(p.stock_value for p in products), 2)
    total_units = sum(p.stock_qty or 0 for p in products)
    return {
        "product_count": len(products),
        "total_units": total_units,
        "total_stock_value": total_value,
        "excluded_products": excluded,
    }
