"""Reading a photographed purchase order.

WHAT THIS IS AND IS NOT. This is an INPUT METHOD for the purchase order module,
not a second one. Nothing here writes a `PurchaseOrder`: the reading comes back
as a draft, a human corrects it on the same form they would have typed it into,
and the existing `POST /api/purchase-orders` is what saves it. That is the whole
point — an OCR pass that writes straight to the database is a system that files
its own mistakes, and the field flags below exist precisely so the person
reviewing it knows where to look first.

WHY IT DOES NOT GO THROUGH extraction/engine.py. That engine reads SUPPLIER
INVOICES: it detects the supplier by GSTIN, loads their learned profile, and
reconciles a tax block that must tie out to the printed totals. A purchase order
is our own document, has no tax block, names no GSTIN of ours worth detecting,
and has no supplier profile to learn from — it is closer to the LR register than
to an invoice. So it follows `services/lr.py`: one vision call, one prompt, one
shape back, and the same Tamil handling. Reusing the invoice engine would have
meant teaching it that half its pipeline is optional.

The arithmetic check is deliberately narrow — qty x rate against the printed
amount, and nothing else. On an invoice the identities are dense and the
validator can be confident; an order is a request, its lines are often round
numbers agreed on the phone, and inventing disagreements on a document that has
no totals to tie to would train people to ignore the flags.
"""
import base64
import json
import os

from .. import runtime
from . import dates, translate

#: What the vision call is asked for, and therefore what a read order may fill.
#: Only fields a purchase order actually PRINTS — the same rule lr.LR_PROMPT
#: keeps. Nothing here asks for our own internal numbering or status.
PO_FIELDS = ["po_no", "po_date", "supplier_name", "company", "brand", "item",
             "place", "transport", "agent", "purchaser", "discount_pct"]

PO_LINE_FIELDS = ["particulars", "size", "qty", "uom", "rate", "amount",
                  "brand", "design_no", "hsn"]

PO_PROMPT = """You are reading an Indian garment wholesaler's PURCHASE ORDER —
the document the BUYER issues to a supplier saying what they want. It may be a
printed order, a letterhead form filled in by hand, or a photograph of a page
from an order book.

Return a JSON object with these keys (use null for anything the page does not
show — do NOT invent a value):
- po_no (the order number printed on the page, if any)
- po_date (ISO YYYY-MM-DD)
- supplier_name (who the order is ADDRESSED TO — the seller)
- company (the buying firm issuing the order, usually on the letterhead)
- brand
- item (the general description of the goods ordered)
- place (delivery place / destination, if named)
- transport (transporter or delivery mode, if named)
- agent (selling / commission agent, if named)
- purchaser (the buyer or purchase manager who raised it)
- discount_pct (a percentage discount stated for the whole order, as a number)
- lines: an array, ONE OBJECT PER ROW of the order's item table, each with:
    - particulars (the description column, verbatim)
    - size (the size or size mix exactly as written, e.g. "30:2, 32:4, 34:4")
    - qty (a number)
    - uom (PCS, DOZ, MTR, SET …)
    - rate (price per unit, a number)
    - amount (the row total as PRINTED — do not compute it if it is not there)
    - brand, design_no, hsn (if the table has those columns)

IMPORTANT:
- The supplier is who the order is sent TO. The company is who sent it. Getting
  these the wrong way round is the single most common error on this document.
- Transcribe EVERY row of the item table.
- Numbers as numbers, with no currency symbols, commas or units.
- An order carries no tax block and usually no grand total. Do not invent one.
""" + translate.VISION_LANGUAGE_RULES + """
Return ONLY the JSON object."""

#: Rupees of slack when checking qty x rate against a printed amount. Orders are
#: written in round figures and a supplier's own rounding is not a misread.
AMOUNT_TOLERANCE = 1.0


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def available():
    """Whether a photographed order can be read at all.

    Asked by the screen BEFORE it offers the button, so the answer is "vision is
    not configured" on a settings page rather than a failure after somebody has
    photographed a document.
    """
    if not runtime.get("anthropic_api_key"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def validate(draft: dict) -> dict:
    """Check what came back and say where to look. Never changes a value.

    Returns `warnings` (sentences for the banner), `field_flags` (paths the form
    highlights) and a `confidence` score. Flagging a field is a request for a
    human's attention, not a claim that it is wrong — which is why nothing here
    corrects anything, and why a flagged field still carries the read value.
    """
    warnings, flags = [], {}

    if not (draft.get("supplier_name") or "").strip():
        flags["supplier_name"] = True
        warnings.append("No supplier could be read — an order cannot be confirmed without one")
    if not dates.to_iso(draft.get("po_date")):
        flags["po_date"] = True
        warnings.append("The order date could not be read as a date")

    lines = draft.get("lines") or []
    if not lines:
        warnings.append("No item rows were found on the page")
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        qty, rate, amount = _num(line.get("qty")), _num(line.get("rate")), _num(line.get("amount"))
        if not (line.get("particulars") or "").strip():
            flags[f"lines.{i}.particulars"] = True
        if qty is None:
            flags[f"lines.{i}.qty"] = True
        # Only when all three are present is there an identity to check. A line
        # with no printed amount is not a disagreement — `line_amount` will
        # derive it on save, which is the documented behaviour.
        if qty is not None and rate is not None and amount is not None:
            if abs(qty * rate - amount) > AMOUNT_TOLERANCE:
                flags[f"lines.{i}.amount"] = True
                warnings.append(
                    f"Row {i + 1}: {qty:g} x {rate:g} is {qty * rate:g}, "
                    f"but the page says {amount:g}")

    # Starts at 1.0 and is penalised per flag and per warning, the same shape
    # extraction/validate.py uses — so a confidence figure means the same thing
    # on this screen as it does on the invoice review beside it.
    score = 1.0 - 0.08 * len(flags) - 0.05 * len(warnings)
    return {"warnings": warnings, "field_flags": flags,
            "confidence": round(max(0.0, min(1.0, score)), 2)}


def _clean(draft: dict) -> dict:
    """Keep only the fields this module asked for, normalised.

    A vision call can return a key nobody requested; letting it through would
    put an unknown field on the form and, worse, into the save payload.
    """
    out = {k: draft.get(k) for k in PO_FIELDS}
    out["po_date"] = dates.normalise(out.get("po_date")) if out.get("po_date") else None
    out["discount_pct"] = _num(out.get("discount_pct"))
    lines = []
    for raw in (draft.get("lines") or []):
        if not isinstance(raw, dict):
            continue
        line = {k: raw.get(k) for k in PO_LINE_FIELDS}
        for k in ("qty", "rate", "amount"):
            line[k] = _num(line[k])
        if any(v not in (None, "") for v in line.values()):
            lines.append(line)
    out["lines"] = lines
    return out


def extract_po(image_path: str) -> dict:
    """Read one photographed order. Returns {draft, provider, note, ...}.

    Never raises on a bad read: a failure comes back as an empty draft and a note
    saying what went wrong, because the screen's answer to "vision could not read
    this" is to fall through to manual entry with the page still on screen — not
    an error dialog over a form somebody now has to reopen.
    """
    if not available():
        return {"draft": _clean({}), "provider": "unavailable", "language": None,
                "note": "No vision key set — key the order in by hand, or set the "
                        "key in Settings to read it from the photograph.",
                **validate(_clean({}))}
    try:
        import anthropic
        model = runtime.get("vision_model") or "claude-3-5-sonnet-20241022"
        client = anthropic.Anthropic(api_key=runtime.get("anthropic_api_key"))
        with open(image_path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower()
        mt = {".png": "image/png", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/jpeg")
        msg = client.messages.create(
            model=model, max_tokens=8000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}},
                {"type": "text", "text": PO_PROMPT}]}])
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", "") == "text").strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])

        # A Tamil order is read in Tamil and handed on in English, with the page's
        # own words kept beside it — the same sweep the LR register gets. Only
        # words move; every quantity, rate and date is the page's own figure.
        swept = translate.sweep(data)
        lang = (data.get("source_language") or "").strip() or None
        if (lang or "").lower() == "english":
            lang = None
        if not lang and swept["languages"]:
            lang = swept["languages"][0]

        draft = _clean(data)
        note = f"{len(draft['lines'])} row(s) read by vision"
        if swept["note"]:
            note += f" · {swept['note']}"
        return {"draft": draft, "provider": "claude_vision", "note": note,
                "language": lang, "original_values": swept["originals"],
                **validate(draft)}
    except Exception as e:                       # noqa: BLE001 — reported, not raised
        draft = _clean({})
        return {"draft": draft, "provider": "error", "language": None,
                "note": f"The page could not be read ({type(e).__name__}). "
                        f"Key the order in by hand, or try a clearer photograph.",
                **validate(draft)}
