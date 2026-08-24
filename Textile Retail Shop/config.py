import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production-please")
    # Where the shop's data lives.
    #
    # An explicit DATABASE_URL wins, whatever its scheme — that is somebody
    # saying exactly which database to use, and a SQLite path is a perfectly
    # ordinary thing to say (a test, a second till, a copy to poke at). An
    # earlier version of this only accepted Postgres URLs here and silently fell
    # back to the shop's own file for anything else, which meant a run that
    # thought it was pointed at a scratch database was quietly writing to the
    # real one.
    #
    # Failing that, the deployment's own spellings, in the order the warehouse
    # reads them (backend/app/config.DB_URL_CANDIDATES). Reading only
    # DATABASE_URL meant a host that spells it POSTGRES_URL — which is what
    # Vercel and Supabase hand out — sent the warehouse to Postgres and the shop
    # to a SQLite file nothing else could see, with no error to say so.
    #
    # `postgres://` is rewritten because SQLAlchemy has refused that spelling
    # since 1.4 while most dashboards still print it.
    SQLALCHEMY_DATABASE_URI = next(
        (v.replace("postgres://", "postgresql://", 1)
         for v in (os.environ.get(k, "").strip() for k in (
             "DATABASE_URL", "ESSA_DATABASE_URL", "POSTGRES_URL",
             "POSTGRES_PRISMA_URL", "POSTGRES_URL_NON_POOLING"))
         if v),
        f"sqlite:///{BASE_DIR / 'textile_shop.db'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Re-read templates from disk on every request so edits appear on a refresh
    # (otherwise Flask caches them until the server restarts).
    TEMPLATES_AUTO_RELOAD = True

    # Business / GST defaults
    SHOP_NAME = os.environ.get("SHOP_NAME", "Taqua Silks")
    SHOP_ADDRESS = os.environ.get("SHOP_ADDRESS", "Municipal Office Rd, Noyyal, Tiruppur, Tamil Nadu 641604")
    SHOP_PHONE = os.environ.get("SHOP_PHONE", "+91 98765 43210")
    SHOP_GSTIN = os.environ.get("SHOP_GSTIN", "33ABCDE1234F1Z5")
    SHOP_STATE_CODE = os.environ.get("SHOP_STATE_CODE", "33")  # Tamil Nadu

    # Loyalty: 1 point per rupee spent above LOYALTY_MIN_BILL, 1 point = LOYALTY_POINT_VALUE INR
    LOYALTY_EARN_RATE = float(os.environ.get("LOYALTY_EARN_RATE", "0.01"))  # 1% of bill
    LOYALTY_POINT_VALUE = float(os.environ.get("LOYALTY_POINT_VALUE", "1.0"))
    LOYALTY_MIN_BILL = float(os.environ.get("LOYALTY_MIN_BILL", "500"))

    LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", "5"))
