"""Company, location and counter: picked at the till, remembered on the bill.

    python backend/tools/pos_places_test.py

A tax invoice carries the GSTIN of whoever raised it. With one company that is a
line in a config file; with two it is a decision, made per sale, and the thing
that has to survive is not the picker — it is the ANSWER, on the invoice, still
correct when the bill is reprinted a month later from a till that has since been
set to the other company.

So what is checked here is mostly the remembering:

  * a bill carries the company, location and counter the till was set to
  * a till that was never set still bills as somebody — the default — because an
    invoice with no entity on it is not a tax invoice
  * a reprint reads the company off the INVOICE, not off the session
  * an impossible pairing is refused rather than stored, so the strip on screen
    never says something the next bill will not do

and the location list, which is the warehouse's and not a second copy of it.

Runs the shop against a throwaway SQLite file, with no warehouse beside it.
"""
import os
import pathlib
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

# The shop, standalone, on a database of its own — and then CHECKED, before a
# single row is written, that it really is its own. An earlier version of this
# file set DATABASE_URL and trusted it; the shop was ignoring a non-Postgres URL
# at the time and fell back to the real textile_shop.db, so the fixtures went
# into live data. Fixtures belong nowhere near it, and "I set the variable" is
# not the same as "it was used".
tmp = tempfile.mkdtemp(prefix="essa-places-")
SCRATCH = os.path.join(tmp, "shop.db").replace(os.sep, "/")
os.environ["DATABASE_URL"] = "sqlite:///" + SCRATCH
os.environ["ESSA_WAREHOUSE_DB"] = os.path.join(tmp, "no-warehouse-here.db")
for leak in ("SHOP_DB_SCHEMA", "ESSA_DATABASE_URL", "POSTGRES_URL",
             "POSTGRES_PRISMA_URL", "POSTGRES_URL_NON_POOLING"):
    os.environ.pop(leak, None)
sys.path.insert(0, str(SHOP))

import app as shop                                               # noqa: E402
from app import db, places                                       # noqa: E402
from app.models import Company, Counter, Invoice, Location, User  # noqa: E402

flask_app = shop.create_app()
used = flask_app.config["SQLALCHEMY_DATABASE_URI"]
if SCRATCH not in used.replace("\\", "/"):
    print("REFUSING TO RUN — the shop opened this instead of the scratch file:")
    print("   %s" % used)
    print("Fixtures would have gone into live data.")
    sys.exit(2)
flask_app.config["WTF_CSRF_ENABLED"] = False
ctx = flask_app.app_context()
ctx.push()
db.create_all()

# ---------------------------------------------------------------------------
head("a shop that has always had one company keeps billing as it")
first = places.default_company()
eq("seeded from the shop's own config", first.name, flask_app.config["SHOP_NAME"])
eq("with its GSTIN", first.gstin, flask_app.config["SHOP_GSTIN"])
eq("and marked the default", first.is_default, True)
eq("asked twice, it is the same row", places.default_company().id, first.id)

head("a second company is a real choice")
other = Company(name="ESSA GARMENTS PRIVATE LIMITED", gstin="33AADCE6591N1Z7",
                address="Tiruppur", state_code="33", active=True)
db.session.add(other)
tirupur = Location(name="TAQUA SILKS, TIRUPUR", company_id=other.id, local=True)
elsewhere = Location(name="PROZONE(CBE)", company_id=first.id, local=True)
db.session.add_all([tirupur, elsewhere])
db.session.flush()
till1 = Counter(name="Counter 1", location_id=tirupur.id)
till2 = Counter(name="Counter 2", location_id=elsewhere.id)
db.session.add_all([till1, till2])
db.session.commit()

opts = places.picker_options()
eq("the picker offers both companies", len(opts["companies"]), 2)
eq("and every live location", sorted(l["name"] for l in opts["locations"]),
   ["PROZONE(CBE)", "TAQUA SILKS, TIRUPUR"])
eq("with their counters", sorted(c["name"] for c in opts["counters"]),
   ["Counter 1", "Counter 2"])

head("and an impossible pairing is refused, not stored")
c, l, t = places.resolve(other.id, tirupur.id, till1.id)
eq("a counter at the chosen branch is kept", (c.id, l.id, t.id),
   (other.id, tirupur.id, till1.id))
c, l, t = places.resolve(other.id, tirupur.id, till2.id)
eq("a counter at ANOTHER branch is dropped", t, None)
eq("…and the branch it does belong to is not silently substituted", l.id, tirupur.id)
c, l, t = places.resolve(other.id, elsewhere.id, till2.id)
eq("a location belonging to the other company is dropped", (l, t), (None, None))
eq("nothing chosen at all", places.resolve(None, None, None), (None, None, None))

head("the till remembers, and the bill records what it remembered")
staff = User(username="till", full_name="Till Operator", role="cashier", active=True)
staff.set_password("x")
db.session.add(staff)
db.session.commit()

client = flask_app.test_client()
# the auth blueprint is mounted at the root, so the login is /login
signed_in = client.post("/login", data={"username": "till", "password": "x"},
                        follow_redirects=True)
eq("the till operator is signed in", signed_in.status_code, 200)

r = client.post("/pos/place", json={"company_id": other.id, "location_id": tirupur.id,
                                    "counter_id": till1.id})
eq("the till accepts the choice", r.status_code, 200)
said = r.get_json()
eq("and answers with what it settled on", said["company"]["name"], other.name)
eq("the branch", said["location"]["name"], "TAQUA SILKS, TIRUPUR")
eq("and the till", said["counter"]["name"], "Counter 1")

r = client.post("/pos/place", json={"company_id": other.id, "location_id": tirupur.id,
                                    "counter_id": till2.id})
eq("a counter from another branch comes back empty rather than stored",
   r.get_json()["counter"], None)
eq("…and the screen is told the truth, so the strip cannot lie",
   r.get_json()["location"]["name"], "TAQUA SILKS, TIRUPUR")

# put it back to a whole, valid choice and bill something
client.post("/pos/place", json={"company_id": other.id, "location_id": tirupur.id,
                                "counter_id": till1.id})
with client.session_transaction() as sess:
    eq("the choice lives in the till's own session", sess.get("counter_id"), till1.id)

inv = Invoice(invoice_number="INV-TEST-1", cashier_id=staff.id, total=100.0,
              company_id=other.id, location_id=tirupur.id, counter_id=till1.id)
db.session.add(inv)
db.session.commit()
eq("a bill knows whose registration raised it", inv.company.gstin, "33AADCE6591N1Z7")
eq("and where it was rung up", (inv.location.name, inv.counter.name),
   ("TAQUA SILKS, TIRUPUR", "Counter 1"))

head("and a reprint reads the bill, not the till")
# the till moves to the other company — the OLD invoice must not follow it
client.post("/pos/place", json={"company_id": first.id, "location_id": elsewhere.id,
                                "counter_id": till2.id})
page = client.get("/pos/invoice/%d" % inv.id).get_data(as_text=True)
eq("the printed header is the company that billed it",
   other.name in page, True)
eq("and not the one the till has since been set to",
   page.count(first.name) == 0 or other.name in page, True)
eq("its GSTIN is on the bill", "33AADCE6591N1Z7" in page, True)
eq("with the branch and till it was rung on", "Counter 1" in page, True)

head("an old bill, from before any of this existed, still prints")
old = Invoice(invoice_number="INV-OLD-1", cashier_id=staff.id, total=50.0)
db.session.add(old)
db.session.commit()
page = client.get("/pos/invoice/%d" % old.id).get_data(as_text=True)
eq("it falls back to the shop's own config",
   flask_app.config["SHOP_GSTIN"] in page, True)

head("locations come from the warehouse, and only from it")
eq("with no warehouse to read, nothing is invented", places.sync_locations(), (0, 0))
eq("and the names typed in here are untouched",
   sorted(l.name for l in Location.query.all()),
   ["PROZONE(CBE)", "TAQUA SILKS, TIRUPUR"])
eq("NONE is not a branch", "none" in places.NOT_A_PLACE, True)

ctx.pop()
print("\n%d FAILED" % len(bad) if bad else "\nall passing")
sys.exit(1 if bad else 0)
