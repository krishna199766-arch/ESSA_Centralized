import os
import re
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .database import Base, engine
from . import models  # noqa: F401  (register tables)
from .routers import (documents, suppliers, purchases, inventory, outward,
                      payments, returns, reports, settings, auth, masters, lr,
                      bundles, dashboard, master_data, labels, dead_stock,
                      notifications, voice, users)
from .extraction.engine import provider_status
from .security import auth_middleware
from .config import COMPANY_NAME, COMPANY_GSTIN
from . import runtime
from .services import storage as storage_svc

# Why the schema build is allowed to fail here
# ---------------------------------------------
# On the warehouse PC this is a SQLite file that is always reachable, and a
# failure means something is badly wrong and crashing is the honest response.
# Deployed, the database is across a network and behind a connection string
# somebody typed. A raise at import time there does not produce a message
# anybody sees — the whole module fails to import, and the platform answers
# every request with an opaque FUNCTION_INVOCATION_FAILED. The reason is in a
# log the person debugging has to know to go and find.
#
# So the failure is recorded instead of raised, and /api/status reports it. The
# deployment is just as unusable either way; the difference is that this one
# says what is wrong with it.
STARTUP_ERROR = None


def _scrub(text: str) -> str:
    """Connection errors quote the URL back, and the URL carries the password.
    /api/status is reachable by anyone, so the credentials come out first."""
    return re.sub(r"://([^:/@\s]+):([^@/\s]+)@", r"://\1:***@", str(text))


def _database_status(url: str) -> dict:
    """What /api/status says about the database.

    `ok` is not simply "did it start". A serverless deployment that falls back
    to SQLite starts perfectly, answers every request, and writes to /tmp —
    which is wiped between invocations. Every GRN posted against it is lost, and
    nothing anywhere says so. That is a worse failure than not starting, because
    the warehouse would find out days later, so it is reported as NOT ok with
    the reason spelled out.
    """
    dialect = url.split("://")[0] if "://" in url else "?"
    host = _scrub(url).split("@")[-1].split("/")[0] if "@" in url else "local file"
    ephemeral = dialect.startswith("sqlite") and os.environ.get("VERCEL")
    warning = None
    if ephemeral:
        warning = ("Running on a temporary SQLite file, because no usable Postgres "
                   "connection string was found. This works, and then LOSES "
                   "EVERYTHING when the instance recycles. Set ESSA_DATABASE_URL "
                   "to a real Postgres URL — a value still containing < or > is a "
                   "template and is ignored.")
    from .config import database_url_report
    return {
        "ok": STARTUP_ERROR is None and not ephemeral,
        # The driver and host, never the password — see _scrub.
        "dialect": dialect,
        "host": host,
        "persistent": not ephemeral,
        "error": STARTUP_ERROR,
        "warning": warning,
        # Only shown when there is a problem: when it is working, the list of
        # variables that were not needed is noise on an endpoint the app polls.
        "checked": None if not ephemeral else database_url_report(),
    }


def _record_startup_failure(exc: BaseException, during: str) -> None:
    global STARTUP_ERROR
    if STARTUP_ERROR is None:
        STARTUP_ERROR = f"{during}: {type(exc).__name__}: {_scrub(exc)}"


try:
    Base.metadata.create_all(bind=engine)
except Exception as exc:                       # noqa: BLE001 — reported, not raised
    _record_startup_failure(exc, "creating the schema")


def _migrate():
    """Add columns introduced after a DB was first created (SQLite), so existing
    installs pick up new Product attributes without losing data.

    SQLite only, and that is not a limitation — it is the whole point. Every
    statement below patches a database that already holds Essa's data and was
    created before the column existed. There is exactly one such database and it
    is the SQLite file on the warehouse PC. A Postgres deployment is created by
    `create_all` above, from today's models, with every one of these columns
    already present — so running these against it would at best be a long series
    of no-ops and at worst fail on syntax SQLite accepts and Postgres does not
    (`ALTER TABLE … DROP COLUMN` inside a try/except being the clearest case).
    """
    from sqlalchemy import inspect, text
    if engine.dialect.name != "sqlite":
        return
    insp = inspect(engine)
    if "products" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("products")}
    adds = {"color": "VARCHAR", "size": "VARCHAR", "pattern": "VARCHAR", "fit": "VARCHAR",
            "product_type": "VARCHAR", "material": "VARCHAR", "design_no": "VARCHAR",
            "sale_price": "FLOAT", "sale_discount_pct": "FLOAT", "detailed": "BOOLEAN",
            "detailed_at": "DATETIME", "detailed_by": "VARCHAR",
            "category": "VARCHAR", "category_section": "VARCHAR",
            # the unit this product is counted in, and how many individual items
            # are in one of it (piece = 1, pair = 2). Left NULL on existing rows
            # on purpose: a product received before unit types existed keeps
            # counting the way it did until a receipt pins it — see
            # services/inventory.line_unit_type.
            "unit_type": "VARCHAR", "pieces_per_unit": "FLOAT",
            # the rest of the stock master's attribute set
            "brand": "VARCHAR", "style": "VARCHAR", "sleeve": "VARCHAR"}
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
        # every page of a multi-page invoice; NULL on rows that predate it, which
        # the readers treat as "just stored_path"
        if "pages" not in dcols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE documents ADD COLUMN pages JSON"))
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
            # a page read in Tamil, and what it actually said
            "source_language": "VARCHAR", "original_values": "JSON",
            # the transporter's G. TOTAL and the charge lines under it — freight
            # alone was never the whole bill
            "freight_total": "FLOAT", "freight_charges": "JSON",
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
    # purchase_lines: category and unit type chosen at GRN time
    if "purchase_lines" in insp.get_table_names():
        plcols = {c["name"] for c in insp.get_columns("purchase_lines")}
        for name, typ in (("category", "VARCHAR"), ("unit_type", "VARCHAR"),
                          # retail pricing carried from the invoice review
                          ("mrp", "FLOAT"), ("sale_price", "FLOAT"),
                          ("sale_discount_pct", "FLOAT"),
                          # the supplier's size cell, often the whole size run
                          ("size", "VARCHAR")):
            if name not in plcols:
                with engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE purchase_lines ADD COLUMN {name} {typ}"))
    # purchase_line_splits: grew from size-only to the full attribute breakdown
    if "purchase_line_splits" in insp.get_table_names():
        scols = {c["name"] for c in insp.get_columns("purchase_line_splits")}
        sadds = {"color": "VARCHAR", "material": "VARCHAR", "pattern": "VARCHAR",
                 "fit": "VARCHAR", "product_type": "VARCHAR", "design_no": "VARCHAR",
                 "sale_discount_pct": "FLOAT", "category": "VARCHAR",
                 "unit_type": "VARCHAR",
                 "brand": "VARCHAR", "style": "VARCHAR", "sleeve": "VARCHAR"}
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


try:
    _migrate()
except Exception as exc:                       # noqa: BLE001 — reported, not raised
    _record_startup_failure(exc, "migrating the schema")

# ---- accounts ----
# Held apart from the convenience seeds below. Those are conveniences: an
# install that starts without its category list is merely inconvenient. An
# install that starts with an empty users table cannot be signed into at all,
# and presenting that as a login screen which rejects every correct password is
# the worst outcome available.
#
# So it is still not swallowed — it is reported, and /api/status names it. The
# reason it is no longer raised is the one at the top of this file: a raise here
# is invisible on a serverless deployment, and "cannot sign in, no reason given"
# is exactly what it produces.
from .database import SessionLocal as _Session
from .services import users as _users
try:
    _udb = _Session()
    try:
        _users.seed(_udb)
    finally:
        _udb.close()
except Exception as exc:                       # noqa: BLE001 — reported, not raised
    _record_startup_failure(exc, "seeding the accounts")

# load the product-category master (from the GRN Excel) on first run, and heal
# any LR row that was linked to an invoice before the cross-fill was symmetric
# (linked, but Inv No / Inv Date left blank)
try:
    from .database import SessionLocal
    from .services import masters as _masters
    from .services import lr_link as _lr_link
    from .services import unit_types as _unit_types
    _db = SessionLocal()
    _n = _masters.import_categories(_db)
    _masters.seed_options(_db)
    # dozens-to-pieces needs a unit master to convert against, and the warehouse
    # should not have to build one before the first receipt
    _unit_types.seed(_db)
    # a warehouse with no label template cannot print a sticker, and designing
    # one before the first receipt is a wall in front of the first garment
    from .services import label_designer as _label_designer
    _label_designer.ensure_default(_db)
    _lr_link.backfill_linked_rows(_db)
    _db.close()
except Exception:
    pass

app = FastAPI(title="Essa Document Intake", version="0.1.0",
              description="Trainable invoice-to-data extraction for Essa Garments")

# ---- who may call what ----
# One middleware rather than a dependency on each route — see security.POLICY
# for the table it reads, and why it is a table.
#
# Order is load-bearing and reads backwards: Starlette runs the LAST-added
# middleware first, so CORS is added after this one in order to sit outside it.
# That is what makes a rejection usable — a 401 raised inside CORS still comes
# back with the headers the browser needs to hand the body to the app, and a
# preflight OPTIONS is answered by CORS before it ever reaches an auth check it
# carries no token to pass.
app.middleware("http")(auth_middleware)


@app.exception_handler(storage_svc.StorageError)
def _storage_error(request, exc):
    """A file that could not be stored or read back is not an unexplained
    fault, and answering with a bare 500 makes it look like one — "Extract
    failed: 500" is what the screen shows, and it names neither the step nor
    the reason. 502 with the message says which of the two halves failed, and
    the message came from the storage service itself."""
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": f"File storage: {exc}"}, status_code=502)

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
app.include_router(master_data.router)
app.include_router(lr.router)
app.include_router(bundles.router)
app.include_router(dashboard.router)
app.include_router(labels.router)
app.include_router(dead_stock.router)
app.include_router(notifications.router)
app.include_router(voice.router)
app.include_router(users.router)


# ---- the retail shop (POS) at /pos ----
# A separate Flask app, mounted here so it shares this origin — see pos_mount.
# It is optional: a missing folder or an environment without Flask leaves the
# POS screen saying what to install, which is more use than a 500 behind a
# button someone just clicked.
_pos_error = None
try:
    from .pos_mount import load_pos_app
    _pos_app = load_pos_app()
except Exception as exc:                              # noqa: BLE001 — reported, not raised
    _pos_app, _pos_error = None, f"{type(exc).__name__}: {exc}"

if _pos_app is not None:
    import warnings
    with warnings.catch_warnings():                   # the module warns on import
        warnings.simplefilter("ignore", DeprecationWarning)
        from starlette.middleware.wsgi import WSGIMiddleware
    app.mount("/pos", WSGIMiddleware(_pos_app))
else:
    from fastapi.responses import HTMLResponse

    @app.get("/pos", response_class=HTMLResponse)
    @app.get("/pos/{rest:path}", response_class=HTMLResponse)
    def pos_unavailable(rest: str = ""):
        return HTMLResponse(
            "<body style=\"font:14px/1.6 system-ui;padding:32px;color:#33261F\">"
            "<h2 style='margin:0 0 8px'>POS is not loaded</h2>"
            f"<p>{_pos_error}</p>"
            "<p>The shop lives in the <b>Textile Retail Shop</b> folder beside "
            "this project and needs its Python packages in the backend "
            "environment:</p>"
            "<pre style='background:#F2EEEB;padding:12px;border-radius:6px'>"
            "cd backend\n.venv\\Scripts\\activate\npip install -r requirements.txt</pre>"
            "<p>Then restart the server.</p></body>", status_code=503)


@app.get("/api/status")
def status():
    """Also the deployment's own diagnostic. `database` is the part worth
    reading first: if the server came up without one, every other answer here
    is beside the point, and this is the only place that says so in words."""
    from .database import DB_URL
    return {
        "company": {"name": COMPANY_NAME, "gstin": COMPANY_GSTIN},
        "provider_preference": runtime.get("provider_preference"),
        "providers": provider_status(),
        "pos": {"available": _pos_app is not None, "error": _pos_error},
        "database": _database_status(DB_URL),
        "storage": storage_svc.backend_name(),
        # Enough to tell which build is actually serving. Three fixes in a row
        # here were tested against a deployment that had not finished replacing
        # the previous one, and an unchanged error looks identical to a fix that
        # did not work — so the running code states its own version.
        "storage_detail": {
            "backend": storage_svc.backend_name(),
            "blob_api_version": storage_svc.BLOB_API_VERSION,
            "blob_access": storage_svc.BLOB_ACCESS,
        },
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
        # Everything under /assets is content-hashed by the build, so a stale copy
        # is impossible — a rebuild changes the filename. index.html is the one
        # file with a fixed name, and it is what points at those filenames. Cached
        # without a directive it goes on naming the previous build's bundle, and a
        # rebuild silently does nothing until the browser decides to look again.
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"),
                            headers={"Cache-Control": "no-cache"})
