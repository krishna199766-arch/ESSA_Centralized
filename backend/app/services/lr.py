"""
LR Entry extraction — read a photographed/scanned LR register (or the TRANSPORT
Excel exported to image/PDF) and return the consignment rows, so the LR Entry
grid populates itself. No manual entry: vision reads the page.

Columns match the register: recv_date, transport, bundle, lr_no, lr_date,
supplier_name, inv_no, inv_date, qty, amount, paid_topay, freight_amount,
cash_cheque, item.

Who received the consignment is deliberately NOT here — the warehouse records
that from the phone app when the packages land, not from the register page.
"""
import os
import json
from .. import runtime
from ..config import DATA_DIR

LR_SAMPLE = os.path.join(DATA_DIR, "lr_sample.json")

LR_FIELDS = ["recv_date", "transport", "bundle", "lr_no", "lr_date", "supplier_name",
             "inv_no", "inv_date", "qty", "amount", "paid_topay", "freight_amount",
             "cash_cheque", "item"]

LR_PROMPT = """You are reading an Indian garment wholesaler's LR ENTRY register
(a transport/consignment log — could be a handwritten book page or an Excel
screenshot). Return a JSON object {"rows": [ ... ]} with ONE object per
consignment row. Each row has these keys (use null if a cell is blank):
- recv_date (received date, ISO YYYY-MM-DD)
- transport (courier/transporter name e.g. GATI, GOLDEN, AKR EXPRESS, DD)
- bundle (number of bundles)
- lr_no (LR / docket number)
- lr_date (ISO)
- supplier_name
- inv_no (invoice number)
- inv_date (ISO)
- qty (total quantity)
- amount
- paid_topay (TOPAY / PAID / NO)
- freight_amount
- cash_cheque (CASH / CHEQUE / NO)
- item (product type)
Transcribe EVERY row. Numbers as numbers. Return ONLY the JSON object."""


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
