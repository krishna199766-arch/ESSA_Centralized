"""
Seed the database for first run:
  * create the 5 known suppliers (from the verified ground truth) — WITHOUT
    profiles, so they start 'not yet trained' and you can walk the train-once
    flow yourself;
  * ingest the 5 bundled sample invoices through the real extraction pipeline
    so the app opens with documents ready to review.

Idempotent: safe to re-run. Use --reset to wipe and rebuild.
"""
import os
import sys
import json
import hashlib
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base, engine, SessionLocal
from app import models
from app.config import GROUND_TRUTH_DIR, SAMPLE_DIR, UPLOAD_DIR
from app.extraction import engine as ext_engine


def load_ground_truth():
    docs = []
    for fn in sorted(os.listdir(GROUND_TRUTH_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(GROUND_TRUTH_DIR, fn)) as f:
                docs.append(json.load(f))
    return docs


def upsert_supplier(db, sup):
    existing = None
    if sup.get("gstin"):
        existing = db.query(models.Supplier).filter(models.Supplier.gstin == sup["gstin"]).first()
    if existing:
        return existing
    row = models.Supplier(
        name=sup.get("name"), gstin=sup.get("gstin"), pan=sup.get("pan"),
        state=sup.get("state"), state_code=sup.get("state_code"),
        address=sup.get("address"), phone=sup.get("phone"), email=sup.get("email"),
        bank=sup.get("bank") or {}, aliases=[],
    )
    db.add(row)
    db.flush()
    return row


def ingest_sample(db, gt):
    src = gt["source_file"]
    path = os.path.join(SAMPLE_DIR, src)
    if not os.path.exists(path):
        print(f"  ! sample image missing: {src}")
        return
    with open(path, "rb") as f:
        raw = f.read()
    content_hash = hashlib.sha256(raw).hexdigest()
    if db.query(models.Document).filter(models.Document.content_hash == content_hash).first():
        print(f"  = already ingested: {src}")
        return
    stored = os.path.join(UPLOAD_DIR, f"{content_hash[:16]}{os.path.splitext(src)[1]}")
    # write bytes (don't shutil.copy — that would inherit read-only perms from
    # the packaged sample and later block re-uploads of the same image)
    with open(stored, "wb") as f:
        f.write(raw)
    os.chmod(stored, 0o644)

    doc = models.Document(filename=src, stored_path=stored, content_hash=content_hash,
                          mime="image/jpeg", status="uploaded")
    db.add(doc)
    db.flush()

    ocr_text = ext_engine._ocr_text(stored)
    suppliers = db.query(models.Supplier).all()
    supplier, _ = ext_engine.detect_supplier(ocr_text, suppliers)
    result = ext_engine.run_extraction(stored, profile=None, ocr_text=ocr_text)
    if not supplier:
        gstin = (result["data"].get("supplier") or {}).get("gstin")
        supplier = next((s for s in suppliers if s.gstin == gstin), None)
    if supplier:
        doc.supplier_id = supplier.id

    ex = models.Extraction(document_id=doc.id, provider=result["provider"],
                           data=result["data"], confidence=result["confidence"],
                           warnings=result["warnings"], field_flags=result["field_flags"],
                           raw_text=result.get("raw_text", ""))
    db.add(ex)
    doc.status = "needs_review"
    print(f"  + ingested {src:32s} provider={result['provider']:12s} conf={result['confidence']:.2f} supplier={supplier.name if supplier else '?'}")


def main(reset=False, empty=False):
    if reset or empty:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        from app.services import masters as masters_svc
        n = masters_svc.import_categories(db, force=True)
        print(f"Imported {n} product categories from Excel master")
        if empty:
            print("Clean database ready — categories only, no suppliers/documents/LR data.")
            return
        gts = load_ground_truth()
        print(f"Seeding {len(gts)} suppliers (no profiles — train them yourself):")
        for gt in gts:
            s = upsert_supplier(db, gt["supplier"])
            print(f"  · {s.name} ({s.gstin}) [{s.state}]")
        db.commit()
        print("\nIngesting sample invoices through the pipeline:")
        for gt in gts:
            ingest_sample(db, gt)
        db.commit()
        n_docs = db.query(models.Document).count()
        n_sup = db.query(models.Supplier).count()
        print(f"\nDone. {n_sup} suppliers, {n_docs} documents.")
    finally:
        db.close()


if __name__ == "__main__":
    main(reset="--reset" in sys.argv, empty="--empty" in sys.argv)
