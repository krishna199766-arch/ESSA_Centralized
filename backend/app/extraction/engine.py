"""
Orchestrator. One entry point (`run_extraction`) that:

  1. gets OCR text once (cheap) and detects the supplier by GSTIN / name,
  2. loads that supplier's learned profile (if any),
  3. picks the best available provider  (seeded > vision > tesseract),
  4. runs it, then normalises + reconciles the result,
  5. returns a canonical invoice + confidence + warnings + field flags.

And `learn_from_correction`, which turns a human-confirmed extraction into (or
updates) the supplier's profile — the "train once per format" step.
"""
from typing import Optional, Dict, Any, Tuple, List
from .seeded import SeededProvider
from .tesseract_ocr import TesseractProvider
from .claude_vision import ClaudeVisionProvider
from .validate import normalise_and_check
from .base import ProviderResult
from ..config import COMPANY_GSTIN
from .. import runtime

_seeded = SeededProvider()
_tesseract = TesseractProvider()
_vision = ClaudeVisionProvider()


def _ocr_text(image_path: str) -> str:
    if _tesseract.available():
        try:
            return _tesseract._ocr(image_path)
        except Exception:
            return ""
    return ""


def choose_live_provider():
    """The provider used for genuinely new uploads (seeded handled separately)."""
    pref = runtime.get("provider_preference") or "auto"
    if pref == "claude_vision" and _vision.available():
        return _vision
    if pref == "tesseract" and _tesseract.available():
        return _tesseract
    # auto
    if _vision.available():
        return _vision
    if _tesseract.available():
        return _tesseract
    return None


def detect_supplier(ocr_text: str, suppliers: List[Any]) -> Tuple[Optional[Any], List[str]]:
    """Match an uploaded page to a known supplier by GSTIN first, then name.
    `suppliers` are ORM Supplier rows. Returns (supplier|None, detected_keywords)."""
    import re
    from rapidfuzz import fuzz
    gstins = set(re.findall(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d]Z[A-Z\d]\b", ocr_text or ""))
    gstins.discard(COMPANY_GSTIN)
    for s in suppliers:
        if s.gstin and s.gstin in gstins:
            return s, list(gstins)
    # fuzzy name match against the OCR header
    head = (ocr_text or "")[:600].upper()
    best, best_score = None, 0
    for s in suppliers:
        names = [s.name] + (s.aliases or [])
        for nm in names:
            if not nm:
                continue
            score = fuzz.partial_ratio(nm.upper(), head)
            if score > best_score:
                best, best_score = s, score
    if best_score >= 85:
        return best, list(gstins)
    return None, list(gstins)


def run_extraction(image_path, profile: Optional[Dict[str, Any]] = None,
                   ocr_text: Optional[str] = None) -> Dict[str, Any]:
    """`image_path` is one path, or a list of them for one invoice spread over
    several pages. Only the vision provider reads more than the first: the
    seeded provider matches a single known sample, and OCR of page two on its
    own produces text with no invoice around it."""
    paths = [image_path] if isinstance(image_path, str) else list(image_path)
    first = paths[0]
    if ocr_text is None:
        ocr_text = "\n".join(t for t in (_ocr_text(p) for p in paths) if t)

    # 1. bundled sample? return verified truth.
    seeded = _seeded.extract(first, profile, ocr_text)
    if seeded.confidence > 0:
        result = seeded
    else:
        prov = choose_live_provider()
        if prov is None:
            # nothing available: hand back an empty shell for manual entry
            from .base import empty_invoice
            result = ProviderResult(data=empty_invoice(), provider="manual",
                                    confidence=0.0, raw_text=ocr_text,
                                    notes=["no extraction provider available; manual entry"])
        else:
            pdata = dict(profile or {})
            pdata["_company_gstin"] = COMPANY_GSTIN
            try:
                result = prov.extract(paths if len(paths) > 1 else first, pdata, ocr_text)
            except Exception as e:
                # a vision/API failure must never 500 the upload — fall back to
                # offline OCR (or an empty shell) and flag it for review.
                note = f"{prov.name} failed ({type(e).__name__}); fell back to OCR"
                if prov is not _tesseract and _tesseract.available():
                    result = _tesseract.extract(first, pdata, ocr_text)
                    result.notes.append(note)
                else:
                    from .base import empty_invoice
                    result = ProviderResult(data=empty_invoice(), provider="manual",
                                            confidence=0.0, raw_text=ocr_text, notes=[note])

    inv, warnings, flags, confidence = normalise_and_check(
        result.data, profile=profile, company_gstin=COMPANY_GSTIN)

    # blend provider confidence with reconciliation confidence
    blended = round(0.5 * result.confidence + 0.5 * confidence, 3)
    return {
        "data": inv,
        "provider": result.provider,
        "confidence": blended,
        "warnings": warnings + [n for n in result.notes if n],
        "field_flags": flags,
        "raw_text": result.raw_text,
    }


def learn_from_correction(corrected: Dict[str, Any]) -> Dict[str, Any]:
    """Derive a supplier profile from a confirmed extraction: detection keys,
    tax behaviour, UOM default, and the confirmed doc as a reference example."""
    sup = corrected.get("supplier") or {}
    tx = corrected.get("taxes") or {}
    buyer_gstin = (corrected.get("buyer") or {}).get("gstin") or COMPANY_GSTIN
    sup_gstin = sup.get("gstin")

    inter = bool(sup_gstin and buyer_gstin and sup_gstin[:2] != buyer_gstin[:2])
    rates = {}
    if inter:
        r = tx.get("igst_rate") or 0
        if r:
            rates["igst"] = r
    else:
        if tx.get("cgst_rate"):
            rates["cgst"] = tx["cgst_rate"]
            rates["sgst"] = tx.get("sgst_rate", tx["cgst_rate"])

    uoms = [it.get("uom") for it in corrected.get("line_items", []) if it.get("uom")]
    uom_default = max(set(uoms), key=uoms.count) if uoms else "PCS"

    keywords = [w for w in [sup.get("name")] if w]

    return {
        "template_key": corrected.get("template_key") or (sup.get("name") or "supplier").lower().replace(" ", "_"),
        "detect_gstin": sup_gstin,
        "detect_keywords": keywords,
        "tax_mode": "inter_state" if inter else "intra_state",
        "default_tax_rates": rates,
        "has_tds": bool(tx.get("tds_amount")),
        "uom_default": uom_default,
        "reference_example": corrected,
    }


def provider_status() -> Dict[str, Any]:
    return {
        "seeded": _seeded.available(),
        "tesseract": _tesseract.available(),
        "claude_vision": _vision.available(),
        "active_live_provider": getattr(choose_live_provider(), "name", None),
    }
