"""Checks that goods leave the shop only when they are actually handed over.

    python test_delivery.py

No pytest — the shop has no test dependency and this needs none. Runs against a
throwaway database with no warehouse attached, so it never touches
textile_shop.db and proves the delivery desk works standalone.

The checks that matter most are the guards, because every one of them is a way a
short delivery could otherwise be signed off as complete: one garment satisfying
its line twice, a tag from another line verifying this one, more going out than
the bill owes, the same line sent twice, and an unscanned piece passing without a
manager behind it. A delivery module whose guards are decorative records the
handover it was told about rather than the one that happened.
"""
import os
import sys
import tempfile
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent

# A scratch database, and deliberately no warehouse: the delivery desk resolves
# per-piece codes from the SKU they carry, so it must work with nothing upstairs.
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'delivery_test.db'}"
os.environ["ESSA_WAREHOUSE_DB"] = str(Path(tempfile.mkdtemp()) / "no-warehouse.db")
sys.path.insert(0, str(SHOP_DIR))

from app import create_app, db                                    # noqa: E402
from app.models import (Category, Customer, Delivery, Invoice,     # noqa: E402
                        InvoiceItem, Product, User)

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


# ---- a bill to collect ------------------------------------------------------
with app.app_context():
    db.create_all()
    cashier = User(username="till", full_name="Till User", role="cashier")
    cashier.set_password("x")
    boss = User(username="boss", full_name="M Anand", role="manager")
    boss.set_password("x")
    cat = Category(name="SHIRT")
    cust = Customer(name="R Kumar", phone="9876500001")
    db.session.add_all([cashier, boss, cat, cust])
    db.session.flush()

    # One item the warehouse serialised per garment, one it did not.
    shirt = Product(sku="ESSA-00002", name="LADIES-T-SHIRT", category_id=cat.id,
                    selling_price=850.0, gst_rate=5.0, stock_qty=50)
    dhoti = Product(sku="ESSA-00007", name="MENS-DHOTI", category_id=cat.id,
                    selling_price=400.0, gst_rate=5.0, stock_qty=50)
    db.session.add_all([shirt, dhoti])
    db.session.flush()

    inv = Invoice(invoice_number="INV-000101", customer_id=cust.id,
                  cashier_id=cashier.id, staff_id=cashier.id,
                  subtotal=3350.0, total=3350.0)
    db.session.add(inv)
    db.session.flush()
    it_shirt = InvoiceItem(invoice_id=inv.id, product_id=shirt.id, quantity=3,
                           unit_price=850.0, gst_rate=5.0, line_total=2550.0)
    it_dhoti = InvoiceItem(invoice_id=inv.id, product_id=dhoti.id, quantity=2,
                           unit_price=400.0, gst_rate=5.0, line_total=800.0)
    db.session.add_all([it_shirt, it_dhoti])
    db.session.commit()
    SHIRT_LINE, DHOTI_LINE, INV_ID = it_shirt.id, it_dhoti.id, inv.id
    SHIRT_ID = shirt.id

    print("\n-- a fresh bill owes everything on it")
    check("nothing collected yet", inv.pending_qty, 5.0)
    check("status", inv.delivery_status, "pending")

client = app.test_client()


def login(username):
    with app.app_context():
        u = User.query.filter_by(username=username).first()
    with client.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True


def deliver(staff, lines):
    return client.post("/delivery/create", json={"staff_code": staff, "lines": lines})


# ---- finding the bill -------------------------------------------------------
login("till")
print("\n-- the bill is found by number, by card and by phone")
r = client.get("/delivery/api/bill?code=INV-000101")
check("by bill number", r.status_code, 200)
bill = r.get_json()["bills"][0]
check("lines on it", len(bill["lines"]), 2)
check("what it owes", bill["pending"], 5.0)
check("customer", bill["customer"]["name"], "R Kumar")
ok("by membership card", client.get("/delivery/api/bill?code=CUST000001").status_code == 200)
ok("by phone", client.get("/delivery/api/bill?code=9876500001").status_code == 200)

print("\n-- a per-piece tag resolves with no warehouse attached")
r = client.get("/delivery/api/scan?code=ESSA-00002-007")
check("resolved", r.status_code, 200)
check("to its product", r.get_json()["product_id"], SHIRT_ID)
check("and names the garment", r.get_json()["piece_code"], "ESSA-00002-007")
r = client.get("/delivery/api/scan?code=ESSA-00007")
check("a SKU tag names no garment", r.get_json()["piece_code"], None)

# ---- part collection --------------------------------------------------------
print("\n-- two shirts and one dhoti go home; the bill stays open")
r = deliver("till", [
    {"invoice_item_id": SHIRT_LINE, "quantity": 2,
     "scans": ["ESSA-00002-001", "ESSA-00002-002"]},
    {"invoice_item_id": DHOTI_LINE, "quantity": 1, "scans": ["ESSA-00007"]},
])
ok("recorded", r.status_code == 200, r.get_json())
check("pieces handed over", r.get_json()["qty"], 3.0)
check("still owed", r.get_json()["pending"], 2.0)
first = r.get_json()["number"]

with app.app_context():
    d = Delivery.query.filter_by(number=first).first()
    check("all of it verified by scan", d.scanned_qty, 3.0)
    check("none passed by hand", d.overridden_qty, 0.0)
    check("goods value", d.total_amount, 2100.0)
    check("bills covered", len(d.bills), 1)
    check("bill is part collected", db.session.get(Invoice, INV_ID).delivery_status, "part")

# ---- the guards -------------------------------------------------------------
print("\n-- one garment cannot go out twice")
r = deliver("till", [{"invoice_item_id": SHIRT_LINE, "quantity": 1,
                      "scans": ["ESSA-00002-001"]}])
check("refused", r.status_code, 400)
ok("says why", "already been handed over" in r.get_json()["error"], r.get_json()["error"])

print("\n-- but a SKU tag repeats, because every piece carries the same one")
r = deliver("till", [{"invoice_item_id": DHOTI_LINE, "quantity": 1,
                      "scans": ["ESSA-00007"]}])
ok("accepted", r.status_code == 200, r.get_json())

print("\n-- more than the bill owes is refused")
r = deliver("till", [{"invoice_item_id": DHOTI_LINE, "quantity": 5,
                      "scans": ["ESSA-00007"]}])
check("refused", r.status_code, 400)
ok("names what is left", "left to collect" in r.get_json()["error"])

print("\n-- a tag from another line cannot verify this one")
r = deliver("till", [{"invoice_item_id": SHIRT_LINE, "quantity": 1,
                      "scans": ["ESSA-00007"]}])
check("refused", r.status_code, 400)
ok("says which", "different line" in r.get_json()["error"])

print("\n-- the same line sent twice is refused, not summed")
r = deliver("till", [
    {"invoice_item_id": SHIRT_LINE, "quantity": 1, "scans": ["ESSA-00002-003"]},
    {"invoice_item_id": SHIRT_LINE, "quantity": 1, "scans": ["ESSA-00002-004"]},
])
check("refused", r.status_code, 400)
with app.app_context():
    check("and nothing went out", db.session.get(Invoice, INV_ID).pending_qty, 1.0)

print("\n-- ordinary staff cannot pass a piece without scanning it")
r = deliver("till", [{"invoice_item_id": SHIRT_LINE, "quantity": 1, "scans": [],
                      "override_reason": "tag torn"}])
check("refused", r.status_code, 403)
ok("points at a manager", "manager" in r.get_json()["error"])

print("\n-- a manager can, with a reason, and it is recorded")
login("boss")
r = deliver("boss", [{"invoice_item_id": SHIRT_LINE, "quantity": 1, "scans": []}])
check("refused with no reason", r.status_code, 400)
r = deliver("boss", [{"invoice_item_id": SHIRT_LINE, "quantity": 1, "scans": [],
                      "override_reason": "tag torn off in the bag"}])
ok("accepted", r.status_code == 200, r.get_json())
with app.app_context():
    d = Delivery.query.filter_by(number=r.get_json()["number"]).first()
    check("passed by hand", d.overridden_qty, 1.0)
    check("nothing claimed as scanned", d.scanned_qty, 0.0)
    check("the reason is kept", d.lines[0].override_reason, "tag torn off in the bag")
    ok("and who allowed it", d.lines[0].overridden_by is not None)

print("\n-- nothing scanned, and nobody named, are both refused")
check("empty delivery", deliver("boss", []).status_code, 400)
r = client.post("/delivery/create", json={"lines": [
    {"invoice_item_id": SHIRT_LINE, "quantity": 1, "scans": []}]})
check("no staff member", r.status_code, 400)
ok("asks for the ID card", "ID card" in r.get_json()["error"])

# ---- the end state ----------------------------------------------------------
print("\n-- the bill is now collected, and stops being offered")
with app.app_context():
    inv = db.session.get(Invoice, INV_ID)
    check("nothing owed", inv.pending_qty, 0.0)
    check("status", inv.delivery_status, "delivered")
check("the card offers no open bill", client.get("/delivery/api/bill?code=CUST000001").status_code, 404)
r = client.get("/delivery/api/bill?code=INV-000101")
ok("the bill itself still loads", r.status_code == 200)
check("owing nothing", r.get_json()["bills"][0]["pending"], 0.0)

print("\n-- a delivery moves no stock and no money")
with app.app_context():
    check("stock untouched", db.session.get(Product, SHIRT_ID).stock_qty, 50.0)
    check("bill total untouched", db.session.get(Invoice, INV_ID).total, 3350.0)

print("\n-- goods that came back on a credit note stop being owed")
# A second bill: 4 pieces, 1 collected, 2 returned. What is left to hand over is
# 1 — and "collected" must stay 1 rather than picking the return up as well.
with app.app_context():
    inv2 = Invoice(invoice_number="INV-000102", cashier_id=User.query.filter_by(
        username="till").first().id, subtotal=3400.0, total=3400.0)
    db.session.add(inv2)
    db.session.flush()
    it2 = InvoiceItem(invoice_id=inv2.id, product_id=SHIRT_ID, quantity=4,
                      unit_price=850.0, gst_rate=5.0, line_total=3400.0)
    db.session.add(it2)
    db.session.commit()
    LINE2, INV2 = it2.id, inv2.id

login("till")
r = deliver("till", [{"invoice_item_id": LINE2, "quantity": 1,
                      "scans": ["ESSA-00002-101"]}])
ok("one collected", r.status_code == 200, r.get_json())

with app.app_context():
    from app.models import CreditNote, CreditNoteItem
    note = CreditNote(number="CRN-000001", invoice_id=INV2,
                      cashier_id=User.query.filter_by(username="till").first().id,
                      subtotal=1700.0, total=1785.0)
    db.session.add(note)
    db.session.flush()
    db.session.add(CreditNoteItem(credit_note_id=note.id, invoice_item_id=LINE2,
                                  product_id=SHIRT_ID, quantity=2,
                                  unit_price=850.0, gst_rate=5.0, line_total=1700.0))
    db.session.commit()

    inv2 = db.session.get(Invoice, INV2)
    check("sold", inv2.total_qty, 4.0)
    check("collected stays what was handed over", inv2.delivered_qty, 1.0)
    check("owed drops by what came back", inv2.pending_qty, 1.0)
    check("still part collected", inv2.delivery_status, "part")

print("\n-- and the bill cannot then over-deliver")
r = deliver("till", [{"invoice_item_id": LINE2, "quantity": 2,
                      "scans": ["ESSA-00002-102", "ESSA-00002-103"]}])
check("refused", r.status_code, 400)
r = deliver("till", [{"invoice_item_id": LINE2, "quantity": 1,
                      "scans": ["ESSA-00002-102"]}])
ok("the last one goes", r.status_code == 200, r.get_json())
with app.app_context():
    check("bill closed", db.session.get(Invoice, INV2).delivery_status, "delivered")

print("\n-- every screen renders")
with app.app_context():
    did = Delivery.query.first().id
for path in ("/delivery/", "/delivery/pending", "/delivery/list",
             f"/delivery/{did}", f"/delivery/{did}/print", f"/pos/invoice/{INV_ID}"):
    check(f"GET {path}", client.get(path).status_code, 200)
ok("the bill carries a scannable code",
   b"<svg" in client.get(f"/pos/invoice/{INV_ID}").data)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All delivery checks passing.")
