import os
import copy
import hashlib
import datetime as dt
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..config import UPLOAD_DIR, COMPANY_GSTIN
from ..extraction import engine
from ..schemas import ConfirmRequest

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _profile_dict(profile: models.SupplierProfile):
    if not profile:
        return None
    return {
        "template_key": profile.template_key,
        "default_tax_rates": profile.default_tax_rates or {},
        "tax_mode": profile.tax_mode,
        "has_tds": profile.has_tds,
        "uom_default": profile.uom_default,
        "column_map": profile.column_map or {},
        "reference_example": profile.reference_example or {},
    }


def _doc_out(doc: models.Document):
    ex = doc.latest_extraction
    data = ex.data if ex else {}
    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "supplier_id": doc.supplier_id,
        "supplier_name": doc.supplier.name if doc.supplier else (data.get("supplier", {}) or {}).get("name"),
        "grand_total": (data.get("totals", {}) or {}).get("grand_total"),
        "invoice_number": (data.get("invoice", {}) or {}).get("number"),
        "confidence": ex.confidence if ex else None,
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
    }


@router.get("")
def list_documents(db: Session = Depends(get_db)):
    docs = db.query(models.Document).order_by(models.Document.id.desc()).all()
    return [_doc_out(d) for d in docs]


@router.delete("/clear-all")
def clear_all(wipe_masters: bool = False, db: Session = Depends(get_db)):
    """Wipe all operational data (documents, extractions, LR entries, GRNs,
    inventory, outwards, returns, payments) for a clean slate. Deletes children
    before parents. By default KEEPS suppliers + trained profiles and the
    category master. Pass wipe_masters=true for a fully empty test database
    (also clears suppliers, profiles, agents, transporters — categories stay)."""
    # Every table is emptied with a BULK delete, which is fast but does not load
    # rows — so SQLAlchemy's `cascade="all, delete-orphan"` never fires and each
    # child has to be listed here explicitly. Anything hanging off Product or
    # PurchaseLine that is missing from this list survives the wipe as orphan rows,
    # and because ids restart afterwards it is then silently adopted by whatever
    # new row lands on the same id: piece codes reappearing under an unrelated
    # product, cartons pointing at lines that no longer exist. Add new child
    # tables here at the same time as you add them to models.py.
    order = [
        models.PaymentAllocation, models.Payment,
        models.PurchaseReturnLine, models.PurchaseReturn,
        models.StockOutwardLine, models.StockOutward,
        models.StockMovement,
        models.ProductUnit,                       # per-piece codes (Product child)
        models.Bundle,                            # carton labels (PurchaseLine child)
        models.GrnShortage,                       # billed-but-not-received
        models.PurchaseLineSplit, models.PurchaseLine, models.Purchase,
        models.LREntry,
        models.LineItem, models.Extraction, models.Document,
        models.Product,
    ]
    if wipe_masters:
        order += [models.SupplierProfile, models.Supplier, models.Agent, models.Transport]
    counts = {}
    for M in order:
        counts[M.__tablename__] = db.query(M).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "cleared": counts,
            "kept": "category master" + ("" if wipe_masters else " + suppliers + trained profiles")}


@router.delete("/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    """Delete a single document and its extractions/line-items, plus any DRAFT
    GRN built from it. Blocks if its GRN is already posted to inventory (use
    Clear all for a full reset)."""
    doc = db.get(models.Document, doc_id)
    if not doc:
        raise HTTPException(404, "document not found")
    posted = db.query(models.Purchase).filter(
        models.Purchase.document_id == doc_id,
        models.Purchase.status == "posted").first()
    if posted:
        raise HTTPException(400, "This document's GRN is already posted to inventory. "
                                 "Use 'Clear all' to reset everything.")
    for p in db.query(models.Purchase).filter(models.Purchase.document_id == doc_id).all():
        db.delete(p)          # cascades purchase_lines
    db.delete(doc)            # cascades extractions + line_items
    db.commit()
    return {"ok": True, "deleted_document": doc_id}


@router.post("/upload")
async def upload_and_extract(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    content_hash = hashlib.sha256(raw).hexdigest()
    ext = os.path.splitext(file.filename)[1] or ".bin"
    stored = os.path.join(UPLOAD_DIR, f"{content_hash[:16]}{ext}")
    # Filename is the content hash, so an existing file has identical bytes —
    # don't rewrite it (also avoids clobbering read-only seeded sample files).
    if not os.path.exists(stored):
        with open(stored, "wb") as f:
            f.write(raw)
        os.chmod(stored, 0o644)

    doc = models.Document(filename=file.filename, stored_path=stored,
                          content_hash=content_hash, mime=file.content_type,
                          status="uploaded")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # detect supplier via OCR header, load its learned profile
    ocr_text = engine._ocr_text(stored)
    suppliers = db.query(models.Supplier).all()
    supplier, _ = engine.detect_supplier(ocr_text, suppliers)
    profile = None
    if supplier:
        doc.supplier_id = supplier.id
        profile = _profile_dict(supplier.active_profile)

    result = engine.run_extraction(stored, profile=profile, ocr_text=ocr_text)

    # Back-fill the consignment fields (LR no & date, transporter, booking city)
    # from the matching LR register row BEFORE the extraction is stored, so the
    # review form opens with its transport block already populated. Provenance
    # goes in meta so the UI can badge those fields after a reload too.
    from ..services import lr_link
    lr_source = lr_link.fill_invoice_from_lr(db, result["data"], invoice_doc_id=doc.id)
    if lr_source:
        result["data"].setdefault("meta", {})["lr_source"] = lr_source
        result["warnings"] = result["warnings"] + lr_link.describe_invoice_fill(lr_source)

    # if the seeded/vision result named a supplier we didn't detect from OCR, link it
    if not doc.supplier_id:
        gstin = (result["data"].get("supplier") or {}).get("gstin")
        if gstin:
            match = db.query(models.Supplier).filter(models.Supplier.gstin == gstin).first()
            if match:
                doc.supplier_id = match.id

    ex = models.Extraction(
        document_id=doc.id, provider=result["provider"],
        profile_id=(supplier.active_profile.id if supplier and supplier.active_profile else None),
        data=result["data"], confidence=result["confidence"],
        warnings=result["warnings"], field_flags=result["field_flags"],
        raw_text=result.get("raw_text", ""),
    )
    db.add(ex)
    doc.status = "confirmed" if result["confidence"] >= 0.95 and not result["field_flags"] else "needs_review"
    db.commit()
    db.refresh(doc)
    db.refresh(ex)

    # cross-fill: if this invoice matches an existing LR row, fill its blanks
    lr_filled = lr_link.fill_lr_from_invoice(db, ex.data, doc.id)

    return {
        "document": _doc_out(doc),
        "extraction": {
            "id": ex.id, "provider": ex.provider, "confidence": ex.confidence,
            "warnings": ex.warnings, "field_flags": ex.field_flags, "data": ex.data,
        },
        "supplier_recognised": bool(supplier),
        "profile_used": bool(profile),
        "lr_filled": lr_filled,
        "lr_source": lr_source,
    }


@router.get("/{doc_id}")
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(models.Document, doc_id)
    if not doc:
        raise HTTPException(404, "document not found")
    ex = doc.latest_extraction
    return {
        "document": _doc_out(doc),
        "extraction": ({"id": ex.id, "provider": ex.provider, "confidence": ex.confidence,
                        "warnings": ex.warnings, "field_flags": ex.field_flags,
                        "data": ex.data, "is_correction": ex.is_correction} if ex else None),
        "history": [{"id": e.id, "provider": e.provider, "confidence": e.confidence,
                     "is_correction": e.is_correction,
                     "created_at": e.created_at.isoformat()} for e in doc.extractions],
    }


@router.get("/{doc_id}/image")
def get_image(doc_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    doc = db.get(models.Document, doc_id)
    if not doc or not os.path.exists(doc.stored_path):
        raise HTTPException(404, "image not found")
    return FileResponse(doc.stored_path)


def _searched_for(data):
    """What the matcher actually looked for — so a failure explains itself."""
    inv = data.get("invoice") or {}
    num, lr = inv.get("number"), inv.get("lr_no")
    bits = [f"invoice no {num}" if num else "no invoice no on this document"]
    bits.append(f"LR no {lr}" if lr else "no LR no printed on this invoice")
    return " and ".join(bits)


@router.post("/{doc_id}/fetch-transport")
def fetch_transport(doc_id: int, lr_entry_id: Optional[int] = None,
                    db: Session = Depends(get_db)):
    """Pull this invoice's consignment fields (LR No & date, transporter, booking
    city) in from the LR register on demand.

    Upload already does this automatically, but the register is often photographed
    *after* the invoices it covers — this is how those invoices catch up without
    re-uploading. Same matching, quantity gate and blank-only rules as the
    automatic pass. A fill is recorded as a new extraction so the audit trail
    still shows where the values came from; a no-op leaves the document alone.

    Pass `lr_entry_id` to link a row the user picked from the suggestions. That
    is an explicit human decision, so it bypasses the automatic quantity gate —
    but the disagreement is still reported, and the row is linked back to this
    document so the register shows the pairing too.

    When nothing matches, the response carries `candidates`: register rows for
    the same supplier, ranked. Suggestions only — a supplier-name agreement is
    never enough to fill on its own."""
    from ..services import lr_link
    doc = db.get(models.Document, doc_id)
    if not doc:
        raise HTTPException(404, "document not found")
    prev = doc.latest_extraction
    if not prev:
        raise HTTPException(400, "this document has no extraction to fill")

    data = copy.deepcopy(prev.data or {})

    if lr_entry_id is not None:
        # the service links the row and pushes the invoice's no/date back into it
        report = lr_link.fill_invoice_from_lr_entry(db, data, lr_entry_id,
                                                    invoice_doc_id=doc.id)
        if report is None:
            raise HTTPException(404, f"LR register row {lr_entry_id} not found")
        lr = db.get(models.LREntry, lr_entry_id)
        notes = lr_link.describe_invoice_fill(report)
        detail = (f"Linked LR row {lr.lr_no or lr_entry_id}."
                  + (f" Filled {len(report['filled'])} field(s)." if report["filled"]
                     else " Nothing blank left to fill.")
                  + _register_fill_note(report))
        return _persist_lr_fill(db, doc, prev, data, report, notes, detail,
                                matched=True, always=True)

    report = lr_link.fill_invoice_from_lr(db, data, invoice_doc_id=doc.id)
    notes = lr_link.describe_invoice_fill(report)

    if not report:
        cands = lr_link.suggest_lr_candidates(db, data)
        detail = f"No register row carries {_searched_for(data)}."
        if cands:
            detail += (f" {len(cands)} row(s) for this supplier are listed below — "
                       "pick the consignment this invoice belongs to.")
        else:
            detail += " No register row for this supplier either."
        return {"ok": True, "matched": False, "filled": {}, "notes": [],
                "candidates": cands, "detail": detail}

    if not report.get("filled"):
        blocked = report.get("qty") == "mismatch"
        detail = ("Matched the register, but the quantities disagree — nothing filled."
                  if blocked else
                  "Matched the register, but there was nothing blank left to fill.")
        # a gate-blocked row is still the likeliest answer: offer it for linking
        forced = ([db.get(models.LREntry, i) for i in report["matched_lr_ids"]]
                  if blocked else [])
        return {"ok": True, "matched": True, "filled": {}, "notes": notes,
                "lr_source": report, "detail": detail,
                "candidates": (lr_link.suggest_lr_candidates(
                    db, data, include=[r for r in forced if r]) if blocked else None)}

    return _persist_lr_fill(db, doc, prev, data, report, notes,
                            f"Filled {len(report['filled'])} field(s) from the LR register."
                            + _register_fill_note(report),
                            matched=True)


_LR_FIELD_LABELS = {"inv_no": "Inv No", "inv_date": "Inv Date", "amount": "Amount",
                    "qty": "Qty", "item": "Item",
                    "freight_amount": "Freight", "supplier_name": "Supplier",
                    "transport": "Transport"}


def _register_fill_note(report):
    """" … and wrote Inv No + Inv Date back into the register." — the other half of
    the link, so the user knows the register row is now complete too."""
    written = sorted({f for fields in (report.get("register_filled") or {}).values()
                      for f in fields})
    if not written:
        return ""
    names = ", ".join(_LR_FIELD_LABELS.get(f, f) for f in written)
    return f" Wrote {names} back into the LR register."


def _persist_lr_fill(db, doc, prev, data, report, notes, detail, matched, always=False):
    """Record a register-sourced fill as a new extraction (audit trail intact).
    A fill that changed nothing adds no extraction row unless `always`."""
    ex_id = None
    if report.get("filled") or always:
        data.setdefault("meta", {})["lr_source"] = report
    if report.get("filled"):
        ex = models.Extraction(document_id=doc.id, provider="lr_register", data=data,
                               confidence=prev.confidence,
                               warnings=(prev.warnings or []) + notes,
                               field_flags=prev.field_flags or {},
                               is_correction=False)
        db.add(ex)
    db.commit()
    if report.get("filled"):
        db.refresh(ex)
        ex_id = ex.id
    return {"ok": True, "matched": matched, "filled": report.get("filled", {}),
            "notes": notes, "lr_source": report, "extraction_id": ex_id,
            "data": data, "detail": detail}


#: Date fields inside the canonical invoice. Normalised when a human confirms the
#: extraction — this is the moment the read stops being a guess and starts being a
#: record, and `invoice.date` in particular becomes `Purchase.invoice_date`, which
#: everything downstream ages and sorts a payable by.
_DATE_PATHS = ("invoice.date", "invoice.due_date", "invoice.order_date",
               "invoice.irn_date", "invoice.lr_date", "invoice.eway_date",
               "meta.grn_date")


def normalise_dates(data):
    """Rewrite the known date fields of a canonical invoice to ISO.

    Only the paths above, and only when they parse: a date the parser cannot read
    is left as the supplier wrote it, because the printed page is the record and a
    blank is a worse answer than an odd string."""
    from ..services import dates
    if not isinstance(data, dict):
        return data
    for path in _DATE_PATHS:
        section, _, key = path.partition(".")
        block = data.get(section)
        if isinstance(block, dict) and block.get(key):
            block[key] = dates.normalise(block[key])
    return data


@router.post("/{doc_id}/confirm")
def confirm(doc_id: int, req: ConfirmRequest, db: Session = Depends(get_db)):
    """Save the human-corrected extraction and (optionally) learn the supplier
    profile from it — this is the 'train once per format' action."""
    doc = db.get(models.Document, doc_id)
    if not doc:
        raise HTTPException(404, "document not found")
    corrected = normalise_dates(req.data)

    # persist the correction as a new extraction row (audit trail preserved)
    ex = models.Extraction(document_id=doc.id, provider="human", data=corrected,
                           confidence=1.0, warnings=[], field_flags={},
                           is_correction=True)
    db.add(ex)

    # ensure supplier exists / update it
    sup_info = corrected.get("supplier") or {}
    supplier = None
    if sup_info.get("gstin"):
        supplier = db.query(models.Supplier).filter(models.Supplier.gstin == sup_info["gstin"]).first()
    if not supplier and sup_info.get("name"):
        supplier = models.Supplier(name=sup_info["name"], gstin=sup_info.get("gstin"))
        db.add(supplier)
        db.flush()
    if supplier:
        supplier.name = sup_info.get("name") or supplier.name
        supplier.gstin = sup_info.get("gstin") or supplier.gstin
        supplier.state = sup_info.get("state") or supplier.state
        supplier.state_code = sup_info.get("state_code") or supplier.state_code
        supplier.address = sup_info.get("address") or supplier.address
        supplier.bank = sup_info.get("bank") or supplier.bank
        doc.supplier_id = supplier.id

    # auto-populate masters from whatever the extractor read (no manual entry)
    from ..services import masters as masters_svc
    inv_info = corrected.get("invoice") or {}
    masters_svc.get_or_create_transport(db, inv_info.get("transporter"))
    masters_svc.get_or_create_agent(db, inv_info.get("agent"))

    trained = False
    if req.train and supplier:
        spec = engine.learn_from_correction(corrected)
        existing = supplier.active_profile
        if existing:
            existing.is_active = False
            new_version = existing.version + 1
            samples = existing.sample_count + 1
        else:
            new_version, samples = 1, 1
        profile = models.SupplierProfile(
            supplier_id=supplier.id, template_key=spec["template_key"],
            version=new_version, is_active=True,
            detect_gstin=spec["detect_gstin"], detect_keywords=spec["detect_keywords"],
            tax_mode=spec["tax_mode"], default_tax_rates=spec["default_tax_rates"],
            has_tds=spec["has_tds"], uom_default=spec["uom_default"],
            reference_example=spec["reference_example"],
            trained_from_document_id=doc.id, sample_count=samples,
        )
        db.add(profile)
        trained = True

    # denormalise confirmed line items for reporting
    db.query(models.LineItem).filter(models.LineItem.document_id == doc.id).delete()
    for it in corrected.get("line_items", []):
        db.add(models.LineItem(document_id=doc.id, barcode=it.get("barcode"),
                               description=it.get("description"), hsn=it.get("hsn"),
                               qty=it.get("qty"), uom=it.get("uom"),
                               rate=it.get("rate"), amount=it.get("amount")))
    doc.status = "confirmed"
    db.commit()

    # cross-fill LR rows from the (corrected) invoice too
    from ..services import lr_link
    lr_filled = lr_link.fill_lr_from_invoice(db, corrected, doc.id)

    return {"ok": True, "trained_profile": trained,
            "supplier_id": supplier.id if supplier else None,
            "lr_filled": lr_filled}


@router.get("/{doc_id}/export")
def export_document(doc_id: int, format: str = "json", db: Session = Depends(get_db)):
    """Export the confirmed invoice as a purchase-entry payload (JSON) or a
    line-item CSV ready to import into the ERP purchase/GRN module."""
    doc = db.get(models.Document, doc_id)
    if not doc or not doc.latest_extraction:
        raise HTTPException(404, "nothing to export")
    data = doc.latest_extraction.data
    if format == "csv":
        import io, csv
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["barcode", "description", "hsn", "qty", "uom", "rate", "amount"])
        for it in data.get("line_items", []):
            w.writerow([it.get("barcode", ""), it.get("description", ""), it.get("hsn", ""),
                        it.get("qty", ""), it.get("uom", ""), it.get("rate", ""), it.get("amount", "")])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")
    # default: purchase-entry JSON payload
    t = data.get("totals", {})
    payload = {
        "purchase_entry": {
            "supplier_gstin": (data.get("supplier") or {}).get("gstin"),
            "supplier_name": (data.get("supplier") or {}).get("name"),
            "invoice_number": (data.get("invoice") or {}).get("number"),
            "invoice_date": (data.get("invoice") or {}).get("date"),
            "taxable_total": t.get("taxable_total"),
            "tax_total": t.get("tax_total"),
            "grand_total": t.get("grand_total"),
            "taxes": data.get("taxes"),
            "line_items": data.get("line_items"),
        }
    }
    return JSONResponse(payload)
