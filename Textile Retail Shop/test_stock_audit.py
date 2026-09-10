"""Checks that counting a floor tells the truth, and changes nothing until told.

    python test_stock_audit.py

No pytest — the shop has no test dependency and this needs none. Runs against a
throwaway database with no warehouse attached.

The checks that matter most are the ones where the audit must NOT do something.
Every one of them is a way a count could quietly become fiction:

  * a count that moved stock by itself would destroy the only record that is
    supposed to be independent of the books;
  * an unapproved count moving stock would let one person write off a shelf;
  * an adjustment applied twice would move the same variance again;
  * an uncounted line adjusted as zero would turn an unfinished count into a
    shop-wide write-off;
  * two counts open on one floor would give one shelf two answers;
  * a line counted after the count was closed would change a signed document.
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'audit_test.db'}"
os.environ["ESSA_WAREHOUSE_DB"] = str(Path(tempfile.mkdtemp()) / "no-warehouse.db")
sys.path.insert(0, str(SHOP_DIR))

from app import audits, create_app, db                                # noqa: E402
from app.models import (AUDIT_MOVEMENT_REASON, Category, Company,     # noqa: E402
                        Counter, Floor, Location, Product, StockAudit,
                        StockAuditLine, StockMovement, User)

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


def refuses(name, fn, expect=""):
    """The call must be refused, with a reason worth reading."""
    try:
        fn()
    except audits.AuditError as exc:
        said = str(exc)
        good = expect.lower() in said.lower() if expect else True
        print(f"{'PASS' if good else 'FAIL'}  {name}")
        if not good:
            print(f"        said: {said!r}\n        want it to mention: {expect!r}")
            failures.append(name)
        return
    print(f"FAIL  {name}  — it was allowed")
    failures.append(name)


# ---- a shop with two floors and stock on them --------------------------------
with app.app_context():
    db.create_all()
    boss = User(username="admin", full_name="A Rahman", role="admin")
    boss.set_password("x")
    hand = User(username="ravi", full_name="R Kumar", role="cashier")
    hand.set_password("x")
    cat = Category(name="LADIES-CHUDITHAR")
    db.session.add_all([boss, hand, cat])
    db.session.flush()

    co = Company(name="TAQUA SILKS", is_default=True, active=True)
    db.session.add(co)
    db.session.flush()
    store = Location(name="TIRUPUR", company_id=co.id, active=True)
    db.session.add(store)
    db.session.flush()
    ground = Floor(location_id=store.id, name="Ground Floor", prefix="TG",
                   sort_order=0, active=True)
    first = Floor(location_id=store.id, name="First Floor", prefix="TF",
                  sort_order=1, active=True)
    db.session.add_all([ground, first])
    db.session.flush()
    db.session.add(Counter(name="Counter 1", location_id=store.id,
                           floor_id=ground.id))

    def product(sku, name, qty, floor, price=1200.0):
        p = Product(sku=sku, name=name, category_id=cat.id, selling_price=price,
                    stock_qty=qty, gst_rate=5.0, cost_price=price * 0.6,
                    size="M", color="Blue", fabric="Cotton", design_no="CD102",
                    mrp=price * 1.25, floor_id=floor.id if floor else None)
        db.session.add(p)
        return p

    # Stock arrives and sells through the ledger, so purchase/sales/system have
    # something real behind them — which is the identity §4 asks the screen to
    # show its working for.
    chud = product("LC001", "LADIES CHUDITHAR", 0, ground)
    legg = product("LC002", "LEGGINGS", 0, ground)
    saree = product("LC003", "SILK SAREE", 0, first)
    stray = product("LC004", "DUPATTA", 0, None)      # nobody has placed it
    db.session.flush()
    for p, inward, outward in ((chud, 100, 35), (legg, 40, 0),
                               (saree, 25, 5), (stray, 12, 2)):
        p.stock_qty = inward - outward
        db.session.add(StockMovement(product_id=p.id, change=inward,
                                     reason="purchase", reference="GRN-1"))
        if outward:
            db.session.add(StockMovement(product_id=p.id, change=-outward,
                                         reason="sale", reference="INV-1"))
    db.session.commit()
    ids = {"ground": ground.id, "first": first.id, "store": store.id,
           "chud": chud.id, "legg": legg.id, "saree": saree.id,
           "stray": stray.id, "boss": boss.id, "hand": hand.id}

    print("-- the books, and how they got there --")
    totals = audits.ledger_totals([chud.id, legg.id, saree.id])
    check("purchase and sales come off the movement ledger",
          totals[chud.id], (100.0, 35.0))
    check("…and purchase − sales IS the system figure",
          totals[chud.id][0] - totals[chud.id][1], chud.stock_qty)

    print("\n-- opening a count on a floor --")
    audit = audits.open_audit(ground, user_id=ids["boss"], note="evening count")
    check("it gets a number", audit.number.startswith("AUD-"), True)
    check("…and opens on the floor's own products",
          sorted(l.sku for l in audit.lines), ["LC001", "LC002"])
    check("…not the first floor's", "LC003" in [l.sku for l in audit.lines], False)
    line = next(l for l in audit.lines if l.sku == "LC001")
    check("a line freezes what the books said",
          (line.system_qty, line.purchase_qty, line.sales_qty), (65.0, 100.0, 35.0))
    check("…and nothing is counted yet", line.physical_qty, None)
    check("the floor now reads as being counted",
          audits.floor_status(ground)[0], "in_progress")

    refuses("a second count on the same floor is refused",
            lambda: audits.open_audit(ground, user_id=ids["boss"]),
            "already being counted")
    other = audits.open_audit(first, user_id=ids["boss"])
    check("…but another floor may be counted at the same time",
          other.number != audit.number, True)
    audits.set_status(other, "cancelled", user_id=ids["boss"])

    print("\n-- counting --")
    audits.count(audit, line, 63, user_id=ids["hand"])
    check("physical minus system is the difference", line.difference, -2.0)
    check("…and that is a shortage", line.line_status, "shortage")
    check("…worth what the shop sells it for", line.value_variance, -2400.0)

    legg_line = next(l for l in audit.lines if l.sku == "LC002")
    audits.count(audit, legg_line, 40, user_id=ids["hand"])
    check("a count that agrees is matched", legg_line.line_status, "matched")
    audits.count(audit, legg_line, 43, user_id=ids["hand"])
    check("…and re-counting replaces it rather than adding",
          (legg_line.physical_qty, legg_line.line_status), (43.0, "excess"))

    refuses("a negative count is refused",
            lambda: audits.count(audit, legg_line, -1), "cannot be negative")
    refuses("…and so is one that is not a number",
            lambda: audits.count(audit, legg_line, "lots"), "must be a number")

    audits.count(audit, legg_line, 40, user_id=ids["hand"])
    check("counting zero is a real finding",
          (audits.count(audit, legg_line, 0).physical_qty,
           legg_line.line_status), (0.0, "shortage"))
    audits.uncount(audit, legg_line)
    check("…and is different from never having looked",
          (legg_line.physical_qty, legg_line.line_status), (None, "not_counted"))
    audits.count(audit, legg_line, 43, user_id=ids["hand"])

    print("\n-- the summary at the head of the floor --")
    s = audit.summary
    check("products, counted and left",
          (s["products"], s["counted"], s["not_counted"]), (2, 2, 0))
    check("system and physical totals", (s["system_qty"], s["physical_qty"]),
          (105.0, 106.0))
    check("shortage and excess are QUANTITIES, not line counts",
          (s["shortage"], s["excess"]), (2.0, 3.0))
    check("…and the lines are counted separately",
          (s["shortage_lines"], s["excess_lines"], s["matched"]), (1, 1, 0))


# ---- scanning ---------------------------------------------------------------
with app.app_context():
    print("\n-- scanning a tag --")
    audit = StockAudit.query.filter_by(status="in_progress").one()
    line, msg = audits.scan(audit, "LC001")
    check("a SKU resolves to its line", line.sku, "LC001")
    ok("…and says it has already been counted", "already counted" in msg.lower(), msg)
    check("…and the scan is tallied", line.scans, 1)

    # A garment nobody had placed: counting it here is what records it here.
    line, msg = audits.scan(audit, "LC004")
    check("an unplaced garment joins the count", line.sku, "LC004")
    check("…and is now recorded as held on this floor",
          db.session.get(Product, ids["stray"]).floor_id, ids["ground"])
    ok("…and the screen says so", "now recorded as held" in msg, msg)
    check("…with the books frozen onto its line",
          (line.system_qty, line.purchase_qty, line.sales_qty), (10.0, 12.0, 2.0))

    # A garment that belongs upstairs, found down here. It still counts — it is
    # where the piece IS — and the mismatch is the finding.
    line, msg = audits.scan(audit, "LC003")
    check("a garment from another floor still counts here", line.sku, "LC003")
    ok("…and is flagged as off its floor", line.found_off_floor, "not flagged")
    ok("…and the screen says where it belongs", "First Floor" in msg, msg)

    refuses("a code that matches nothing is refused",
            lambda: audits.scan(audit, "NOT-A-TAG"), "Nothing in the catalogue")

    audits.count(audit, line, 4, user_id=ids["hand"])


# ---- the way through, and what may move stock -------------------------------
with app.app_context():
    print("\n-- nothing has touched stock --")
    audit = StockAudit.query.filter_by(status="in_progress").one()
    check("the chudithars are still what the books said",
          db.session.get(Product, ids["chud"]).stock_qty, 65.0)
    check("…and no audit movement has been written",
          StockMovement.query.filter_by(reason=AUDIT_MOVEMENT_REASON).count(), 0)

    print("\n-- and it will not, until the count is approved --")
    refuses("an in-progress count cannot move stock",
            lambda: audits.apply_adjustment(audit, user_id=ids["boss"]),
            "Only an approved count")
    audits.set_status(audit, "completed", user_id=ids["boss"])
    refuses("nor a completed one",
            lambda: audits.apply_adjustment(audit, user_id=ids["boss"]),
            "Only an approved count")
    refuses("a completed count cannot jump straight to approved",
            lambda: audits.set_status(audit, "approved", user_id=ids["boss"]),
            "reviewed")
    audits.set_status(audit, "reviewed", user_id=ids["boss"])
    refuses("nor a reviewed one, before approval",
            lambda: audits.apply_adjustment(audit, user_id=ids["boss"]),
            "Only an approved count")

    refuses("counting is finished once the count is closed",
            lambda: audits.count(audit, audit.lines[0], 99),
            "counting is finished")
    refuses("…and so is scanning",
            lambda: audits.scan(audit, "LC001"), "counting is finished")

    audits.set_status(audit, "approved", user_id=ids["boss"])
    check("every step is stamped with who and when",
          all([audit.completed_at, audit.completed_by_id, audit.reviewed_at,
               audit.approved_at, audit.approved_by_id]), True)

    print("\n-- applying the approved count --")
    before = {pid: db.session.get(Product, pid).stock_qty
              for pid in (ids["chud"], ids["legg"], ids["saree"], ids["stray"])}
    moved, skipped = audits.apply_adjustment(audit, user_id=ids["boss"])
    check("one movement per line this count may correct", moved, 2)
    check("the chudithars come down to what was counted",
          db.session.get(Product, ids["chud"]).stock_qty, 63.0)
    check("…the leggings go up", db.session.get(Product, ids["legg"]).stock_qty, 43.0)
    ok("a line nobody counted is left completely alone",
       db.session.get(Product, ids["stray"]).stock_qty == before[ids["stray"]],
       f"{before[ids['stray']]} -> {db.session.get(Product, ids['stray']).stock_qty}")

    # The one that would have been a real loss. The saree is held upstairs and
    # four of them were found down here; its system figure is 20 for the whole
    # shop. Writing this count into it would have said the shop holds 4 and
    # silently written off the 16 still on the first floor.
    check("a garment found off its floor is counted but NOT adjusted", skipped, 1)
    ok("…so the pieces on its own floor are not written off",
       db.session.get(Product, ids["saree"]).stock_qty == before[ids["saree"]],
       f"{before[ids['saree']]} -> {db.session.get(Product, ids['saree']).stock_qty}")
    ok("…and the count still records that it was down here",
       next(l for l in audit.lines if l.sku == "LC003").found_off_floor)

    moves = StockMovement.query.filter_by(reason=AUDIT_MOVEMENT_REASON).all()
    check("each correction is a movement, not a silent rewrite", len(moves), 2)
    ok("…filed against the audit's number",
       all(audit.number in (m.reference or "") for m in moves),
       str([m.reference for m in moves]))
    check("…and the movements are the differences themselves",
          sorted(m.change for m in moves), [-2.0, 3.0])

    refuses("applying it a second time is refused",
            lambda: audits.apply_adjustment(audit, user_id=ids["boss"]),
            "already applied")
    check("an approved count cannot be re-opened",
          audits.NEXT_STATUS["approved"], set())

    print("\n-- and the count still says what it found --")
    check("the frozen system figure is untouched by the adjustment",
          next(l for l in audit.lines if l.sku == "LC001").system_qty, 65.0)
    check("…so the variance it recorded is still readable",
          next(l for l in audit.lines if l.sku == "LC001").difference, -2.0)
    check("the floor reads as approved now",
          audits.floor_status(db.session.get(Floor, ids["ground"]))[0], "approved")


# ---- filters, screens and the register --------------------------------------
with app.app_context():
    print("\n-- filters --")
    audit = StockAudit.query.order_by(StockAudit.id).first()
    check("by status", sorted(l.sku for l in audits.line_rows(audit, status="shortage")),
          ["LC001", "LC003"])
    check("by free text on the SKU",
          [l.sku for l in audits.line_rows(audit, search="LC002")], ["LC002"])
    check("by an attribute",
          len(audits.line_rows(audit, attributes={"color": "Blue"})), 4)
    check("…and one nothing matches",
          audits.line_rows(audit, attributes={"color": "Puce"}), [])

    print("\n-- every screen renders --")
    client = app.test_client()
    client.post("/login", data={"username": "admin", "password": "x"},
                follow_redirects=True)
    for path in ("/audits/", f"/audits/{audit.id}",
                 f"/audits/{audit.id}?status=shortage",
                 f"/audits/{audit.id}?q=LC001&color=Blue",
                 f"/audits/{audit.id}/print", f"/audits/{audit.id}/export",
                 "/audits/api/products?q=chud", "/inventory/new"):
        r = client.get(path)
        ok(f"GET {path}", r.status_code == 200, f"status {r.status_code}")

    r = client.get(f"/audits/{audit.id}/export")
    body = r.get_data(as_text=True)
    ok("the CSV names the floor and carries the working",
       "Ground Floor" in body and "Purchase qty" in body and "LC001" in body)

    print("\n-- the reports --")
    from datetime import date, timedelta
    from app import reports_lib
    start, end = date.today() - timedelta(days=1), date.today() + timedelta(days=1)
    for key in ("stock_audits", "stock_audit_lines"):
        out = reports_lib.run(key, start, end)
        ok(f"{key} runs", "columns" in out and out["rows"])
    reg = reports_lib.run("stock_audits", start, end)
    ok("the register lists the count", any(audit.number in r for r in reg["rows"]),
       str(reg["rows"]))

    from app import nlq
    for question, want in [("physical stock audit", "stock_audits"),
                           ("stock count last week", "stock_audits"),
                           ("stock variance detail", "stock_audit_lines")]:
        check(f"“{question}”", nlq._offline(question)["report_key"], want)


print("\n" + ("=" * 60))
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All stock-audit checks passed.")
