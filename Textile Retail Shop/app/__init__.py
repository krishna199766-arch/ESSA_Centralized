import os

import datetime as dt
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy import MetaData
from config import Config

# Which Postgres schema the shop's tables live in.
#
# Standalone — the shop on its own SQLite file — this is unset and nothing
# changes: SQLite has no schemas and never needed one, because a file each kept
# the two applications apart.
#
# Mounted inside the warehouse on ONE Postgres, it is set to "shop", and it has
# to be, because four table names are the same in both codebases: categories,
# products, stock_movements and users. Whichever application creates a name first
# wins it, and the other then queries a table with its own name and the wrong
# columns — "column categories.description does not exist", on a table that
# plainly does exist.
#
# Naming the schema on the METADATA is what makes that hold. It puts the schema
# in the SQL — `SELECT ... FROM shop.categories` — rather than in the session,
# and session state is exactly what a transaction-mode connection pooler does not
# keep. The earlier fix set search_path through the connection URL's `options`
# parameter, which works on a direct connection and is silently dropped by
# PgBouncer, which is what the deployment runs on. The schema in the statement
# needs nothing from the connection.
SHOP_DB_SCHEMA = os.environ.get("SHOP_DB_SCHEMA") or None

db = SQLAlchemy(metadata=MetaData(schema=SHOP_DB_SCHEMA))
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Blueprints
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.inventory import inventory_bp
    from app.routes.pos import pos_bp
    from app.routes.customers import customers_bp
    from app.routes.staff import staff_bp
    from app.routes.reports import reports_bp
    from app.routes.returns import returns_bp
    from app.routes.alterations import alterations_bp
    from app.routes.checker import checker_bp
    from app.routes.floor import floor_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(inventory_bp, url_prefix="/inventory")
    app.register_blueprint(pos_bp, url_prefix="/pos")
    app.register_blueprint(customers_bp, url_prefix="/customers")
    app.register_blueprint(staff_bp, url_prefix="/staff")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(returns_bp, url_prefix="/returns")
    app.register_blueprint(alterations_bp, url_prefix="/alterations")
    app.register_blueprint(checker_bp, url_prefix="/stock-check")
    app.register_blueprint(floor_bp, url_prefix="/floor")

    # A product detailed and posted from the warehouse's mobile app should be in
    # the shop by the time anyone looks, without a restart or a button. Checking
    # costs one stat() of the warehouse database — see warehouse_items.sync_if_stale
    # — so requests where nothing has changed pay almost nothing for it.
    # Bound HERE, inside create_app, and never inside the handler. This package is
    # loaded as `app`, and when the shop is served inside the Essa backend that
    # name belongs to the backend by the time a request arrives — an `import app…`
    # in the handler would reach for the wrong package. create_app runs while the
    # name is still ours (see backend/app/pos_mount.py), so the module objects
    # captured now stay correct for the life of the process.
    from app import dbpatch, modules, warehouse_items, places as places_mod

    @app.before_request
    def refresh_from_warehouse():
        if request.endpoint == "static":
            return
        try:
            # Schema first, and unconditionally: a database from an older build is
            # missing columns the models declare, and the sync below is skipped
            # entirely when there is no warehouse to read — so patching cannot be
            # left as a side effect of it. Costs nothing after the first call.
            dbpatch.apply_all()
            warehouse_items.sync_if_stale()
        except Exception:
            # The till keeps trading whatever the warehouse is doing: a failed
            # refresh must not take a sale down with it. Logged rather than
            # passed over, because the first version of this swallowed an
            # ImportError and looked exactly like a sync that had nothing to do.
            db.session.rollback()
            app.logger.warning("warehouse refresh failed", exc_info=True)

    @app.before_request
    def _capture_warehouse_scope():
        """Remember which warehouse this till was opened from.

        The warehouse UI mounts the shop in a frame and puts `?wh=<id>` on the
        first URL. A frame cannot send a header, and only that first request
        carries the parameter — the till then navigates between its own screens
        — so it is copied into the session, where the rest of the shop reads it
        (see places.current_scope).

        `?wh=` with nothing after it CLEARS the scope, which is how the shop
        behaves when it is opened directly rather than from inside a warehouse.

        `places_mod` is the module captured in create_app, NOT imported here. By
        the time a request runs, `app` in sys.modules is the WAREHOUSE's package
        again — the swap in backend/app/pos_mount is over — so `from app import
        places` at this point reaches for the wrong package and 500s. Every late
        import in this shop has to be hoisted for that reason; see the note above
        the capture in create_app.
        """
        from flask import session
        if "wh" not in request.args:
            return
        raw = (request.args.get("wh") or "").strip()
        session[places_mod.SCOPE_KEY] = int(raw) if raw.isdigit() else None

    # Context processor for shop info
    @app.context_processor
    def inject_shop():
        # The module list, for the dashboard's cards and the header's menu alike,
        # filtered to what this person may open — a card leading to a 403 is
        # worse than no card. `CURRENT_MODULE` is what lets the closed menu say
        # which screen you are on.
        #
        # `modules` is the one bound above, in create_app: importing it here would
        # run at request time, when the name `app` belongs to the backend.
        from flask_login import current_user
        return {
            "SHOP_MODULES": modules.visible(current_user),
            "CURRENT_MODULE": modules.current(request.endpoint),
            "SHOP_NAME": app.config["SHOP_NAME"],
            "SHOP_ADDRESS": app.config["SHOP_ADDRESS"],
            "SHOP_PHONE": app.config["SHOP_PHONE"],
            "SHOP_GSTIN": app.config["SHOP_GSTIN"],
        }

    # Jinja filter
    @app.template_filter("inr")
    def inr(value):
        try:
            return f"₹{float(value):,.2f}"
        except (TypeError, ValueError):
            return "₹0.00"

    # One date format on screen: DD-MM-YYYY, the way this shop writes it on every
    # bill and register page. SQLite hands `date(invoice_date)` back as ISO text
    # and a `date` object prints ISO too, so anything that reaches a template
    # without a strftime of its own goes through here: {{ day | dmy }}
    @app.template_filter("dmy")
    def dmy(value):
        if value in (None, ""):
            return ""
        if isinstance(value, dt.datetime):
            value = value.date()
        if isinstance(value, dt.date):
            return value.strftime("%d-%m-%Y")
        try:
            return dt.date.fromisoformat(str(value)[:10]).strftime("%d-%m-%Y")
        except ValueError:
            # not a date this can read — better shown as it came than blanked
            return value

    # Barcode renderer available in templates as: {{ product.barcode | barcode }}
    from markupsafe import Markup
    from app.utils import barcode_svg, qr_svg

    @app.template_filter("barcode")
    def barcode(value, **kwargs):
        return Markup(barcode_svg(value, **kwargs))

    # The warehouse's QR for an item: {{ product.warehouse_qr | qr }}
    @app.template_filter("qr")
    def qr(value, **kwargs):
        return Markup(qr_svg(value, **kwargs))

    return app
