"""Stock dispatched from the warehouse arrives at the branch it was sent to.

    python backend/tools/pos_transfers_test.py

Posting a Stock Outward reduces the warehouse. Nothing used to pick the goods up
at the other end, so they left one system and arrived in none.

The property that matters most here is not that stock arrives — it is that it
arrives ONCE. The sync runs on every start, and a shop whose stock grows by the
whole delivery every time the till is restarted is a bug that gets believed for
weeks, because nobody watches a number that only goes up when they are not
looking. So it is run twice in this file, every time, and the second run must
change nothing.

After that: the ACCEPTED quantity and not the sent one, a sale coming off the
branch it was rung at, and a dispatch to somewhere this shop does not know being
left alone rather than guessed at.

Runs the shop and a stand-in warehouse on two throwaway SQLite files, and refuses
to start if it is not actually pointed at them.
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

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


if not SHOP.is_dir():
    print("no shop beside this project; nothing to check")
    sys.exit(0)

tmp = tempfile.mkdtemp(prefix="essa-transfers-")
SCRATCH = os.path.join(tmp, "shop.db").replace(os.sep, "/")
WAREHOUSE = os.path.join(tmp, "warehouse.db")
os.environ["DATABASE_URL"] = "sqlite:///" + SCRATCH
os.environ["ESSA_WAREHOUSE_DB"] = WAREHOUSE
for leak in ("SHOP_DB_SCHEMA", "ESSA_DATABASE_URL", "POSTGRES_URL",
             "POSTGRES_PRISMA_URL", "POSTGRES_URL_NON_POOLING"):
    os.environ.pop(leak, None)
sys.path.insert(0, str(SHOP))

# --- a stand-in warehouse: only the tables the shop reads -------------------
wh = sqlite3.connect(WAREHOUSE)
wh.executescript("""
CREATE TABLE products (
  id INTEGER PRIMARY KEY, sku TEXT, barcode TEXT, description TEXT, hsn TEXT,
  uom TEXT, mrp REAL, sale_price REAL, sale_discount_pct REAL, avg_cost REAL,
  stock_qty REAL, category TEXT, size TEXT, color TEXT, brand TEXT,
  material TEXT, pattern TEXT, fit TEXT, product_type TEXT, design_no TEXT,
  style TEXT, sleeve TEXT, active INTEGER DEFAULT 1);
CREATE TABLE stock_outwards (
  id INTEGER PRIMARY KEY, code TEXT, date TEXT, to_destination TEXT,
  status TEXT, received_date TEXT);
CREATE TABLE stock_outward_lines (
  id INTEGER PRIMARY KEY, outward_id INTEGER, product_id INTEGER,
  qty REAL, accepted_qty REAL);
CREATE TABLE master_options (id INTEGER PRIMARY KEY, kind TEXT, value TEXT);
""")
wh.execute("INSERT INTO products (id, sku, description, hsn, uom, mrp, sale_price,"
           " avg_cost, stock_qty, category, size) VALUES"
           " (41,'ESSA-00007','LADIES-LEHANGA','621143','PCS',2317,1853.6,1655,0,"
           "'LADIES-LEHANGA','FREE')")
wh.execute("INSERT INTO master_options (kind, value) VALUES"
           " ('auto_transfer_location','TAQUA SILKS, TIRUPUR'),"
           " ('auto_transfer_location','PROZONE(CBE)'),"
           " ('auto_transfer_location','NONE')")
wh.execute("INSERT INTO stock_outwards (id, code, date, to_destination, status,"
           " received_date) VALUES (1,'TF-001','2026-08-19','TAQUA SILKS, TIRUPUR',"
           "'received','2026-08-19')")
# sent 12, only 11 came off the lorry
wh.execute("INSERT INTO stock_outward_lines (id, outward_id, product_id, qty,"
           " accepted_qty) VALUES (1,1,41,12,11)")
# a dispatch to a place this shop has never heard of
wh.execute("INSERT INTO stock_outwards (id, code, date, to_destination, status)"
           " VALUES (2,'TF-002','2026-08-20','SOMEWHERE ELSE','posted')")
wh.execute("INSERT INTO stock_outward_lines (id, outward_id, product_id, qty,"
           " accepted_qty) VALUES (2,2,41,5,5)")
# and one still being packed
wh.execute("INSERT INTO stock_outwards (id, code, date, to_destination, status)"
           " VALUES (3,'TF-003','2026-08-21','TAQUA SILKS, TIRUPUR','draft')")
wh.execute("INSERT INTO stock_outward_lines (id, outward_id, product_id, qty,"
           " accepted_qty) VALUES (3,3,41,7,7)")
wh.commit()
wh.close()

import app as shop                                               # noqa: E402
from app import db, places, transfers                            # noqa: E402
from app.models import (Location, LocationStock, Product,        # noqa: E402
                        TransferReceipt, User)

flask_app = shop.create_app()
used = flask_app.config["SQLALCHEMY_DATABASE_URI"]
if SCRATCH not in used.replace("\\", "/"):
    print("REFUSING TO RUN — the shop opened this instead of the scratch file:")
    print("   %s" % used)
    sys.exit(2)
ctx = flask_app.app_context()
ctx.push()
db.create_all()

# ---------------------------------------------------------------------------
head("the branches come from the warehouse's own list")
added, retired = places.sync_locations()
eq("both real places, and not NONE", added, 2)
eq("named as the warehouse names them",
   sorted(l.name for l in Location.query.all()),
   ["PROZONE(CBE)", "TAQUA SILKS, TIRUPUR"])

head("a dispatch that arrived becomes that branch's stock")
lines, pieces = transfers.sync_transfers()
eq("one line taken in", lines, 1)
eq("eleven pieces — what was ACCEPTED, not the twelve sent", pieces, 11.0)
tirupur = Location.query.filter_by(name="TAQUA SILKS, TIRUPUR").first()
prod = Product.query.filter_by(warehouse_id=41).first()
eq("the item came in with it", prod is not None, True)
eq("and it is at Tirupur",
   LocationStock.query.filter_by(location_id=tirupur.id,
                                 product_id=prod.id).first().qty, 11.0)
eq("the shop's total agrees", prod.stock_qty, 11.0)
# SOMEWHERE ELSE is not on the warehouse's branch master, so it is not a branch
# — see places._warehouse_locations. A dispatch there could be a customer
# delivery, and taking it into shop stock would invent goods on a shelf.
eq("a dispatch to a place that is not a known branch was left alone",
   TransferReceipt.query.count(), 1)
eq("and a note still being packed was not taken in",
   [r.code for r in TransferReceipt.query.all()], ["TF-001"])

head("…and running it again changes nothing at all")
again = transfers.sync_transfers()
eq("no lines the second time", again, (0, 0))
eq("stock is where it was", prod.stock_qty, 11.0)
eq("and so is the branch's", LocationStock.query.filter_by(
    location_id=tirupur.id, product_id=prod.id).first().qty, 11.0)
for _ in range(3):
    transfers.sync_transfers()
eq("…however many times it runs", db.session.get(Product, prod.id).stock_qty, 11.0)

head("the far end accepting more of it later is taken in too")
wh = sqlite3.connect(WAREHOUSE)
wh.execute("INSERT INTO stock_outwards (id, code, date, to_destination, status,"
           " received_date) VALUES (4,'TF-004','2026-08-22','PROZONE(CBE)',"
           "'received','2026-08-22')")
wh.execute("INSERT INTO stock_outward_lines (id, outward_id, product_id, qty,"
           " accepted_qty) VALUES (4,4,41,4,4)")
wh.commit()
wh.close()
lines, pieces = transfers.sync_transfers()
eq("the new dispatch only", (lines, pieces), (1, 4.0))
prozone = Location.query.filter_by(name="PROZONE(CBE)").first()
eq("it landed at Prozone", LocationStock.query.filter_by(
    location_id=prozone.id, product_id=prod.id).first().qty, 4.0)
eq("Tirupur is untouched", LocationStock.query.filter_by(
    location_id=tirupur.id, product_id=prod.id).first().qty, 11.0)
eq("and the shop holds both", db.session.get(Product, prod.id).stock_qty, 15.0)
eq("the split adds up to the total",
   sum(r.qty for r in LocationStock.query.filter_by(product_id=prod.id).all()),
   db.session.get(Product, prod.id).stock_qty)

head("a sale comes off the branch it was rung at")
staff = User(username="till2", full_name="Till Two", role="cashier", active=True)
staff.set_password("x")
db.session.add(staff)
prod.selling_price = 1853.6
prod.gst_rate = 5.0
db.session.commit()

client = flask_app.test_client()
client.post("/login", data={"username": "till2", "password": "x"},
            follow_redirects=True)
client.post("/pos/place", json={"company_id": places.default_company().id,
                                "location_id": tirupur.id})
r = client.post("/pos/checkout", json={
    "items": [{"product_id": prod.id, "quantity": 2, "unit_price": 1853.6}],
    "staff_id": staff.id, "payment_method": "cash"})
eq("the sale goes through", r.status_code, 200)
eq("Tirupur is two lighter", LocationStock.query.filter_by(
    location_id=tirupur.id, product_id=prod.id).first().qty, 9.0)
eq("Prozone is untouched — its pieces are across town",
   LocationStock.query.filter_by(location_id=prozone.id,
                                 product_id=prod.id).first().qty, 4.0)
eq("and the shop's total is two lighter", db.session.get(Product, prod.id).stock_qty, 13.0)
eq("which is still the sum of its branches",
   sum(r.qty for r in LocationStock.query.filter_by(product_id=prod.id).all()),
   db.session.get(Product, prod.id).stock_qty)
eq("and the branch is on the ledger row",
   "TAQUA SILKS, TIRUPUR" in transfers.stock_by_location(prod.id)[0][0], True)

ctx.pop()
print("\n%d FAILED" % len(bad) if bad else "\nall passing")
sys.exit(1 if bad else 0)
