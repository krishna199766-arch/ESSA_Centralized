"""
Vision-model provider — the recommended production path.

Sends the invoice image straight to a vision-capable LLM with (a) the canonical
JSON schema it must return and (b), when the supplier is already known, that
supplier's reference example as a few-shot guide. This is what handles the messy
reality of these bills: skew, handwriting, per-supplier layouts, mixed
CGST/SGST vs IGST vs TDS. It is optional: if no ANTHROPIC_API_KEY is set the
engine simply uses another provider, so the app runs with zero external deps.
"""
import os
import json
import base64
from typing import Optional, Dict, Any
from .base import ExtractionProvider, ProviderResult, empty_invoice
from .. import runtime
from ..services import translate

# A COMPLETE schema example so the model knows every field to fill. extra="allow"
# on the Pydantic side means anything additional also survives.
FULL_SCHEMA = {
    "document_type": "purchase_invoice",
    "supplier": {
        "name": None, "legal_name": None, "gstin": None, "pan": None, "cin": None,
        "state": None, "state_code": None, "address": None, "phone": None, "email": None,
        "manufacturer": None,
        "bank": {"name": None, "account_no": None, "ifsc": None, "branch": None},
    },
    "buyer": {"name": None, "gstin": None, "pan": None, "state": None, "state_code": None, "address": None},
    "invoice": {
        "number": None, "date": None, "due_date": None, "challan_no": None,
        "order_no": None, "order_date": None, "reference_no": None,
        "irn": None, "ack_no": None, "irn_date": None, "eway_bill": None,
        "lr_no": None, "lr_date": None, "transporter": None, "destination": None,
        "delivery_note": None, "delivery_note_date": None, "tran_id": None,
        "book_city": None, "broker": None, "terms": None, "agent": None,
    },
    "line_items": [
        {"sr": 1, "barcode": None, "description": None, "brand": None, "design": None,
         "size": None, "hsn": None, "qty": None, "uom": None, "mrp": None, "rate": None,
         "discount_pct": 0, "discount_amount": 0, "taxable_value": None, "amount": None}
    ],
    "taxes": {
        "cgst_rate": 0, "cgst_amount": 0, "sgst_rate": 0, "sgst_amount": 0,
        "igst_rate": 0, "igst_amount": 0, "tds_amount": 0,
        "other_charges": 0, "freight": 0, "special_discount": 0, "round_off": 0,
    },
    "totals": {"total_qty": None, "sub_total": None, "taxable_total": None,
               "tax_total": None, "grand_total": None, "amount_in_words": None},
    "meta": {"grn_no": None, "grn_date": None, "received_by": None, "notes": None,
             "hsn_summary": []},
}
SCHEMA_HINT = json.dumps(FULL_SCHEMA, indent=2)

SYSTEM = """You are an expert accounts-payable data-entry assistant for an Indian
garment distributor. You read a supplier's purchase/tax invoice (often a phone
photo, skewed, with handwritten notes) and return ONE JSON object matching the
provided canonical schema. Capture the ENTIRE invoice — do not skip fields.

Capture ALL of the following when present anywhere on the page (header, footer,
stamps, or handwriting):
- Supplier block: name, legal/trading name, GSTIN, PAN, CIN, full address, phone,
  email, manufacturer/MFG-by, and the BANK details (bank name, account number,
  IFSC, branch).
- Invoice block: invoice number, date, due date, challan/DC number, order number
  & date, reference number, IRN + ACK number + IRN date (e-invoices), e-way bill
  number, LR number & date, transporter/courier, destination, delivery note,
  transport/EWB tran-id, broker, payment terms, agent.
- EVERY line item with: serial no, barcode, description, brand, design, size, HSN,
  qty, unit (UOM), MRP, rate, discount, taxable value, line amount.
  MRP, rate and discount are PER PIECE; taxable value and line amount are line
  totals. `discount_amount` is the per-piece discount (MRP − rate) — if the bill
  prints a discount for the whole line, divide it by the quantity. Where the bill
  states no discount at all, leave both discount fields null rather than working
  one out from the MRP.
- Taxes: CGST/SGST rates+amounts OR IGST rate+amount, TDS (even handwritten),
  other charges, freight, special discount, round off.
- Totals: total quantity, sub total, taxable total, tax total, grand total, and
  the amount-in-words text.
- Meta: handwritten GRN number & date, "received by" name, and any other notes.

Rules: Use CGST+SGST when supplier and buyer GSTINs start with the same 2 digits
(same state), otherwise IGST. Dates in ISO YYYY-MM-DD. Numbers as numbers, not
strings. Do not invent data; use null for anything genuinely absent. Return ONLY
the JSON object, no prose.
""" + translate.VISION_LANGUAGE_RULES


class ClaudeVisionProvider(ExtractionProvider):
    name = "claude_vision"

    def available(self) -> bool:
        if not runtime.get("anthropic_api_key"):
            return False
        try:
            import anthropic  # noqa
            return True
        except Exception:
            return False

    def _media_type(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".pdf": "application/pdf"}.get(ext, "image/jpeg")

    def _source_block(self, path: str, b64: str) -> Dict[str, Any]:
        """The content block for this file — a PDF is a `document`, not an `image`.

        A PDF used to be sent as an image labelled image/jpeg, which the API
        refuses, so a supplier who emails a PDF bill could not be read at all.
        (`pdf2image` is in requirements and was never called; turning the pages
        into images locally was started and not finished.) Sending the PDF
        itself is both the smaller change and the better one — the model reads
        every page in one request, and no page-rasterising binary has to exist
        on the machine, which is what lets this run somewhere without poppler.
        """
        media = self._media_type(path)
        if media == "application/pdf":
            return {"type": "document",
                    "source": {"type": "base64", "media_type": media, "data": b64}}
        return {"type": "image",
                "source": {"type": "base64", "media_type": media, "data": b64}}

    def extract(self, image_path, profile: Optional[Dict[str, Any]] = None,
                ocr_text: Optional[str] = None) -> ProviderResult:
        """`image_path` is one path, or several for one invoice printed across
        several pages — a bill of sixty lines arrives as "Tax Invoice" and "Tax
        Invoice(Page 2)", and only the last page carries the totals.

        The pages go into ONE request rather than one request each. Sent
        separately they would come back as two invoices to be stitched together
        afterwards, and the stitching is the part that needs the whole document:
        page 2 repeats the header, continues the line numbering, and is the only
        page with a grand total on it. The model can see all of that at once.
        """
        import anthropic
        model = runtime.get("vision_model") or "claude-3-5-sonnet-20241022"
        client = anthropic.Anthropic(api_key=runtime.get("anthropic_api_key"))

        paths = [image_path] if isinstance(image_path, str) else list(image_path)
        pages = []
        for p in paths:
            with open(p, "rb") as f:
                pages.append((p, base64.standard_b64encode(f.read()).decode()))

        guidance = f"Canonical schema to fill:\n{SCHEMA_HINT}"
        if len(pages) > 1:
            guidance += (
                f"\n\nThis is ONE invoice photographed as {len(pages)} pages, in order. "
                "Return ONE invoice, not one per page:\n"
                "- `line_items` is every row from every page, in the order they are "
                "printed. A page-2 row numbered 35 follows the page-1 row numbered 34.\n"
                "- The header (supplier, buyer, invoice number, date) is repeated on "
                "each page. Read it once; do not duplicate it.\n"
                "- The totals, tax lines and amount in words appear only on the last "
                "page. Take them from there, and do not invent per-page subtotals.\n"
                "- A row continued across the page break is one row, not two."
            )
        if profile and profile.get("reference_example"):
            guidance += ("\n\nThis supplier's format has been learned before. Here is a "
                         "previously CONFIRMED extraction from them — match its field "
                         "conventions, column mapping and tax structure:\n"
                         + json.dumps(profile["reference_example"], indent=2, ensure_ascii=False))

        # max_tokens caps thinking AND the answer together, and the current models
        # think before they answer — a 31-line invoice measured 3,884 thinking
        # tokens, which left ~200 of a 4,096 budget for the JSON and cut it off
        # mid-value. A cap is not a spend (only tokens actually produced are
        # billed), so it is sized for the worst invoice rather than the average
        # one: a two-page bill is ~3,500 tokens of JSON on top of the thinking.
        # Above roughly 16k the SDK's own HTTP timeout is the next thing to break,
        # which is why this streams — get_final_message() returns the same object
        # messages.create() would have.
        with client.messages.stream(
            model=model,
            max_tokens=32000,
            system=SYSTEM,
            messages=[{"role": "user", "content": [
                # Each page labelled, so "the totals are on the last page" and
                # "page 2 continues the numbering" refer to something the model
                # can actually identify rather than to an unlabelled sequence.
                *[block for i, (p, b64) in enumerate(pages, 1) for block in (
                    *([{"type": "text", "text": f"--- page {i} of {len(pages)} ---"}]
                      if len(pages) > 1 else []),
                    self._source_block(p, b64),
                )],
                {"type": "text", "text": guidance + "\n\nReturn the JSON now."},
            ]}],
        ) as stream:
            msg = stream.get_final_message()
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        data = self._parse_json(text)
        if not data:
            # Truncation and a genuinely malformed reply both arrive here as
            # unparseable text. They are not the same problem — one is fixed by
            # raising the ceiling, the other by looking at the invoice — so the
            # warning has to say which, or it sends the reader after the wrong one.
            if msg.stop_reason == "max_tokens":
                note = (f"invoice too large for one reply — the model hit the "
                        f"{32000:,}-token ceiling and the JSON was cut off")
            else:
                note = "model did not return valid JSON"
            return ProviderResult(data=empty_invoice(), provider=self.name,
                                  confidence=0.0, raw_text=text, notes=[note])
        notes = ["vision extraction"]
        # A bill written in Tamil is read in Tamil and handed on in English. The
        # prompt above asks for that; this is the sweep over whatever came back
        # untranslated anyway, and it is what makes the outcome dependable rather
        # than usual. Words only — every figure on the invoice is the supplier's
        # own, and the arithmetic in validate.py checks it as such.
        page_lang = (data.pop("source_language", None) or "").strip() or None
        swept = translate.sweep(data)
        if swept["originals"]:
            meta = data.setdefault("meta", {})
            meta.setdefault("original_values", {}).update(swept["originals"])
        lang = page_lang if (page_lang or "").lower() != "english" else None
        lang = lang or (swept["languages"][0] if swept["languages"] else None)
        if lang:
            data.setdefault("meta", {})["source_language"] = lang
            notes.append(swept["note"] or f"read in {lang} and translated to English")
        return ProviderResult(data=data, provider=self.name, confidence=0.9,
                              raw_text=text, notes=notes)

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except Exception:
            a, b = text.find("{"), text.rfind("}")
            if a >= 0 and b > a:
                try:
                    return json.loads(text[a:b + 1])
                except Exception:
                    return None
        return None
