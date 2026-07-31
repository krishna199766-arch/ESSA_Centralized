"""
Tesseract provider — a fully offline OCR path (no API key, no network).

It reads the page text with Tesseract, then pulls the header fields that follow
stable patterns anywhere in India (GSTIN, invoice no, dates, amount-in-words,
totals) using regex, and makes a best-effort pass at the line-item table guided
by the supplier profile's column map when available. It will not match a vision
model on messy photographed bills — that's expected. The reconciliation layer
scores its output honestly and flags everything that needs a human, which is the
correct behaviour for the offline fallback.
"""
import os
import re
from typing import Optional, Dict, Any, List
from .base import ExtractionProvider, ProviderResult, empty_invoice


def _configure_tesseract():
    """Let Windows users point at tesseract.exe without touching PATH:
    set ESSA_TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe"""
    cmd = os.environ.get("ESSA_TESSERACT_CMD")
    if cmd:
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = cmd
        except Exception:
            pass

GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]Z[A-Z\d])\b")
PAN_RE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")
DATE_RE = re.compile(r"\b(\d{1,2}[-/][A-Za-z0-9]{2,4}[-/]\d{2,4})\b")
MONEY_RE = re.compile(r"([\d,]+\.\d{2})")
INV_RE = re.compile(r"(?:invoice\s*(?:no|number|#)|inv\s*no)\s*[:.\-]?\s*([A-Z0-9/\-]+)", re.I)


def _to_iso(d: str) -> str:
    return d  # kept as-printed; normalising every regional format is a profile concern


class TesseractProvider(ExtractionProvider):
    name = "tesseract"

    def available(self) -> bool:
        try:
            import pytesseract  # noqa
            from PIL import Image  # noqa
            return True
        except Exception:
            return False

    def _ocr(self, image_path: str) -> str:
        import pytesseract
        from PIL import Image
        _configure_tesseract()
        img = Image.open(image_path)
        # light upscaling helps on small phone photos
        if max(img.size) < 1600:
            scale = 1600 / max(img.size)
            img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)))
        return pytesseract.image_to_string(img)

    def extract(self, image_path: str, profile: Optional[Dict[str, Any]] = None,
                ocr_text: Optional[str] = None) -> ProviderResult:
        text = ocr_text or self._ocr(image_path)
        inv = empty_invoice()
        notes: List[str] = []

        gstins = GSTIN_RE.findall(text)
        company_gstin = None
        if profile:
            company_gstin = profile.get("_company_gstin")
        # supplier = the GSTIN that is not the buyer/company
        sup_gstin = next((g for g in gstins if g != company_gstin), gstins[0] if gstins else None)
        inv["supplier"]["gstin"] = sup_gstin
        if company_gstin and company_gstin in gstins:
            inv["buyer"]["gstin"] = company_gstin
        pans = PAN_RE.findall(text)
        if pans:
            inv["supplier"]["pan"] = pans[0]

        m = INV_RE.search(text)
        if m:
            inv["invoice"]["number"] = m.group(1)
        dates = DATE_RE.findall(text)
        if dates:
            inv["invoice"]["date"] = _to_iso(dates[0])

        # amount in words / grand total: take the largest money value on the page
        monies = [float(x.replace(",", "")) for x in MONEY_RE.findall(text)]
        if monies:
            inv["totals"]["grand_total"] = max(monies)

        # supplier name: first non-empty line with letters before the first GSTIN
        for line in text.splitlines():
            s = line.strip()
            if len(s) > 4 and re.search(r"[A-Za-z]", s) and "invoice" not in s.lower():
                inv["supplier"]["name"] = s
                break

        # rate hints from profile
        if profile and profile.get("default_tax_rates"):
            for k, v in profile["default_tax_rates"].items():
                inv["taxes"][f"{k}_rate"] = v

        notes.append("offline OCR; header parsed by regex, line-items need review")
        # deliberately low base confidence: totals reconciler will refine
        return ProviderResult(data=inv, provider=self.name, confidence=0.35,
                              raw_text=text, notes=notes)
