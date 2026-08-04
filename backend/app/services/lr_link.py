"""
LR ↔ Invoice linking:

1. Duplicate detection on LR upload — before saving extracted LR rows we
   classify each one against the register (and earlier rows in the same page)
   into three tiers:
     * "duplicate" — same key AND every field is identical → skipped by default
     * "doubtful"  — same key (LR No + Invoice No) but one or more OTHER fields
                     differ → kept in the grid and flagged for the data-entry
                     person to verify & approve (it may be a correction, a
                     second consignment on the same LR/invoice, or a typo)
     * "new"       — key not seen before

   Duplicate key (default): normalised (LR No + Invoice No). If a row has no
   LR number we fall back to (Supplier + Invoice No).

2. Cross-fill from invoice — when an invoice is uploaded/confirmed we look for
   the LR row it belongs to (match on Invoice No + supplier) and fill that
   row's BLANK fields from the invoice (inv_date, qty, amount, supplier_name,
   item), then link the two. Fields the LR already holds are never overwritten;
   invoice values that disagree with the register are flagged, not applied.

3. Cross-fill INTO the invoice — the mirror of (2). The register is the
   authoritative record of how a consignment travelled, so once an invoice is
   matched we back-fill its blank consignment fields (LR no & date, transporter,
   booking city) from the register. This is what makes the invoice form's
   "E-invoice & Transport" block populate itself even for the many supplier
   formats that never print the docket details.

   Linking is SYMMETRIC: whichever direction establishes the pairing, the register
   row also takes the invoice's number and date (see `link_row_to_invoice`). A row
   that says "linked" with an empty Inv No is a broken half-link, so every path
   that sets `matched` fills through the same helper.
"""
from .. import models

# Every column we compare when deciding "exactly the same" vs "doubtful" — the
# columns that come off the register PAGE, and only those.
#
# The office-decision columns (purchase_manager, stock_holding_days,
# additional_margin, auto_transfer_location) are deliberately absent. They are
# never printed on a register, so a re-import always has them blank; comparing
# them would make every row where someone had since filled one in come back
# "doubtful" against its own twin, and the point of the check is to spot a page
# transcribed differently, not office edits.
LR_ALL_FIELDS = ["recv_date", "transport", "bundle", "lr_no", "lr_date",
                 "supplier_name", "inv_no", "inv_date", "qty", "amount",
                 "paid_topay", "freight_amount", "item",
                 "lr_mode", "boxes", "agent"]
_LR_NUMERIC = {"bundle", "qty", "amount", "freight_amount", "boxes"}


def _norm(v):
    if v is None:
        return ""
    return "".join(str(v).split()).upper().strip()


def _get(row, k):
    return row.get(k) if isinstance(row, dict) else getattr(row, k, None)


def dup_key(row):
    """Return a stable fingerprint for a row (dict or LREntry)."""
    lr = _norm(_get(row, "lr_no"))
    inv = _norm(_get(row, "inv_no"))
    if lr and inv:
        return ("lr+inv", lr, inv)
    if inv:                      # no LR number printed — fall back to supplier+invoice
        return ("sup+inv", _norm(_get(row, "supplier_name")), inv)
    if lr:
        return ("lr", lr)
    return None                  # nothing to key on → never treated as a duplicate


def _field_equal(field, a, b):
    a_blank = a in (None, "")
    b_blank = b in (None, "")
    if a_blank and b_blank:
        return True
    if a_blank != b_blank:
        return False
    if field in _LR_NUMERIC:
        try:
            return abs(float(a) - float(b)) <= 0.5
        except (TypeError, ValueError):
            return _norm(a) == _norm(b)
    return _norm(a) == _norm(b)


def _diff_fields(a_row, b_row):
    """List of columns whose values differ between two rows ([] = identical)."""
    return [f for f in LR_ALL_FIELDS
            if not _field_equal(f, _get(a_row, f), _get(b_row, f))]


def _index_existing(db):
    idx = {}
    for e in db.query(models.LREntry).all():
        k = dup_key(e)
        if k:
            idx.setdefault(k, []).append(e)
    return idx


def _classify(row, candidates):
    """Given a row and the existing rows sharing its key, return
    (status, diff_fields, conflict_values). status ∈ new|duplicate|doubtful."""
    if not candidates:
        return "new", [], {}
    best_diffs = None
    best = None
    for cnd in candidates:
        diffs = _diff_fields(row, cnd)
        if not diffs:
            return "duplicate", [], {}          # an exact twin exists → hard duplicate
        if best_diffs is None or len(diffs) < len(best_diffs):
            best_diffs, best = diffs, cnd
    conflict = {f: _get(best, f) for f in best_diffs}
    return "doubtful", best_diffs, conflict


def annotate_duplicates(db, rows):
    """Tag each row with `_status` (new|duplicate|doubtful), `_diffs` (columns
    that differ from the matched record) and `_conflict_with` (that record's
    values for those columns). Returns (rows, summary)."""
    existing = _index_existing(db)
    batch = {}
    n_dup = n_doubt = 0
    for r in rows:
        k = dup_key(r)
        r["_status"], r["_diffs"], r["_conflict_with"] = "new", [], {}
        if k is None:
            continue
        candidates = existing.get(k, []) + batch.get(k, [])
        status, diffs, conflict = _classify(r, candidates)
        r["_status"], r["_diffs"], r["_conflict_with"] = status, diffs, conflict
        if status == "duplicate":
            n_dup += 1
        elif status == "doubtful":
            n_doubt += 1
        batch.setdefault(k, []).append(r)
    summary = {
        "total": len(rows),
        "duplicates": n_dup,
        "doubtful": n_doubt,
        "new": len(rows) - n_dup - n_doubt,
    }
    return rows, summary


def is_exact_duplicate(row, candidates):
    """Server-side guard used on save: True only when an identical row exists."""
    return _classify(row, candidates)[0] == "duplicate"


# ---- cross-fill --------------------------------------------------------------
#
# Matching rule (as specified): find the LR row for an uploaded invoice by
#   * LR No  — if the invoice carries one (some tax invoices print the transport
#     docket no), OR
#   * Invoice No + supplier — the reliable bridge when the invoice has no LR no.
# Then REQUIRE the quantity to match as a confirmation gate before populating.
# When the gate passes, fill the LR's BLANK fields from the invoice:
#   invoice number, invoice date, amount, freight (+ qty, item).
# Values the register already holds are never overwritten; disagreements are
# flagged. If the LR No/Invoice No match but the quantity does NOT, we link the
# row and flag a quantity conflict WITHOUT filling (needs verification).

def _parse_invoice(data):
    """Pull every LR-fillable value out of a canonical invoice dict. Freight and
    lr_no are looked up defensively — present only if the invoice format carries
    them."""
    inv = data.get("invoice") or {}
    sup = data.get("supplier") or {}
    tot = data.get("totals") or {}
    items = data.get("line_items") or []
    qty = tot.get("total_qty")
    if qty in (None, "", 0) and items:
        try:
            qty = sum(float(it.get("qty") or 0) for it in items) or None
        except (TypeError, ValueError):
            qty = None
    return {
        "inv_no": inv.get("number"),
        "inv_date": inv.get("date"),
        "amount": tot.get("grand_total"),
        "qty": qty,
        "item": items[0].get("description") if items else None,
        "supplier_name": sup.get("name"),
        "transport": inv.get("transporter") or inv.get("transport"),
        # present only if the invoice actually prints these:
        "freight_amount": inv.get("freight") or inv.get("freight_amount") or tot.get("freight"),
        "lr_no": inv.get("lr_no") or inv.get("lr_number") or inv.get("lr"),
    }


# fields the invoice may write into the LR when the LR cell is blank
_FILL_FIELDS = ["inv_no", "inv_date", "amount", "freight_amount",
                "qty", "item", "supplier_name", "transport"]
# fields where an already-present register value can be compared to the invoice
_COMPARABLE = {"inv_date", "amount", "freight_amount", "item"}
_NUMERIC = {"amount", "qty", "freight_amount", "bundle"}


def _values_differ(field, register_val, invoice_val):
    """True if the register and invoice disagree enough to flag."""
    if register_val in (None, "", 0) or invoice_val in (None, "", 0):
        return False
    if field in _NUMERIC:
        try:
            return abs(float(register_val) - float(invoice_val)) > 0.5
        except (TypeError, ValueError):
            return _norm(register_val) != _norm(invoice_val)
    a, b = _norm(register_val), _norm(invoice_val)
    if field in ("item", "transport"):
        # free-text names: "GATI" vs "GATI EXPRESS" is agreement, not a conflict
        return not (a in b or b in a)
    return a != b


def _supplier_matches(lr_row, data):
    """Loose supplier agreement between an LR row and an invoice."""
    sup = (data.get("supplier") or {}).get("name") or ""
    lr_name = lr_row.supplier_name or ""
    if not lr_name or not sup:
        return True                      # no supplier on one side → don't block the match
    a, b = _norm(lr_name), _norm(sup)
    return a in b or b in a


def _qty_gate(lr_qty, inv_qty):
    """Confirmation gate: 'ok' | 'mismatch' | 'unconfirmed' (one side blank)."""
    if lr_qty in (None, "", 0) or inv_qty in (None, "", 0):
        return "unconfirmed"
    try:
        return "ok" if abs(float(lr_qty) - float(inv_qty)) <= 0.5 else "mismatch"
    except (TypeError, ValueError):
        return "unconfirmed"


def _blank(v):
    return v in (None, "", 0)


def _match_lr_rows(db, data):
    """Register rows belonging to an invoice, as [(LREntry, matched_by), ...].

    The single matching rule both cross-fill directions use: the printed LR
    number when the invoice carries one, otherwise Invoice No + a loose supplier
    agreement. Empty when the invoice has neither key.

    An LR number this module itself back-filled is NOT a valid key — it came from
    the register, so keying on it would let one invoice reach every row sharing
    that docket (a split consignment covering other invoices) and write its data
    into them."""
    p = _parse_invoice(data)
    inv_no, lr_no = _norm(p["inv_no"]), _norm(p["lr_no"])
    if "lr_no" in (((data.get("meta") or {}).get("lr_source") or {}).get("filled") or {}):
        lr_no = ""
    if not inv_no and not lr_no:
        return []
    out = []
    for lr in db.query(models.LREntry).all():
        if lr_no and _norm(lr.lr_no) == lr_no:
            out.append((lr, "lr_no"))
        elif inv_no and _norm(lr.inv_no) == inv_no and _supplier_matches(lr, data):
            out.append((lr, "inv_no"))
    return out


def _fill_row_from_invoice(lr, p):
    """Fill ONE register row's blank cells from a parsed invoice.

    Returns (filled, mismatches). A value the register already holds is never
    overwritten — the register is the record of how the consignment travelled, so
    a disagreement is flagged rather than applied."""
    filled, mismatches = [], []
    for field in _FILL_FIELDS:
        val = p.get(field)
        if _blank(val):
            continue
        current = getattr(lr, field, None)
        if _blank(current):                       # blank → fill from invoice
            setattr(lr, field, val)
            filled.append(field)
        elif field in _COMPARABLE and _values_differ(field, current, val):
            mismatches.append({"field": field, "register": current, "invoice": val})
    return filled, mismatches


def link_row_to_invoice(lr, p, invoice_doc_id, qty="unconfirmed"):
    """Record the pairing on the register row AND fill its blanks from the invoice.

    Every path that links a row ends up here, so a row that says "linked" always
    carries the invoice's number and date — whichever direction the link came
    from. A quantity disagreement is kept visible on the row instead of blocking
    the fill: by the time this is called the pairing is either key-confirmed or a
    human's explicit choice."""
    if invoice_doc_id is not None:
        lr.invoice_document_id = invoice_doc_id
    lr.matched = True
    filled, mismatches = _fill_row_from_invoice(lr, p)
    if qty == "mismatch":
        mismatches = [{"field": "qty", "register": lr.qty, "invoice": p["qty"]}] + mismatches
    lr.mismatches = mismatches
    return filled


def backfill_linked_rows(db):
    """Heal rows that were linked before the fill became symmetric.

    A row could be marked linked by the register→invoice direction, which used to
    write only into the invoice — so the register showed "✓ linked" with Inv No and
    Inv Date still empty. Runs at startup; a no-op once there is nothing to fill.

    Rows carrying a quantity conflict are skipped: the gate blocked their fill on
    purpose and a human still has to settle them."""
    healed = 0
    rows = db.query(models.LREntry).filter(
        models.LREntry.matched == True,                       # noqa: E712
        models.LREntry.invoice_document_id.isnot(None)).all()
    for lr in rows:
        if not any(_blank(getattr(lr, f, None)) for f in _FILL_FIELDS):
            continue
        if any(m.get("field") == "qty" for m in (lr.mismatches or [])):
            continue
        doc = db.get(models.Document, lr.invoice_document_id)
        ex = doc.latest_extraction if doc else None
        if not ex or not ex.data:
            continue
        filled, mismatches = _fill_row_from_invoice(lr, _parse_invoice(ex.data))
        if not filled:
            continue
        known = {m.get("field") for m in (lr.mismatches or [])}
        lr.mismatches = (lr.mismatches or []) + [m for m in mismatches
                                                 if m["field"] not in known]
        healed += 1
    if healed:
        db.commit()
    return healed


def fill_lr_from_invoice(db, data, invoice_doc_id):
    """Match an uploaded invoice to its LR row (LR No, else Invoice No+supplier),
    confirm on quantity, then fill the LR's blank fields. Returns a list of
    {id, filled, mismatches, matched_by, qty} describing each linked row."""
    p = _parse_invoice(data)
    results = []
    for lr, matched_by in _match_lr_rows(db, data):
        qty = _qty_gate(lr.qty, p["qty"])
        lr.invoice_document_id = invoice_doc_id
        lr.matched = True

        if qty == "mismatch":
            # matched on the key but quantities disagree → link + flag, DON'T fill.
            # An automatic key match with the wrong quantity is the one case a human
            # must see before any invoice value lands in the register.
            lr.mismatches = [{"field": "qty", "register": lr.qty, "invoice": p["qty"]}]
            results.append({"id": lr.id, "filled": [], "mismatches": lr.mismatches,
                            "matched_by": matched_by, "qty": "mismatch"})
            continue

        filled, mismatches = _fill_row_from_invoice(lr, p)
        lr.mismatches = mismatches
        results.append({"id": lr.id, "filled": filled, "mismatches": mismatches,
                        "matched_by": matched_by, "qty": qty})
    if results:
        db.commit()
    return results


# ---- reverse cross-fill: register → invoice's transport block -----------------
#
# The invoice form's "E-invoice & Transport" section holds two kinds of field:
#   * e-invoice fields (IRN, ACK no, IRN date, e-way bill, EWB tran id, delivery
#     note) — printed on the invoice and nowhere else, so extraction is their
#     only source; the register cannot supply them.
#   * consignment fields (below) — the LR register IS the source of record for
#     these, and it usually beats the invoice, which often doesn't print them.
# Only BLANK invoice fields are written, so anything read off the page wins.

INVOICE_FROM_LR = {            # invoice.<key>  <-  LREntry.<column>
    "lr_no": "lr_no",
    "lr_date": "lr_date",
    "transporter": "transport",
    # The invoice's Book City used to be filled from the register's `place`
    # column. That column was dropped from the LR entry, so Book City now comes
    # only from the invoice page itself.
}


def _register_qty(matches):
    """Quantity the register accounts for. Several rows matching one invoice is a
    split consignment, so they sum."""
    total = 0.0
    for lr, _ in matches:
        try:
            total += float(lr.qty or 0)
        except (TypeError, ValueError):
            return None
    return total or None


def fill_invoice_from_lr(db, data, invoice_doc_id=None):
    """Back-fill an invoice's consignment fields from its LR register row(s).

    Mutates `data["invoice"]` in place and returns a report, or {} when nothing
    in the register matches. Rules:
      * only blank invoice fields are written — extraction always wins;
      * a quantity disagreement between invoice and register blocks the fill
        (same confirmation gate as the forward direction) — that pairing needs a
        human, not a guess;
      * if several matched rows carry different values for a field, none is
        applied and the disagreement is reported.

    When no invoice/LR number matches, one last strict tier applies: a single
    register row whose supplier AND quantity both agree, with no rival that could
    equally be it (see `unambiguous_supplier_match`). Anything less certain than
    that returns {} and becomes a suggestion for a human to pick from.
    """
    matches = _match_lr_rows(db, data)
    if not matches:
        sole = unambiguous_supplier_match(db, data)
        if sole is None:
            return {}
        matches = [(sole, "supplier_qty")]

    p = _parse_invoice(data)
    qty = _qty_gate(_register_qty(matches), p["qty"])
    report = {"matched_lr_ids": [lr.id for lr, _ in matches],
              "matched_by": matches[0][1], "qty": qty,
              "filled": {}, "conflicts": [], "differs": []}
    if qty == "mismatch":
        report["conflicts"].append({"field": "qty", "invoice": p["qty"],
                                    "register": _register_qty(matches)})
        return report

    _write_invoice_fields(data, matches, report)
    # Linking is symmetric: the register row gets the invoice's number and date
    # too. Without this a row matched on LR No alone (or on supplier+quantity,
    # which the forward pass never sees because it matches on keys only) shows
    # "linked" with Inv No / Inv Date still empty.
    if invoice_doc_id:
        report["register_filled"] = {
            lr.id: link_row_to_invoice(lr, p, invoice_doc_id, qty) for lr, _ in matches}
        db.commit()
    return report


def _write_invoice_fields(data, matches, report):
    """Write the values the matched rows agree on into the invoice's blank
    consignment fields, recording what was filled / kept / disputed."""
    inv = data.setdefault("invoice", {})
    for inv_field, lr_field in INVOICE_FROM_LR.items():
        values = []
        for lr, _ in matches:
            v = getattr(lr, lr_field, None)
            if not _blank(v) and all(_norm(v) != _norm(x) for x in values):
                values.append(v)
        if not values:
            continue
        if len(values) > 1:                    # the matched rows disagree
            report["conflicts"].append({"field": inv_field, "register": values})
            continue
        current = inv.get(inv_field)
        if _blank(current):
            inv[inv_field] = values[0]
            report["filled"][inv_field] = values[0]
        elif _values_differ(lr_field, values[0], current):
            report["differs"].append({"field": inv_field, "invoice": current,
                                      "register": values[0]})


def fill_invoice_from_lr_entry(db, data, lr_entry_id, invoice_doc_id=None):
    """Fill from one register row a human explicitly picked. None if no such row.

    Their pick overrides the automatic quantity gate — the picker exists precisely
    for the case where the keys don't line up — but a quantity disagreement is
    still reported so it stays visible on the form instead of being buried. The
    pairing is recorded on the register row and the invoice's number/date flow
    back into it, so both sides agree after one click."""
    lr = db.get(models.LREntry, lr_entry_id)
    if lr is None:
        return None
    p = _parse_invoice(data)
    qty = _qty_gate(lr.qty, p["qty"])
    report = {"matched_lr_ids": [lr.id], "matched_by": "manual", "qty": qty,
              "filled": {}, "conflicts": [], "differs": []}
    if qty == "mismatch":
        report["differs"].append({"field": "qty", "invoice": p["qty"],
                                  "register": lr.qty})
    _write_invoice_fields(data, [(lr, "manual")], report)
    report["register_filled"] = {lr.id: link_row_to_invoice(lr, p, invoice_doc_id, qty)}
    db.commit()
    return report


# ---- candidate suggestions when no key matches -------------------------------
#
# A supplier ships repeatedly, so a supplier-name agreement alone can never be
# enough to auto-fill — it would happily attach the wrong consignment. It IS
# enough to offer, though: the register row is usually right there under a
# different invoice number, and a human can tell in one glance. So a failed match
# returns suggestions and lets the data-entry person pick.

CANDIDATE_MIN_SCORE = 85      # supplier-name agreement below this isn't worth offering


def _supplier_score(lr_name, inv_name):
    """0..100 agreement between a register supplier and an invoice's supplier.

    A containment test handles the common shortening ("STRAWBERRY" in the register
    for "STRAWBERRY APPAREL" on the invoice). Past that we only want to absorb OCR
    slips and spelling drift, so this scores whole names — NOT partial_ratio, which
    rates any short name against a long one on its best-matching window alone and
    happily calls "KRISHA" a 91% match for "KRISHNA LEELA CREATION"."""
    a, b = _norm(lr_name), _norm(inv_name)
    if not a or not b:
        return 0
    if a in b or b in a:
        return 100
    from rapidfuzz import fuzz
    return int(fuzz.ratio(a, b))


def _candidate(lr, data, p, score):
    """One offerable register row, with everything the picker needs to show."""
    inv = data.get("invoice") or {}
    return {
        "id": lr.id, "score": score, "qty_agrees": _qty_gate(lr.qty, p["qty"]),
        "lr_no": lr.lr_no, "lr_date": lr.lr_date, "transport": lr.transport,
        "inv_no": lr.inv_no, "inv_date": lr.inv_date,
        "qty": lr.qty, "supplier_name": lr.supplier_name, "recv_date": lr.recv_date,
        "item": lr.item, "already_linked": bool(lr.matched),
        "would_fill": [f for f, col in INVOICE_FROM_LR.items()
                       if not _blank(getattr(lr, col, None)) and _blank(inv.get(f))],
    }


_QTY_ORDER = {"ok": 0, "unconfirmed": 1, "mismatch": 2}


def suggest_lr_candidates(db, data, limit=6, include=None):
    """Register rows that plausibly belong to this invoice, best first.

    Ranked by supplier-name agreement, then by whether the quantity agrees, then
    most-recent-first. `include` force-lists specific rows (used when an exact
    match was found but the quantity gate blocked it — that row is still the most
    likely answer, and a human may want to link it anyway)."""
    p = _parse_invoice(data)
    forced = list(include or [])
    forced_ids = {lr.id for lr in forced}
    out = [_candidate(lr, data, p, _supplier_score(lr.supplier_name, p["supplier_name"]))
           for lr in forced]
    if p["supplier_name"]:
        for lr in db.query(models.LREntry).all():
            if lr.id in forced_ids:
                continue
            score = _supplier_score(lr.supplier_name, p["supplier_name"])
            if score >= CANDIDATE_MIN_SCORE:
                out.append(_candidate(lr, data, p, score))
    head, tail = out[:len(forced)], out[len(forced):]
    tail.sort(key=lambda c: (-c["score"], _QTY_ORDER.get(c["qty_agrees"], 3), -c["id"]))
    return (head + tail)[:limit]


def unambiguous_supplier_match(db, data):
    """The one register row it is safe to fill from when no invoice/LR number
    matches — or None, which sends the decision to the human picker.

    Deliberately strict, because a supplier ships repeatedly and the supplier name
    alone proves nothing. All three must hold:
      * the supplier names agree fully (containment, not a fuzzy near-miss) —
        spelling drift stays a suggestion;
      * the quantities agree, which is what actually singles out the consignment;
      * nothing else could be it. A rival row with the same supplier and no
        quantity recorded cannot be ruled out, so its mere existence is enough to
        stop the automatic fill.
    """
    cands = suggest_lr_candidates(db, data, limit=200)
    confirmed = [c for c in cands if c["score"] == 100 and c["qty_agrees"] == "ok"]
    if len(confirmed) != 1:
        return None
    sole = confirmed[0]
    if any(c["id"] != sole["id"] and c["qty_agrees"] == "unconfirmed" for c in cands):
        return None
    return db.get(models.LREntry, sole["id"])


def describe_invoice_fill(report):
    """Human-readable lines for the review panel's checks box ([] when clean)."""
    if not report:
        return []
    labels = {"lr_no": "LR No", "lr_date": "LR Date", "transporter": "Transporter",
              "qty": "quantity"}
    out = []
    # the automatic gate blocking a fill is the one case with nothing else to say
    if report.get("qty") == "mismatch" and report.get("matched_by") != "manual":
        c = next((x for x in report.get("conflicts", []) if x["field"] == "qty"), {})
        out.append(f"LR register row matched but quantity disagrees "
                   f"(invoice {c.get('invoice')} vs register {c.get('register')}) — "
                   f"transport fields not filled")
        return out
    if report.get("matched_by") == "manual":
        out.append("LR register row linked by hand (invoice no / LR no did not match)")
    if report.get("filled"):
        how = {"lr_no": "matched on LR No", "inv_no": "matched on Invoice No",
               "manual": "row chosen by hand",
               "supplier_qty": "matched on supplier + quantity — the invoice and "
                               "register share no invoice/LR number, so check this "
                               "is the right consignment",
               }.get(report.get("matched_by"), "matched")
        names = ", ".join(labels.get(k, k) for k in report["filled"])
        out.append(f"Filled {names} from the LR register ({how})")
    for d in report.get("differs", []):
        out.append(f"{labels.get(d['field'], d['field'])}: invoice says "
                   f"{d['invoice']!r}, LR register says {d['register']!r} — kept the invoice value")
    for c in report.get("conflicts", []):
        if c["field"] == "qty":
            continue
        out.append(f"{labels.get(c['field'], c['field'])}: matched LR rows disagree "
                   f"({', '.join(str(v) for v in c['register'])}) — left blank")
    return out
