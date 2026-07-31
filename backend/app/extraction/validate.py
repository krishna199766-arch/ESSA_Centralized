"""
Reconciliation & confidence layer — the 'advanced' part of the pipeline.

Whatever a provider returns, this module:
  1. coerces types and fills derived fields (line amount = qty*rate, tax from taxable),
  2. applies the supplier profile's defaults (e.g. inter-state => IGST),
  3. cross-checks the arithmetic the invoice itself must satisfy
     (sum(qty)=total_qty, taxable*rate=tax, taxable+tax(+charges)=grand_total),
  4. emits human-readable warnings, per-field review flags, and an overall
     confidence score.

Because these checks are the invoice's own internal identities, they catch OCR
slips (a misread digit almost always breaks a total) without any ground truth.
"""
from typing import Dict, Any, List, Tuple

MONEY_TOL = 1.5          # rupees of slack for rounding
RATE_TOL = 0.02          # fractional slack when reconstructing tax


def _f(x, default=0.0):
    if x is None or x == "":
        return default
    try:
        if isinstance(x, str):
            x = x.replace(",", "").replace("₹", "").strip()
        return float(x)
    except (TypeError, ValueError):
        return default


def _apply_profile_defaults(inv: Dict[str, Any], profile: Dict[str, Any]) -> None:
    if not profile:
        return
    tx = inv.setdefault("taxes", {})
    rates = profile.get("default_tax_rates") or {}
    # only fill when the provider left the tax structure empty
    has_any_tax = any(_f(tx.get(k)) for k in
                      ("cgst_amount", "sgst_amount", "igst_amount",
                       "cgst_rate", "sgst_rate", "igst_rate"))
    if not has_any_tax:
        for k, v in rates.items():
            tx.setdefault(f"{k}_rate", v)
    if profile.get("uom_default"):
        for it in inv.get("line_items", []):
            if not it.get("uom"):
                it["uom"] = profile["uom_default"]


def _infer_tax_mode(inv: Dict[str, Any], company_gstin: str) -> str:
    """Inter-state (IGST) vs intra-state (CGST+SGST) from the two GSTINs."""
    sup = (inv.get("supplier") or {}).get("gstin") or ""
    buy = (inv.get("buyer") or {}).get("gstin") or company_gstin or ""
    if len(sup) >= 2 and len(buy) >= 2:
        return "intra_state" if sup[:2] == buy[:2] else "inter_state"
    return "unknown"


def normalise_and_check(inv: Dict[str, Any], profile: Dict[str, Any] = None,
                        company_gstin: str = "") -> Tuple[Dict[str, Any], List[str], Dict[str, str], float]:
    warnings: List[str] = []
    flags: Dict[str, str] = {}
    profile = profile or {}

    _apply_profile_defaults(inv, profile)

    items = inv.get("line_items") or []
    tx = inv.setdefault("taxes", {})
    tot = inv.setdefault("totals", {})

    # ---- line items: derive missing amount/qty/rate where two of three known ----
    line_amt_sum = 0.0
    qty_sum = 0.0
    for i, it in enumerate(items):
        qty, rate, amt = _f(it.get("qty")), _f(it.get("rate")), _f(it.get("amount"))
        taxable = _f(it.get("taxable_value"))
        base = taxable or amt
        if not amt and qty and rate:
            it["amount"] = amt = round(qty * rate, 2)
        if amt and qty and rate:
            if abs(qty * rate - amt) > max(MONEY_TOL, amt * 0.02) and abs(qty * rate - taxable) > MONEY_TOL:
                warnings.append(f"Line {i+1} ({it.get('description','?')}): qty×rate={qty*rate:.2f} ≠ amount {amt:.2f}")
                flags[f"line_items.{i}.amount"] = "review"
        qty_sum += qty
        line_amt_sum += (taxable or amt)

    # ---- total qty ----
    stated_qty = _f(tot.get("total_qty"))
    if stated_qty and abs(stated_qty - qty_sum) > 0.5:
        warnings.append(f"Sum of line quantities {qty_sum:g} ≠ stated total qty {stated_qty:g}")
        flags["totals.total_qty"] = "review"
    elif not stated_qty and qty_sum:
        tot["total_qty"] = qty_sum

    # ---- taxable total ----
    stated_taxable = _f(tot.get("taxable_total")) or _f(tot.get("sub_total"))
    if not stated_taxable and line_amt_sum:
        tot["taxable_total"] = stated_taxable = round(line_amt_sum, 2)
    if stated_taxable and line_amt_sum and abs(stated_taxable - line_amt_sum) > max(MONEY_TOL, stated_taxable * 0.02):
        warnings.append(f"Line values sum {line_amt_sum:.2f} ≠ taxable total {stated_taxable:.2f}")
        flags["totals.taxable_total"] = "review"

    # ---- taxes: reconstruct from rates, or infer rate from amount ----
    taxable = stated_taxable or line_amt_sum
    tax_mode = _infer_tax_mode(inv, company_gstin)
    igst_r, cgst_r, sgst_r = _f(tx.get("igst_rate")), _f(tx.get("cgst_rate")), _f(tx.get("sgst_rate"))
    igst_a, cgst_a, sgst_a = _f(tx.get("igst_amount")), _f(tx.get("cgst_amount")), _f(tx.get("sgst_amount"))

    if taxable:
        if igst_r and not igst_a:
            tx["igst_amount"] = igst_a = round(taxable * igst_r / 100, 2)
        if cgst_r and not cgst_a:
            tx["cgst_amount"] = cgst_a = round(taxable * cgst_r / 100, 2)
        if sgst_r and not sgst_a:
            tx["sgst_amount"] = sgst_a = round(taxable * sgst_r / 100, 2)
        # infer rate from amount when only amount present
        if igst_a and not igst_r:
            tx["igst_rate"] = round(igst_a / taxable * 100, 2)
        # verify each stated tax amount against rate
        for label, r, a in (("IGST", igst_r, igst_a), ("CGST", cgst_r, cgst_a), ("SGST", sgst_r, sgst_a)):
            if r and a and abs(taxable * r / 100 - a) > max(MONEY_TOL, a * RATE_TOL):
                warnings.append(f"{label}: {r}% of {taxable:.0f} = {taxable*r/100:.2f} ≠ stated {a:.2f}")
                flags[f"taxes.{label.lower()}_amount"] = "review"

    # tax mode sanity
    if tax_mode == "inter_state" and (cgst_a or sgst_a):
        warnings.append("Supplier & buyer are in different states but CGST/SGST present (expected IGST)")
    if tax_mode == "intra_state" and igst_a:
        warnings.append("Supplier & buyer are in the same state but IGST present (expected CGST+SGST)")

    tax_total = round(igst_a + cgst_a + sgst_a, 2)
    if tax_total:
        tot["tax_total"] = tax_total

    # ---- grand total ----
    charges = _f(tx.get("other_charges")) + _f(tx.get("freight_in_total", 0))
    round_off = _f(tx.get("round_off"))
    recon = taxable + tax_total + charges + round_off
    stated_grand = _f(tot.get("grand_total"))
    if not stated_grand and recon:
        tot["grand_total"] = round(recon, 2)
    elif stated_grand:
        # try both with and without 'other_charges' since layouts differ
        candidates = [recon, recon - charges + _f(tx.get("other_charges"))]
        if all(abs(stated_grand - c) > max(MONEY_TOL, stated_grand * 0.03) for c in candidates):
            warnings.append(f"Reconstructed grand total ≈ {recon:.2f} ≠ stated {stated_grand:.2f}")
            flags["totals.grand_total"] = "review"

    # ---- missing critical fields ----
    critical = {
        "supplier.gstin": (inv.get("supplier") or {}).get("gstin"),
        "invoice.number": (inv.get("invoice") or {}).get("number"),
        "invoice.date": (inv.get("invoice") or {}).get("date"),
        "totals.grand_total": tot.get("grand_total"),
    }
    for path, val in critical.items():
        if not val:
            warnings.append(f"Missing {path}")
            flags[path] = "review"
    if not items:
        warnings.append("No line items extracted")
        flags["line_items"] = "review"

    inv.setdefault("meta", {})["tax_mode"] = tax_mode

    # ---- confidence: start high, penalise each problem ----
    conf = 1.0
    conf -= 0.10 * len(warnings)
    conf -= 0.05 * len(flags)
    if not items:
        conf -= 0.3
    confidence = max(0.05, min(1.0, round(conf, 3)))

    return inv, warnings, flags, confidence
