import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .database import Base, engine
from . import models  # noqa: F401  (register tables)
from .routers import (documents, suppliers, purchases, inventory, outward,
                      payments, returns, reports, settings, auth, masters, lr,
                      bundles)
from .extraction.engine import provider_status
from .config import COMPANY_NAME, COMPANY_GSTIN
from . import runtime

Base.metadata.create_all(bind=engine)


def _migrate():
    """Add columns introduced after a DB was first created (SQLite), so existing
    installs pick up new Product attributes without losing data."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "products" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("products")}
    adds = {"color": "VARCHAR", "size": "VARCHAR", "pattern": "VARCHAR", "fit": "VARCHAR",
            "product_type": "VARCHAR", "material": "VARCHAR", "design_no": "VARCHAR",
            "sale_price": "FLOAT", "sale_discount_pct": "FLOAT", "detailed": "BOOLEAN",
            "detailed_at": "DATETIME", "detailed_by": "VARCHAR",
            "category": "VARCHAR", "category_section": "VARCHAR"}
    missing = {k: v for k, v in adds.items() if k not in have}
    if missing:
        with engine.begin() as conn:
            for name, typ in missing.items():
                conn.execute(text(f"ALTER TABLE products ADD COLUMN {name} {typ}"))
    # margin over cost gave way to discount off MRP — see Product.sale_discount_pct
    if "margin_pct" in have:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE products DROP COLUMN margin_pct"))
        except Exception:
            pass
    # documents: document_type (invoice | lr_register | purchase_order)
    if "documents" in insp.get_table_names():
        dcols = {c["name"] for c in insp.get_columns("documents")}
        if "document_type" not in dcols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE documents ADD COLUMN document_type VARCHAR"))
    # lr_entries: invoice linkage columns (cross-fill from invoice), then the
    # full Transport Entry field set (company, routing, packages, charges, …)
    if "lr_entries" in insp.get_table_names():
        lcols = {c["name"] for c in insp.get_columns("lr_entries")}
        ladds = {
            "entry_source": "VARCHAR", "lr_entry_no": "VARCHAR", "lr_entry_date": "VARCHAR",
            "lr_mode": "VARCHAR", "boxes": "FLOAT",
            "agent": "VARCHAR", "agent_commission": "FLOAT",
            "auto_transfer_location": "VARCHAR", "purchase_manager": "VARCHAR",
            "stock_holding_days": "FLOAT", "additional_margin": "FLOAT",
            "freight_applicable": "BOOLEAN",
        }
        # Fields Essa never used — no consignment ever carried one, so they were
        # ten empty columns in every view. Dropped rather than left unmapped:
        # SQLite has supported DROP COLUMN since 3.35, and an unmapped column is
        # a trap for the next person reading the schema. If the drop fails (an
        # older SQLite, or the column is indexed) the column simply stays behind,
        # unmapped and unread — which is what the legacy `place` / `purchaser`
        # columns already do.
        ldrops = ["due_date", "pay_mode", "package_slip_no", "slip_date",
                  "actual_weight", "charged_weight", "from_city", "receiving_city",
                  "loading_charge", "loading_applicable", "cash_cheque",
                  "company", "bundle_rack", "section", "remark"]
        with engine.begin() as conn:
            if "invoice_document_id" not in lcols:
                conn.execute(text("ALTER TABLE lr_entries ADD COLUMN invoice_document_id INTEGER"))
            if "matched" not in lcols:
                conn.execute(text("ALTER TABLE lr_entries ADD COLUMN matched BOOLEAN DEFAULT 0"))
            if "mismatches" not in lcols:
                conn.execute(text("ALTER TABLE lr_entries ADD COLUMN mismatches JSON"))
            for name, typ in ladds.items():
                if name not in lcols:
                    conn.execute(text(f"ALTER TABLE lr_entries ADD COLUMN {name} {typ}"))
            # rows that predate the manual form all came in by import
            if "entry_source" not in lcols:
                conn.execute(text("UPDATE lr_entries SET entry_source = 'import'"))
        for name in ldrops:
            if name not in lcols:
                continue
            try:
                with engine.begin() as conn:      # its own transaction: one
                    conn.execute(text(           # unsupported drop must not roll
                        f"ALTER TABLE lr_entries DROP COLUMN {name}"))   # back the rest
            except Exception:
                pass
            # the register's name column is the RECEIVING person, not the
            # purchaser — carry old values over to the renamed column. The
            # legacy `purchaser` column is left in place (SQLite can't always
            # drop) but is no longer mapped or read.
            if "received_by" not in lcols:
                conn.execute(text("ALTER TABLE lr_entries ADD COLUMN received_by VARCHAR"))
                if "purchaser" in lcols:
                    conn.execute(text("UPDATE lr_entries SET received_by = purchaser"))
    # purchase_lines: category chosen at GRN time
    if "purchase_lines" in insp.get_table_names():
        plcols = {c["name"] for c in insp.get_columns("purchase_lines")}
        if "category" not in plcols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE purchase_lines ADD COLUMN category VARCHAR"))
    # purchase_line_splits: grew from size-only to the full attribute breakdown
    if "purchase_line_splits" in insp.get_table_names():
        scols = {c["name"] for c in insp.get_columns("purchase_line_splits")}
        sadds = {"color": "VARCHAR", "material": "VARCHAR", "pattern": "VARCHAR",
                 "fit": "VARCHAR", "product_type": "VARCHAR", "design_no": "VARCHAR",
                 "sale_discount_pct": "FLOAT", "category": "VARCHAR"}
        smissing = {k: v for k, v in sadds.items() if k not in scols}
        if smissing:
            with engine.begin() as conn:
                for name, typ in smissing.items():
                    conn.execute(text(
                        f"ALTER TABLE purchase_line_splits ADD COLUMN {name} {typ}"))
        if "margin_pct" in scols:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE purchase_line_splits DROP COLUMN margin_pct"))
            except Exception:
                pass
    # stock_outwards: the receiving (Stock Inward) end of a dispatch
    if "stock_outwards" in insp.get_table_names():
        ocols = {c["name"] for c in insp.get_columns("stock_outwards")}
        oadds = {"received_date": "VARCHAR", "received_at": "DATETIME"}
        omissing = {k: v for k, v in oadds.items() if k not in ocols}
        if omissing:
            with engine.begin() as conn:
                for name, typ in omissing.items():
                    conn.execute(text(
                        f"ALTER TABLE stock_outwards ADD COLUMN {name} {typ}"))
    # purchase_return_lines: which received row each returned line came from, so
    # the debit note can be re-valued from the GRN rather than from a stale rate —
    # plus `shortage_id`, which marks the lines that claim goods never delivered
    # and must therefore settle without moving stock
    if "purchase_return_lines" in insp.get_table_names():
        rcols = {c["name"] for c in insp.get_columns("purchase_return_lines")}
        radds = {"purchase_line_id": "INTEGER", "split_id": "INTEGER", "uom": "VARCHAR",
                 "shortage_id": "INTEGER"}
        rmissing = {k: v for k, v in radds.items() if k not in rcols}
        if rmissing:
            with engine.begin() as conn:
                for name, typ in rmissing.items():
                    conn.execute(text(
                        f"ALTER TABLE purchase_return_lines ADD COLUMN {name} {typ}"))


_migrate()

# load the product-category master (from the GRN Excel) on first run, and heal
# any LR row that was linked to an invoice before the cross-fill was symmetric
# (linked, but Inv No / Inv Date left blank)
try:
    from .database import SessionLocal
    from .services import masters as _masters
    from .services import lr_link as _lr_link
    _db = SessionLocal()
    _n = _masters.import_categories(_db)
    _masters.seed_options(_db)
    _lr_link.backfill_linked_rows(_db)
    _db.close()
except Exception:
    pass

app = FastAPI(title="Essa Document Intake", version="0.1.0",
              description="Trainable invoice-to-data extraction for Essa Garments")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

app.include_router(documents.router)
app.include_router(suppliers.router)
app.include_router(purchases.router)
app.include_router(inventory.router)
app.include_router(outward.router)
app.include_router(payments.router)
app.include_router(returns.router)
app.include_router(reports.router)
app.include_router(settings.router)
app.include_router(auth.router)
app.include_router(masters.router)
app.include_router(lr.router)
app.include_router(bundles.router)


@app.get("/api/status")
def status():
    return {
        "company": {"name": COMPANY_NAME, "gstin": COMPANY_GSTIN},
        "provider_preference": runtime.get("provider_preference"),
        "providers": provider_status(),
    }


# ---- phone web app (PWA) at /m — same origin, no build step, no Expo ----
MOBILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mobile")


@app.get("/m")
def mobile_root():
    return FileResponse(os.path.join(MOBILE_DIR, "index.html"))


@app.get("/m/manifest.webmanifest")
def mobile_manifest():
    return FileResponse(os.path.join(MOBILE_DIR, "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/m/{fname}")
def mobile_asset(fname: str):
    path = os.path.join(MOBILE_DIR, os.path.basename(fname))
    if not os.path.exists(path):
        return FileResponse(os.path.join(MOBILE_DIR, "index.html"))  # SPA-ish fallback
    return FileResponse(path)


# ---- serve the built frontend if present (single-origin deploy) ----
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "frontend", "dist")
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/")
    def spa_root():
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
