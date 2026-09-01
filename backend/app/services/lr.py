"""
LR Entry extraction — read a photographed/scanned LR register (or the TRANSPORT
Excel exported to image/PDF) and return the consignment rows, so the LR Entry
grid populates itself. No manual entry: vision reads the page.

Columns match the register: recv_date, transport, bundle, boxes, lr_no, lr_date,
supplier_name, agent, inv_no, inv_date, qty, amount, paid_topay, freight_amount,
lr_mode, item.

Only fields a register page can actually PRINT are asked for. The LR Entry form
also holds office decisions — purchase manager, stock holding period, additional
margin, auto-transfer location — which exist nowhere on the page, so asking
vision for them would only invite invention.

Nor is vision asked for columns Essa's registers never fill. Weights, package
slip, booking/receiving cities, due date, pay mode, loading charge, cash/cheque,
company, rack, section and remark were all carried for a while and every single
row came back empty, so they were removed rather than left as fields nobody
fills — an optional column that is always blank still costs a prompt line, a
form box and a review cell, and it teaches the reader that blank is normal.

Who received the consignment is deliberately NOT here — the warehouse records
that from the phone app when the packages land, not from the register page.

A register page written in Tamil comes back in English: the vision call is told
to translate as it reads, and whatever slips through is swept up afterwards. Only
the words are touched — every quantity, amount, date and LR number arrives
exactly as it was written, and the Tamil original is kept beside the row. See
services/translate.py.
"""
import os
import json
from .. import models, runtime
from ..config import DATA_DIR
from . import translate

LR_SAMPLE = os.path.join(DATA_DIR, "lr_sample.json")

# what vision returns, and therefore what an imported row may fill
LR_FIELDS = ["recv_date", "transport", "bundle", "lr_no", "lr_date", "supplier_name",
             "inv_no", "inv_date", "qty", "amount", "paid_topay", "freight_amount",
             "freight_total", "freight_charges", "item", "lr_mode", "boxes", "agent"]

LR_PROMPT = """You are reading an Indian garment wholesaler's LR ENTRY register
(a transport/consignment log — could be a handwritten book page or an Excel
screenshot), OR a single transporter's LR / consignment note ("driver copy",
"consignor copy"). A single LR copy is ONE row.
Return a JSON object {"rows": [ ... ]} with ONE object per
consignment row. Each row has these keys (use null if a cell is blank):
- recv_date (received date, ISO YYYY-MM-DD)
- transport (courier/transporter name e.g. GATI, GOLDEN, AKR EXPRESS, DD)
- lr_mode (how it travelled: Hand Delivery / Transport / Courier / Train / Air Cargo)
- bundle (number of bundles)
- boxes (number of boxes/cartons, if counted separately from bundles)
- lr_no (LR / docket number)
- lr_date (ISO)
- supplier_name
- agent (selling/commission agent name, if the register names one)
- inv_no (invoice number)
- inv_date (ISO)
- qty (total quantity / number of pieces)
- amount (goods value / "Value" of the goods — NOT a transport charge)
- paid_topay (TOPAY / PAID / NO)
- freight_amount (the FREIGHT line only)
- freight_charges — an object of the OTHER named charge lines and their amounts,
  omitting blank ones, e.g. {"L.R. Charge": 15, "H.C.": 10, "S.T. Charge": 20,
  "Insurance": 0, "A.O.C.": 0, "D.D. Charge": 0, "Others": 5}
- freight_total — the total of the charge block, printed as "G. TOTAL", "GRAND
  TOTAL", "TOTAL" or "Net Amount" beside the charges. Copy the printed figure
  exactly; do NOT recompute it. Null if the page shows no total.
- item (product type / "Said to contain")
An LR copy carries its charges as a column of named lines with a total at the
foot — read EVERY line of it. freight_amount is only the first of them, and
freight_total is what the transporter is actually owed; they are different
numbers and both are wanted. Never put the goods Value in either of them.
Most registers carry only some of these columns — return null for the rest
rather than guessing. Transcribe EVERY row. Numbers as numbers.
""" + translate.VISION_LANGUAGE_RULES + """
Return ONLY the JSON object."""


def next_entry_no(db, taken=()):
    """Our own running entry number, LRE-00001 upward.

    Derived from the highest number already issued rather than a row count, so
    deleting an entry never hands its number to a different consignment. `taken`
    carries numbers allocated earlier in the same uncommitted batch."""
    used = set(taken)
    for (v,) in db.query(models.LREntry.lr_entry_no).filter(
            models.LREntry.lr_entry_no.isnot(None)).all():
        used.add(v)
    from . import numbering
    return numbering.next_number(db, "lr", is_taken=lambda code: code in used)


def _sample_rows():
    try:
        with open(LR_SAMPLE) as f:
            return json.load(f)
    except Exception:
        return []


def _vision_available():
    if not runtime.get("anthropic_api_key"):
        return False
    try:
        import anthropic  # noqa
        return True
    except Exception:
        return False


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


#: rupees of slack when checking a charge block against its printed total —
#: transporters round, and a 50-paise gap is not a misread
CHARGE_TOLERANCE = 1.0


def settle_charges(rows):
    """Make the freight block on each row add up, or say that it doesn't.

    An LR copy prints freight as a COLUMN — Freight 425, H.C. 10, S.T. Charge 20 —
    with a G. TOTAL at the foot. Three things can come back from a photograph of
    that, and they want three different answers:

      * **total present** — keep it exactly as printed. It is what the lorry is
        paid against, so a total this code liked better would be a number no
        document supports. If the lines don't add up to it, that is worth saying
        out loud (`freight_note`), not quietly fixing.
      * **total missing, lines present** — add them up. A charge block with no
        printed total is a register column, and the sum is the honest reading.
      * **neither** — fall back to the freight figure alone, which is what this
        system recorded before it knew the rest of the block existed.

    Returns a note for the banner, or "".
    """
    mismatched = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        charges = r.get("freight_charges")
        charges = {k: _num(v) for k, v in charges.items()} if isinstance(charges, dict) else {}
        charges = {k: v for k, v in charges.items() if v}      # drop blank/zero lines
        r["freight_charges"] = charges or None
        freight = _num(r.get("freight_amount"))
        parts = (freight or 0) + sum(charges.values())
        total = _num(r.get("freight_total"))
        if total is None:
            # nothing printed: the sum of what IS printed, or just the freight
            total = round(parts, 2) if parts else freight
        elif parts and abs(parts - total) > CHARGE_TOLERANCE:
            mismatched += 1
            r["freight_note"] = (f"charges add to {parts:g}, the page totals {total:g}"
                                 f" — the printed total is kept")
        r["freight_total"] = total
        r["freight_amount"] = freight
    if mismatched:
        return (f"{mismatched} row(s): the freight lines don't add up to the printed "
                f"total — the printed total is kept, check the charges")
    return ""


def translate_rows(rows, page_language=None):
    """Put a Tamil register page into English, and keep what it actually said.

    The vision call has already been asked to translate; this is the second pass
    over whatever came back still in Tamil (see services/translate.py for why one
    pass is not enough). Every row that changed carries `original_values` —
    {field: the text on the page} — so the register can always show the original,
    and `source_language` so the grid can say a row was translated rather than
    silently presenting a reading as if the page had been in English.

    Only words move. The sweep never looks at a value without non-Latin letters,
    so quantities, amounts, dates and LR numbers are the page's own figures."""
    swept = translate.sweep(rows)
    per_row = {}
    for path, original in swept["originals"].items():
        head, _, field = path.partition("].")
        try:
            per_row.setdefault(int(head.lstrip("[")), {})[field] = original
        except ValueError:                # not a row-level path; nothing to pin it to
            continue
    doc_lang = page_language or (swept["languages"][0] if swept["languages"] else None)
    if (doc_lang or "").lower() == "english":
        doc_lang = None
    translated = 0
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        # the vision call records what it translated itself; the sweep adds what
        # it left behind. Both go in the same place, so the row carries ONE record
        # of what the page said whichever pass read it.
        orig = dict(r.get("original_values") or {})
        orig.update(per_row.get(i, {}))
        orig = {k: v for k, v in orig.items() if isinstance(v, str) and v.strip()}
        if orig:
            r["original_values"] = orig
            translated += 1
        else:
            r.pop("original_values", None)
        # a row is marked translated only when something in IT changed — "this row
        # was read in Tamil" stays a fact about the row, not about its neighbours
        r["source_language"] = doc_lang if orig else None
    # the sweep only knows about what IT translated; a page the vision call read
    # straight into English leaves it with nothing to report and the row count is
    # the honest figure
    note = swept["note"] or (f"{translated} row(s) read in {doc_lang} and translated"
                             if translated and doc_lang else "")
    return {"language": doc_lang, "translated_rows": translated,
            "translated_values": swept["translated"], "note": note}


def extract_lr(image_path):
    """Return {"rows":[...], "provider": ..., "note": ...}."""
    if _vision_available():
        try:
            import anthropic, base64
            model = runtime.get("vision_model") or "claude-3-5-sonnet-20241022"
            client = anthropic.Anthropic(api_key=runtime.get("anthropic_api_key"))
            with open(image_path, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode()
            ext = os.path.splitext(image_path)[1].lower()
            mt = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "image/jpeg")
            msg = client.messages.create(
                model=model, max_tokens=8000,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}},
                    {"type": "text", "text": LR_PROMPT}]}])
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
            rows = data.get("rows", data if isinstance(data, list) else [])
            # a Tamil page is read in Tamil and handed on in English — with the
            # original kept against every value that changed
            tr = translate_rows(rows, (data.get("source_language") or "").strip() or None)
            # freight is only the first line of a transporter's bill — reconcile
            # the whole charge block against the total printed under it
            charge_note = settle_charges(rows)
            note = f"{len(rows)} rows read by vision"
            for extra in (tr["note"], charge_note):
                if extra:
                    note += f" · {extra}"
            return {"rows": rows, "provider": "claude_vision", "note": note,
                    "language": tr["language"],
                    "translated_rows": tr["translated_rows"]}
        except Exception as e:
            return {"rows": [], "provider": "error",
                    "note": f"vision failed ({type(e).__name__}); check the image/key"}
    # no vision key: return the seeded sample so the grid is demonstrable
    rows = _sample_rows()
    return {"rows": rows, "provider": "seeded",
            "note": "No vision key set — showing the bundled sample register. "
                    "Set the vision key (top-right) to auto-read your uploaded LR page."}
