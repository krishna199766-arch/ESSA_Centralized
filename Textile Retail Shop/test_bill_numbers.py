"""Checks that every bill gets its own number, and the right one.

    python test_bill_numbers.py

No pytest — the shop has no test dependency and this needs none. Runs against a
throwaway database with no warehouse attached, so it never touches
textile_shop.db.

A bill number is the shop's statutory record of a sale. Two failures matter and
they pull in opposite directions:

  a DUPLICATE  two bills called TG26-007, which is the one thing a tax auditor
               cannot be shown and the reason the number is taken by an atomic
               UPDATE rather than read-and-add-one in Python.

  a GAP        TG26-006 then TG26-008, which invites the question of what
               happened to 007 — so the number is allocated inside the same
               transaction as the sale and given back when the sale fails.

The concurrency check at the bottom is the point of the whole file. It runs real
threads through the real allocator against a real database, because the bug it
guards against cannot be reproduced by calling the function twice in a row: it
only appears when two tills read the same number before either has written.
"""
import os
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DB_PATH = Path(tempfile.mkdtemp()) / "bills_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["ESSA_WAREHOUSE_DB"] = str(Path(tempfile.mkdtemp()) / "no-warehouse.db")
sys.path.insert(0, str(SHOP_DIR))

from app import billing_numbers, create_app, db                      # noqa: E402
from app.models import (BillSequence, Category, Company, Counter,     # noqa: E402
                        Floor, Invoice, InvoiceItem, Location,
                        Product, User)

app = create_app()
failures = []


def check(name, got, want):
    ok_ = got == want
    print(f"{'PASS' if ok_ else 'FAIL'}  {name}")
    if not ok_:
        print(f"        got : {got!r}")
        print(f"        want: {want!r}")
        failures.append(name)


def ok(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if not cond and detail else ""))
    if not cond:
        failures.append(name)


# ---- the financial year -----------------------------------------------------
with app.app_context():
    print("-- the financial year --")
    fy = billing_numbers.financial_year
    check("10 Sep 2026 is in the year that opened in April 2026",
          fy(date(2026, 9, 10)), "26")
    check("31 Mar 2027 is still that year", fy(date(2027, 3, 31)), "26")
    check("1 Apr 2027 begins the next one", fy(date(2027, 4, 1)), "27")
    check("31 Mar 2026 belongs to the year before", fy(date(2026, 3, 31)), "25")
    check("a business closing in January says so",
          fy(date(2026, 3, 31), start_month=1), "26")

    print("\n-- the shape of a number --")
    check("TG + 26 + 1", billing_numbers.format_number("TG", "26", 1), "TG26-001")
    check("…and 23", billing_numbers.format_number("TG", "26", 23), "TG26-023")
    # Three digits is a MINIMUM, not a limit. A series that wrapped at 999 would
    # repeat a number, which is the whole thing this file exists to prevent.
    check("the 1000th bill on a floor does not wrap",
          billing_numbers.format_number("TG", "26", 1000), "TG26-1000")
    check("the shop's plain series keeps the shape it has always had",
          billing_numbers.format_number("INV", "", 1, billing_numbers.FALLBACK_PAD),
          "INV-000001")


# ---- a shop with four floors ------------------------------------------------
with app.app_context():
    db.create_all()
    admin = User(username="admin", full_name="A Rahman", role="admin")
    admin.set_password("x")
    till_user = User(username="ravi", full_name="R Kumar", role="cashier")
    till_user.set_password("x")
    cat = Category(name="SAREE")
    db.session.add_all([admin, till_user, cat])
    db.session.flush()

    company = Company(name="TAQUA SILKS", is_default=True, active=True)
    db.session.add(company)
    db.session.flush()
    shop = Location(name="TIRUPUR", company_id=company.id, active=True)
    other = Location(name="KARUR", company_id=company.id, active=True)
    db.session.add_all([shop, other])
    db.session.flush()

    floors = {}
    for order, (name, prefix) in enumerate(billing_numbers.STANDARD_FLOORS):
        f = Floor(location_id=shop.id, name=name, prefix=prefix,
                  sort_order=order, active=True)
        db.session.add(f)
        floors[prefix] = f
    db.session.flush()

    tg_till = Counter(name="Counter 1", location_id=shop.id,
                      floor_id=floors["TG"].id)
    tg_till2 = Counter(name="Counter 2", location_id=shop.id,
                       floor_id=floors["TG"].id)
    tf_till = Counter(name="Counter 3", location_id=shop.id,
                      floor_id=floors["TF"].id)
    ts_till = Counter(name="Counter 4", location_id=shop.id,
                      floor_id=floors["TS"].id)
    tt_till = Counter(name="Counter 5", location_id=shop.id,
                      floor_id=floors["TT"].id)
    loose = Counter(name="Old till", location_id=other.id)     # no floor
    db.session.add_all([tg_till, tg_till2, tf_till, ts_till, tt_till, loose])

    saree = Product(sku="ESSA-00001", name="SILK SAREE", category_id=cat.id,
                    selling_price=2000.0, stock_qty=500, gst_rate=5.0)
    db.session.add(saree)
    db.session.commit()
    ids = {"tg": tg_till.id, "tg2": tg_till2.id, "tf": tf_till.id,
           "ts": ts_till.id, "tt": tt_till.id, "loose": loose.id,
           "shop": shop.id, "saree": saree.id,
           "floor_tg": floors["TG"].id, "floor_tf": floors["TF"].id}
    year = billing_numbers.financial_year()

    print("\n-- which series a till bills on --")
    check("a till on the Ground Floor bills TG",
          billing_numbers.series_for(tg_till)[:2], ("TG", year))
    check("…the First Floor, TF", billing_numbers.series_for(tf_till)[:2], ("TF", year))
    check("…the Second, TS", billing_numbers.series_for(ts_till)[:2], ("TS", year))
    check("…the Third, TT", billing_numbers.series_for(tt_till)[:2], ("TT", year))
    check("a till on no floor bills the shop's plain series",
          billing_numbers.series_for(loose)[:2], ("INV", ""))
    check("…and so does no till at all",
          billing_numbers.series_for(None)[:2], ("INV", ""))

    print("\n-- each floor counts on its own --")
    # Straight from the brief: three bills on the ground floor and two on the
    # first, and neither knows the other exists.
    got = [billing_numbers.allocate(tg_till)[0] for _ in range(3)]
    check("Ground Floor: 001, 002, 003", got,
          [f"TG{year}-001", f"TG{year}-002", f"TG{year}-003"])
    got = [billing_numbers.allocate(tf_till)[0] for _ in range(2)]
    check("First Floor starts at its own 001", got,
          [f"TF{year}-001", f"TF{year}-002"])
    check("Second Floor too", billing_numbers.allocate(ts_till)[0], f"TS{year}-001")
    check("Third Floor too", billing_numbers.allocate(tt_till)[0], f"TT{year}-001")
    check("…and the ground floor carried on where it was",
          billing_numbers.allocate(tg_till)[0], f"TG{year}-004")

    print("\n-- two tills on ONE floor share its series --")
    # They must: the number is the register, and a floor is one register however
    # many drawers stand on it.
    check("a second Ground Floor till takes the next number, not 001",
          billing_numbers.allocate(tg_till2)[0], f"TG{year}-005")

    print("\n-- the parts are kept, not just the string --")
    number, prefix, fin, seq = billing_numbers.allocate(tg_till)
    check("allocate hands back what the number was built from",
          (number, prefix, fin, seq), (f"TG{year}-006", "TG", year, 6))

    print("\n-- looking is not taking --")
    peeked = billing_numbers.peek(tg_till)
    check("peek says what is next", peeked["number"], f"TG{year}-007")
    check("…twice, because it reserved nothing",
          billing_numbers.peek(tg_till)["number"], f"TG{year}-007")
    check("…and the next allocation is still that number",
          billing_numbers.allocate(tg_till)[0], f"TG{year}-007")
    check("peek on an unmapped till says it is not mapped",
          (billing_numbers.peek(loose)["mapped"],
           billing_numbers.peek(loose)["number"]), (False, "INV-000001"))
    check("…and on a mapped one, names the floor",
          billing_numbers.peek(tg_till)["floor"], "Ground Floor")
    db.session.commit()

    print("\n-- a series opened on a shop that already has bills --")
    # The case every existing shop upgrades into: bills already exist, numbered
    # before any of this was built. A series that started counting at 1 would
    # re-issue numbers already printed and hit the unique constraint — or worse,
    # not hit it, on a prefix nothing had used. So a series opens ABOVE whatever
    # is already on the books.
    db.session.add(Invoice(invoice_number=f"TZ{year}-042", cashier_id=admin.id,
                           subtotal=1.0, total=1.0))
    db.session.commit()
    attic = Floor(location_id=shop.id, name="Attic", prefix="TZ", active=True)
    db.session.add(attic)
    db.session.flush()
    attic_till = Counter(name="Attic till", location_id=shop.id, floor_id=attic.id)
    db.session.add(attic_till)
    db.session.commit()
    check("a brand-new series carries on above the bills already raised",
          billing_numbers.allocate(attic_till)[0], f"TZ{year}-043")
    # …and the shop's own INV- series, which is where every upgrading shop's
    # history actually lives.
    db.session.add(Invoice(invoice_number="INV-000817", cashier_id=admin.id,
                           subtotal=1.0, total=1.0))
    db.session.commit()
    check("…and so does the plain series", billing_numbers.allocate(None)[0],
          "INV-000818")
    db.session.commit()

    print("\n-- a new financial year restarts at 001 --")
    # Nothing has to run at midnight on 1 April: the year is part of the key, so
    # the first bill of the new year opens a new row on its own.
    check("next April is TG27-001",
          billing_numbers.allocate(tg_till, when=date(2027, 4, 1))[0], "TG27-001")
    check("…and the old year is untouched",
          billing_numbers.peek(tg_till)["number"], f"TG{year}-008")
    db.session.commit()


# ---- through the real billing route -----------------------------------------
with app.app_context():
    print("\n-- billing a sale end to end --")
    client = app.test_client()
    client.post("/login", data={"username": "ravi", "password": "x"},
                follow_redirects=True)
    # Put the till on the Ground Floor, the way a cashier does.
    r = client.post("/pos/place", json={"company_id": None, "location_id": ids["shop"],
                                        "floor_id": None, "counter_id": ids["tg"]})
    placed = r.get_json()
    check("the till reports the floor it is mapped to",
          placed["floor"]["name"] if placed.get("floor") else None, "Ground Floor")
    # Seven numbers have been taken on TG above, so the next is 008.
    check("…and the number the next bill will carry",
          placed["next_bill"]["number"], f"TG{year}-008")

    r = client.post("/pos/checkout", json={
        "staff_code": "ravi",
        "items": [{"product_id": ids["saree"], "quantity": 1, "unit_price": 2000.0}],
        "payments": [{"method": "cash", "amount": 2100.0, "tendered": 2100.0}],
    })
    body = r.get_json()
    ok("the bill goes through", r.status_code == 200 and body.get("success"),
       f"{r.status_code}: {body}")
    if body and body.get("success"):
        check("…numbered on the Ground Floor series",
              body["invoice_number"], f"TG{year}-008")
        inv = db.session.get(Invoice, body["invoice_id"])
        check("…and the bill remembers how that was built",
              (inv.bill_prefix, inv.fin_year, inv.bill_seq), ("TG", year, 8))
        check("…and which storey it came from", inv.floor_id, ids["floor_tg"])
        check("…and which till", inv.counter_id, ids["tg"])

    print("\n-- the cashier cannot choose the number --")
    # Sent exactly as a modified page would send it.
    r = client.post("/pos/checkout", json={
        "staff_code": "ravi",
        "invoice_number": "TG26-999", "bill_seq": 999, "bill_prefix": "ZZ",
        "items": [{"product_id": ids["saree"], "quantity": 1, "unit_price": 2000.0}],
        "payments": [{"method": "cash", "amount": 2100.0, "tendered": 2100.0}],
    })
    body = r.get_json()
    ok("a bill claiming its own number still goes through",
       r.status_code == 200 and body.get("success"), f"{r.status_code}: {body}")
    if body and body.get("success"):
        check("…on the next number in the series, not the one it asked for",
              body["invoice_number"], f"TG{year}-009")

    print("\n-- a till moved to another floor changes series at once --")
    client.post("/pos/place", json={"company_id": None, "location_id": ids["shop"],
                                    "floor_id": None, "counter_id": ids["tf"]})
    r = client.post("/pos/checkout", json={
        "staff_code": "ravi",
        "items": [{"product_id": ids["saree"], "quantity": 1, "unit_price": 2000.0}],
        "payments": [{"method": "cash", "amount": 2100.0, "tendered": 2100.0}],
    })
    body = r.get_json()
    check("the same cashier on the First Floor bills TF",
          body.get("invoice_number"), f"TF{year}-003")

    print("\n-- a sale that fails gives its number back --")
    before = billing_numbers.peek(
        db.session.get(Counter, ids["tf"]))["number"]
    r = client.post("/pos/checkout", json={
        "staff_code": "ravi",
        "items": [{"product_id": ids["saree"], "quantity": 99999,
                   "unit_price": 2000.0}],       # more than the shop holds
        "payments": [{"method": "cash", "amount": 1.0, "tendered": 1.0}],
    })
    ok("the sale is refused", r.status_code == 400, str(r.status_code))
    after = billing_numbers.peek(db.session.get(Counter, ids["tf"]))["number"]
    check("…and the next bill is still the number the failed one would have had",
          after, before)

    print("\n-- floor sales keep the shop's plain series --")
    from app.models import SaleSession, SaleSessionItem
    seller = User.query.filter_by(username="ravi").first()
    sess = SaleSession(code="FLR001", salesperson_id=seller.id, status="approved")
    db.session.add(sess)
    db.session.flush()
    db.session.add(SaleSessionItem(session_id=sess.id, product_id=ids["saree"],
                                   quantity=1, unit_price=2000.0, gst_rate=5.0,
                                   line_total=2000.0, tax_amount=100.0))
    db.session.commit()
    r = client.post("/floor/s/FLR001/finalize", json={})
    body = r.get_json()
    ok("a phone sale bills", r.status_code == 200 and body.get("success"),
       f"{r.status_code}: {body}")
    if body and body.get("success"):
        ok("…on the plain series, because a phone is not on a mapped till",
           body["invoice_number"].startswith("INV-"), body["invoice_number"])


# ---- the one that matters: two tills at the same instant ---------------------
#
# Real threads, real HTTP requests, one test client each — which is what two
# tills on one floor actually are. Driving `/pos/checkout` rather than calling
# the allocator, because the protection is spread across the route and the
# database configuration and only the whole path proves it: the number is taken
# inside the sale's transaction, and that transaction opens as a writer (see
# app/__init__.WRITER_ENDPOINTS).
#
# WHAT THIS DOES AND DOES NOT PROVE. It proves the shipped path issues twenty
# distinct numbers under contention, with no gaps and no failed sales. It cannot,
# on SQLite, prove the atomic UPDATE is what does it: SQLite gives a billing
# request the write lock up front, so read-then-add-one would survive here too.
# On Postgres — the deployment where several stores really do bill in parallel —
# there is no such lock and transactions genuinely overlap, and there the
# `UPDATE … SET last_number = last_number + 1` is the only reason two tills do
# not read the same number. Both halves are load-bearing; this test exercises
# one engine.
def bill_once(counter_id, out, index, gate):
    client = app.test_client()
    try:
        client.post("/login", data={"username": "ravi", "password": "x"},
                    follow_redirects=True)
        client.post("/pos/place", json={"location_id": ids["shop"],
                                        "counter_id": counter_id})
        gate.wait()                       # released together, so they contend
        r = client.post("/pos/checkout", json={
            "staff_code": "ravi",
            "items": [{"product_id": ids["saree"], "quantity": 1,
                       "unit_price": 2000.0}],
            "payments": [{"method": "cash", "amount": 2100.0,
                          "tendered": 2100.0}],
        })
        body = r.get_json() or {}
        out[index] = (body.get("invoice_number")
                      if r.status_code == 200 and body.get("success")
                      else f"ERROR: HTTP {r.status_code}: "
                           f"{str(body.get('error'))[:80]}")
    except Exception as exc:                         # noqa: BLE001
        out[index] = f"ERROR: {type(exc).__name__}: {exc}"


with app.app_context():
    row = BillSequence.query.filter_by(prefix="TG", fin_year=year).first()
    start_tg = row.last_number if row else 0
counter_ids = [ids["tg"], ids["tg2"]]

print("\n-- twenty bills, two tills on one floor, all at once --")
ROUNDS = 20
results = [None] * ROUNDS
gate = threading.Barrier(ROUNDS)
threads = [threading.Thread(target=bill_once,
                            args=(counter_ids[i % 2], results, i, gate))
           for i in range(ROUNDS)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=120)

errors = [r for r in results if r and r.startswith("ERROR")]
numbers = [r for r in results if r and not r.startswith("ERROR")]
ok("every till billed — none lost a sale to a lock", not errors,
   "; ".join(errors[:3]))
check("twenty bills came back", len(numbers), ROUNDS)
check("no two bills share a number", len(set(numbers)), len(numbers))
check("…and they are the next twenty on the series, with no gaps",
      sorted(int(n.rsplit("-", 1)[1]) for n in numbers),
      list(range(start_tg + 1, start_tg + 1 + len(numbers))))

with app.app_context():
    rows = Invoice.query.filter(Invoice.invoice_number.like(f"TG{year}-%")).all()
    check("…and the database agrees, with one row per number",
          len({i.invoice_number for i in rows}), len(rows))
    seq = BillSequence.query.filter_by(prefix="TG", fin_year=year).one()
    check("the series counted exactly what it issued",
          seq.last_number, start_tg + ROUNDS)


# ---- the screens, and the master behind them --------------------------------
with app.app_context():
    print("\n-- every screen renders --")
    boss = app.test_client()
    boss.post("/login", data={"username": "admin", "password": "x"},
              follow_redirects=True)
    for path in ("/stores/", "/stores/series", "/pos/", "/pos/invoices",
                 f"/pos/invoices?series=TG", "/pos/api/next-bill"):
        r = boss.get(path)
        ok(f"GET {path}", r.status_code == 200, f"status {r.status_code}")

    print("\n-- the floor master --")
    r = boss.post("/stores/floors/new", data={
        "location_id": ids["shop"], "name": "Fourth Floor",
        "prefix": "tx", "sort_order": "4"}, follow_redirects=True)
    made = Floor.query.filter_by(location_id=ids["shop"], name="Fourth Floor").first()
    ok("a fifth floor is just a row", made is not None)
    check("…and its prefix is stored upper case", made.prefix if made else None, "TX")

    before = Floor.query.count()
    r = boss.post("/stores/floors/new", data={
        "location_id": ids["shop"], "name": "Bad Floor",
        "prefix": "T-G"}, follow_redirects=True)
    check("a prefix that would not read back is refused",
          Floor.query.count(), before)
    ok("…and the page says why", b"letters and digits only" in r.data)

    print("\n-- mapping a till --")
    spare = Counter(name="Counter 9", location_id=ids["shop"])
    db.session.add(spare)
    db.session.commit()
    spare_id = spare.id
    boss.post(f"/stores/counters/{spare_id}/floor",
              data={"floor_id": made.id}, follow_redirects=True)
    check("the till is on the new floor",
          db.session.get(Counter, spare_id).floor_id, made.id)
    check("…so it bills on that series",
          billing_numbers.peek(db.session.get(Counter, spare_id))["number"],
          f"TX{year}-001")

    # A till cannot stand in another building.
    other_floor = Floor(location_id=ids["shop"], name="Roof", prefix="TR")
    db.session.add(other_floor)
    db.session.flush()
    stray = Counter(name="Karur till", location_id=Location.query.filter_by(
        name="KARUR").first().id)
    db.session.add(stray)
    db.session.commit()
    r = boss.post(f"/stores/counters/{stray.id}/floor",
                  data={"floor_id": other_floor.id}, follow_redirects=True)
    check("a till cannot be put on another store's floor",
          db.session.get(Counter, stray.id).floor_id, None)
    ok("…and the page says why", b"own store" in r.data)

    print("\n-- the standard four, for a store that has none --")
    karur = Location.query.filter_by(name="KARUR").first()
    boss.post("/stores/floors/standard", data={"location_id": karur.id},
              follow_redirects=True)
    got = sorted((f.name, f.prefix) for f in
                 Floor.query.filter_by(location_id=karur.id).all()
                 if f.name != "Roof")
    check("all four, with the prefixes from the brief", got,
          [("First Floor", "TF"), ("Ground Floor", "TG"),
           ("Second Floor", "TS"), ("Third Floor", "TT")])
    # …and they share the Tirupur series, because they share its prefixes. That
    # is what the screen warns about, and it is the honest consequence: the bill
    # number is the register, so one prefix is one register.
    karur_tg = Floor.query.filter_by(location_id=karur.id, name="Ground Floor").one()
    karur_till = Counter(name="K1", location_id=karur.id, floor_id=karur_tg.id)
    db.session.add(karur_till)
    db.session.commit()
    ok("a shared prefix continues one series rather than restarting at 001",
       not billing_numbers.peek(karur_till)["number"].endswith("-001"),
       billing_numbers.peek(karur_till)["number"])


print("\n" + ("=" * 60))
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All bill-number checks passed.")
