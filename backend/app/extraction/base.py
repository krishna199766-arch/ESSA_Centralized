"""Provider interface + the canonical field list the whole system agrees on.

Every extraction provider (seeded fixtures, Tesseract OCR, a vision model, or a
future one) implements the same `extract()` contract and returns the same
canonical dict shape. That is what makes providers swappable without touching
the API, DB, or UI: the engine, validator and frontend only ever see canonical
invoices."""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class ProviderResult:
    data: Dict[str, Any]                       # canonical invoice dict
    provider: str
    confidence: float = 0.0                    # provider self-reported 0..1
    raw_text: str = ""
    notes: List[str] = field(default_factory=list)


def empty_invoice() -> Dict[str, Any]:
    return {
        "document_type": "purchase_invoice",
        "template_key": None,
        "supplier": {"name": None, "legal_name": None, "gstin": None, "pan": None,
                     "cin": None, "state": None, "state_code": None, "address": None,
                     "phone": None, "email": None, "manufacturer": None,
                     "bank": {"name": None, "account_no": None, "ifsc": None, "branch": None}},
        "buyer": {"name": None, "gstin": None, "pan": None, "state": None,
                  "state_code": None, "address": None},
        "invoice": {"number": None, "date": None, "due_date": None, "challan_no": None,
                    "order_no": None, "order_date": None, "reference_no": None,
                    "irn": None, "ack_no": None, "irn_date": None, "eway_bill": None,
                    "lr_no": None, "lr_date": None, "transporter": None, "destination": None,
                    "delivery_note": None, "delivery_note_date": None, "tran_id": None,
                    "book_city": None, "broker": None, "terms": None, "agent": None},
        "line_items": [],
        "taxes": {"cgst_rate": 0, "cgst_amount": 0, "sgst_rate": 0, "sgst_amount": 0,
                  "igst_rate": 0, "igst_amount": 0, "tds_amount": 0,
                  "other_charges": 0, "freight": 0, "special_discount": 0, "round_off": 0},
        "totals": {"total_qty": None, "sub_total": None, "taxable_total": None,
                   "tax_total": None, "grand_total": None, "amount_in_words": None},
        "meta": {"grn_no": None, "grn_date": None, "received_by": None, "notes": None},
    }


class ExtractionProvider:
    name = "base"

    def available(self) -> bool:
        """Can this provider run in the current environment?"""
        return True

    def extract(self, image_path: str, profile: Optional[Dict[str, Any]] = None,
                ocr_text: Optional[str] = None) -> ProviderResult:
        raise NotImplementedError
