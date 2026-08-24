"""The shop's tables must not collide with the warehouse's on one database.

    python backend/tools/pos_mount_test.py

Four table names are the same in both codebases — categories, products,
stock_movements and users. Two SQLite files kept them apart for free. One
Postgres does not, and the failure is quiet and confusing: whichever application
creates a name first wins it, and the other reads a table with its own name and
the wrong columns. What the deployment actually showed was

    column categories.description does not exist

on a table that plainly did exist. It was the warehouse's.

The separation is a Postgres schema. The part worth testing is not that a schema
is created — it is HOW the shop is told about it, because the first attempt put
it in the connection SESSION (`options=-csearch_path=shop`), which a
transaction-mode pooler drops on the floor. Naming it on the metadata puts it in
the STATEMENT, and that is what this checks: the SQL the shop would send says
`shop.categories`, whatever the connection thinks.

No database is opened. The check is on the SQL, which is the whole point.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHOP = ROOT / "Textile Retail Shop"
sys.path.insert(0, str(ROOT))

bad = []


def eq(what, got, want):
    if got != want:
        bad.append(what)
        print("  FAIL  %s\n        got  %r\n        want %r" % (what, got, want))
    else:
        print("  ok    %s" % what)


def head(t):
    print("\n%s" % t)


# ---------------------------------------------------------------------------
head("the four names that collide are still four names that collide")
# If this ever stops being true the whole mechanism can go — so it is asserted
# rather than assumed, and it will say so on the day somebody renames one.
import re                                                        # noqa: E402

wh = (ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
shop_models = (SHOP / "app" / "models.py").read_text(encoding="utf-8")
names = lambda src: set(re.findall(r'__tablename__\s*=\s*"([^"]+)"', src))
shared = names(wh) & names(shop_models)
eq("the same four", sorted(shared),
   ["categories", "products", "stock_movements", "users"])

head("and the shop puts its schema in the STATEMENT, not the session")
if not SHOP.is_dir():
    print("  ..    no shop beside this project; nothing to check")
else:
    # imported the way pos_mount imports it: shop first on the path, and with the
    # environment it would be given on a Postgres deployment
    os.environ["SHOP_DB_SCHEMA"] = "shop"
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, str(SHOP))
    ours = {k: v for k, v in sys.modules.items()
            if k == "app" or k.startswith("app.") or k == "config"}
    for k in ours:
        del sys.modules[k]
    try:
        import app as shop_pkg                                   # noqa: E402
        from app import models as shop_tables                    # noqa: E402

        eq("the metadata carries it", shop_pkg.db.metadata.schema, "shop")
        cats = shop_tables.Category.__table__
        eq("and so does every table", cats.schema, "shop")

        # the actual SQL — this is the thing the pooler cannot take away
        sql = " ".join(str(cats.select()).split())
        eq("so the statement names it", "FROM shop.categories" in sql, True)
        eq("and every column with it", "shop.categories.description" in sql, True)

        # and the shop still finds the database the warehouse chose
        from config import Config                                 # noqa: E402
        eq("with no Postgres anywhere, it is still its own SQLite file",
           Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite:///"), True)
    finally:
        for k in [k for k in list(sys.modules)
                  if k == "app" or k.startswith("app.") or k == "config"]:
            del sys.modules[k]
        sys.modules.update(ours)
        try:
            sys.path.remove(str(SHOP))
        except ValueError:
            pass
        os.environ.pop("SHOP_DB_SCHEMA", None)

head("standalone, it is unchanged — SQLite never had schemas")
sys.path.insert(0, str(SHOP))
ours = {k: v for k, v in sys.modules.items()
        if k == "app" or k.startswith("app.") or k == "config"}
for k in ours:
    del sys.modules[k]
try:
    import app as plain_pkg                                       # noqa: E402
    from app import models as plain_tables                        # noqa: E402
    eq("no schema on the metadata", plain_pkg.db.metadata.schema, None)
    eq("nor on a table", plain_tables.Category.__table__.schema, None)
    plain_sql = " ".join(str(plain_tables.Category.__table__.select()).split())
    eq("and the SQL is the bare name it always was",
       "FROM categories" in plain_sql, True)
    eq("with no schema anywhere in it", "shop." in plain_sql, False)
finally:
    for k in [k for k in list(sys.modules)
              if k == "app" or k.startswith("app.") or k == "config"]:
        del sys.modules[k]
    sys.modules.update(ours)
    try:
        sys.path.remove(str(SHOP))
    except ValueError:
        pass

head("the shop looks for the database under every name the warehouse does")
cfg = (SHOP / "config.py").read_text(encoding="utf-8")
for var in ("ESSA_DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL",
            "DATABASE_URL", "POSTGRES_URL_NON_POOLING"):
    eq("  %s" % var, var in cfg, True)

head("and the mount no longer leans on search_path")
mount = (ROOT / "backend" / "app" / "pos_mount.py").read_text(encoding="utf-8")
eq("no options=-csearch_path in the URL it hands over",
   "csearch_path" in mount.split('"""')[-1], False)
eq("it sets SHOP_DB_SCHEMA instead", 'os.environ["SHOP_DB_SCHEMA"]' in mount, True)
eq("and it still creates the schema", "CREATE SCHEMA IF NOT EXISTS" in mount, True)

head("and the deployment routes every POS path to the shop, not to the SPA")
# The frame's own links end in a slash — /pos/inventory/, /pos/customers/ — and
# `/pos/:path*` does NOT match a path ending in one: the trailing slash leaves an
# empty last segment, the rule falls through to the catch-all, and the SPA comes
# back instead of the shop. Inside the POS frame that renders the whole warehouse
# again, header and all, which renders another frame, which renders another. The
# page arrives as stacked copies of itself, and nothing about it says why.
import json                                                      # noqa: E402

vercel = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))


def to_function(path):
    """Whether `path` reaches the Python function. First rule wins, as Vercel does.

    Only two source shapes are used in this file — a literal, and a literal
    followed by (.*) — so matching them needs no path-to-regexp.
    """
    for rule in vercel["rewrites"]:
        src = rule["source"]
        hit = (path == src if not src.endswith("(.*)")
               else path.startswith(src[:-4]))
        if hit:
            return rule["destination"] == "/api/index"
    return False


import re as _re                                                 # noqa: E402
app_jsx = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
pos_paths = sorted(set(_re.findall(r"key: 'pos:[^']*'[^}]*?path: '([^']*)'", app_jsx)))
eq("the menu's own POS links were found", len(pos_paths) > 3, True)
# Not all of them end in a slash — /pos/invoices does not — and that is what made
# this so confusing to look at: some POS screens worked and some came back as the
# warehouse. One is enough for the trap to be real.
eq("and some of them end in a slash, which is the trap",
   sum(1 for p in pos_paths if p.endswith("/")) > 1, True)
for rel in pos_paths:
    eq("  /pos%s reaches the shop" % rel, to_function("/pos" + rel), True)
for extra in ("/pos", "/pos/", "/m", "/m/", "/api/status", "/api/outward/1/receive"):
    eq("  %s too" % extra, to_function(extra), True)
eq("  …and the app's own page still comes from the bundle",
   to_function("/index.html"), False)
eq("  …as does the bundle itself", to_function("/assets/index-abc.js"), False)

print("\n%d FAILED" % len(bad) if bad else "\nall passing")
sys.exit(1 if bad else 0)
