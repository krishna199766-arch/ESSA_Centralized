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
"""
import os
import json
from .. import models, runtime
from ..config import DATA_DIR

LR_SAMPLE = os.path.join(DATA_DIR, "lr_sample.json")

# what vision returns, and therefore what an imported row may fill
LR_FIELDS = ["recv_date", "transport", "bundle", "lr_no", "lr_date", "supplier_name",
             "inv_no", "inv_date", "qty", "amount", "paid_topay", "freight_amount",
             "item", "lr_mode", "boxes", "agent"]

LR_PROMPT = """You are reading an Indian garment wholesaler's LR ENTRY register
(a transport/consignment log — could be a handwritten book page or an Excel
screenshot). Return a JSON object {"rows": [ ... ]} with ONE object per
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
- amount (goods value)
- paid_topay (TOPAY / PAID / NO)
- freight_amount
- item (product type)
Most registers carry only some of these columns — return null for the rest
rather than guessing. Transcribe EVERY row. Numbers as numbers.
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
    high = 0
    for v in used:
        s = str(v or "")
        if s.startswith("LRE-") and s[4:].isdigit():
            high = max(high, int(s[4:]))
    return f"LRE-{high + 1:05d}"


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
            return {"rows": rows, "provider": "claude_vision", "note": f"{len(rows)} rows read by vision"}
        except Exception as e:
            return {"rows": [], "provider": "error",
                    "note": f"vision failed ({type(e).__name__}); check the image/key"}
    # no vision key: return the seeded sample so the grid is demonstrable
    rows = _sample_rows()
    return {"rows": rows, "provider": "seeded",
            "note": "No vision key set — showing the bundled sample register. "
                    "Set the vision key (top-right) to auto-read your uploaded LR page."}
