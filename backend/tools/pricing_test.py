"""Changing prices: the arithmetic, and everything it refuses to do.

    python backend/tools/pricing_test.py

A bulk price change is the fastest way in this application to do a great deal of
damage. The checks that matter are therefore the ones where it must NOT do what
it was asked:

  * cost is not a price and cannot be typed over — every margin reads it;
  * a negative price is not a price;
  * a preview must write nothing, or "look before you leap" means nothing;
  * a revert that would discard somebody's later change must be refused, not
    silently applied;
  * an item with no price to work from is skipped, never set to zero.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'pricing.db'}"

from app.database import Base, SessionLocal, engine                  # noqa: E402
from app import models                                               # noqa: E402
from app.services import pricing as svc                              # noqa: E402

bad = []


def eq(what, got, want):
    if got == want:
        print(f"  ok    {what}")
        return
    bad.append(what)
    print(f"  FAIL  {what}\n        got  {got!r}\n        want {want!r}")


def ok(what, cond, detail=""):
    if cond:
        print(f"  ok    {what}")
        return
    bad.append(what)
    print(f"  FAIL  {what}" + (f"  {detail}" if detail else ""))


def refuses(what, fn, expect=""):
    try:
        fn()
    except svc.PricingError as exc:
        said = str(exc)
        good = expect.lower() in said.lower() if expect else True
        print(f"  {'ok   ' if good else 'FAIL '} {what}")
        if not good:
            bad.append(what)
            print(f"        said: {said!r}\n        want it to mention {expect!r}")
        return
    bad.append(what)
    print(f"  FAIL  {what} — it was allowed")


def head(t):
    print(f"\n{t}")


Base.metadata.create_all(engine)
db = SessionLocal()

# ---- a catalogue to reprice -------------------------------------------------
def product(sku, desc, category, mrp, sale, cost, **kw):
    p = models.Product(sku=sku, description=desc, category=category, mrp=mrp,
                       sale_price=sale, avg_cost=cost, stock_qty=10, **kw)
    db.add(p)
    return p


chud = product("LC001", "LADIES CHUDITHAR", "LADIES-CHUDITHAR", 1500, 1200, 700,
               brand="ABC", size="M", color="Blue")
legg = product("LC002", "LEGGINGS", "LADIES-LEGGINGS", 500, 400, 250,
               brand="ABC", size="M", color="Black")
saree = product("LC003", "SILK SAREE", "LADIES-SAREE", 5000, 4000, 2600,
                brand="XYZ", size="Free", color="Red")
blank = product("LC004", "NEW ARRIVAL", "LADIES-CHUDITHAR", None, None, 0,
                brand="ABC")
db.commit()

head("the arithmetic")
eq("set puts the same figure on everything",
   svc.new_value(chud, "sale_price", "set", 999), 999.0)
eq("a percentage moves from what it is now",
   svc.new_value(chud, "sale_price", "percent", -20), 960.0)
eq("…and rounds to the nearest 10 when asked",
   svc.new_value(saree, "sale_price", "percent", -13, round_to=10), 3480.0)
eq("an amount is a flat rupee change",
   svc.new_value(legg, "sale_price", "amount", 50), 450.0)
eq("discount off MRP works from the printed price",
   svc.new_value(chud, "sale_price", "discount_off_mrp", 25), 1125.0)
eq("a price already at that figure is not a change",
   svc.new_value(chud, "sale_price", "set", 1200), None)
eq("an item with no price to move from is skipped, not zeroed",
   svc.new_value(blank, "sale_price", "percent", -20), None)
eq("…and one with no MRP cannot be discounted off it",
   svc.new_value(blank, "sale_price", "discount_off_mrp", 20), None)
eq("a discount is a percentage, so it is never rounded to rupees",
   svc.new_value(chud, "sale_discount_pct", "set", 12.5, round_to=10), 12.5)

head("what it will not do")
refuses("a price cannot be driven negative",
        lambda: svc.new_value(legg, "sale_price", "amount", -900), "cannot be negative")
refuses("a discount cannot exceed 100%",
        lambda: svc.new_value(chud, "sale_discount_pct", "set", 140), "more than 100")
refuses("cost is not a price this can change",
        lambda: svc.new_value(chud, "avg_cost", "set", 1), "not a price")
refuses("and an operation it does not know is refused",
        lambda: svc.new_value(chud, "sale_price", "double_it", 2), "not something")
refuses("a filter it cannot aim with is refused",
        lambda: svc.find(db, {"weather": "sunny"}), "not something a price change")

head("a preview writes nothing")
before = [(p.id, p.sale_price) for p in db.query(models.Product).all()]
result = svc.preview(db, "sale_price", "percent", -20,
                     filters={"category": "LADIES-CHUDITHAR"})
eq("it says how many would move", result["changed"], 1)
eq("…and how many it looked at", result["total"], 2)
eq("…and that the one with no price is left alone", result["unchanged"], 1)
db.expire_all()
eq("and not one price has actually moved",
   [(p.id, p.sale_price) for p in db.query(models.Product).all()], before)

head("warnings, not refusals")
warned = svc.preview(db, "sale_price", "set", 9999, ids=[chud.id])
ok("selling above MRP is warned about",
   any("above their printed MRP" in w for w in warned["warnings"]),
   str(warned["warnings"]))
ok("…and still allowed, because MRP is blank on plenty of items",
   warned["changed"] == 1)
under = svc.preview(db, "sale_price", "set", 100, ids=[chud.id])
ok("selling below cost is warned about too",
   any("below what they cost" in w for w in under["warnings"]),
   str(under["warnings"]))

head("applying, and the record it leaves")
rev = svc.apply(db, "sale_price", "percent", -20,
                filters={"category": "LADIES-CHUDITHAR"},
                note="festival", user="admin")
db.expire_all()
eq("the chudithar is repriced", db.get(models.Product, chud.id).sale_price, 960.0)
eq("…and the blank one is untouched", db.get(models.Product, blank.id).sale_price, None)
eq("…and another category is untouched", db.get(models.Product, saree.id).sale_price, 4000.0)
eq("the revision counts what it moved", rev.product_count, 1)
ok("…gets a reference", bool(rev.number), rev.number)
eq("…remembers what it was told to do",
   (rev.field, rev.operation, rev.value), ("sale_price", "percent", -20.0))
eq("…and who, and why", (rev.created_by, rev.note), ("admin", "festival"))
eq("…and what was picked, in words", rev.scope, "category = LADIES-CHUDITHAR")
change = rev.changes[0]
eq("every line keeps its before and after",
   (change.sku, change.old_value, change.new_value), ("LC001", 1200.0, 960.0))

refuses("a change that moves nothing is refused rather than recorded",
        lambda: svc.apply(db, "sale_price", "set", 960, ids=[chud.id]),
        "Nothing would change")

head("one item's own price history")
hist = svc.history_for(db, chud.id)
eq("it has one entry", len(hist), 1)
eq("…saying what it went from and to", (hist[0]["old"], hist[0]["new"]), (1200.0, 960.0))
ok("…and why", hist[0]["note"] == "festival" and hist[0]["by"] == "admin")

head("putting it back")
svc.revert(db, rev, user="admin")
db.expire_all()
eq("the price returns to what it was", db.get(models.Product, chud.id).sale_price, 1200.0)
ok("…and the revision says it was put back", rev.reverted_at is not None)
refuses("it cannot be put back twice",
        lambda: svc.revert(db, rev), "already")

head("a revert that would discard a later change is refused")
first = svc.apply(db, "sale_price", "set", 1000, ids=[chud.id], user="a")
second = svc.apply(db, "sale_price", "set", 800, ids=[chud.id], user="b")
refuses("the older one cannot be put back over the newer",
        lambda: svc.revert(db, first), second.number)
ok("…and nothing moved while it was refused",
   db.get(models.Product, chud.id).sale_price == 800.0)
svc.revert(db, second, user="b")
db.expire_all()
eq("the newest one puts back cleanly",
   db.get(models.Product, chud.id).sale_price, 1000.0)
ok("…and now the older one may be put back too",
   svc.revert(db, first, user="a") is not None)
db.expire_all()
eq("…returning to where it started", db.get(models.Product, chud.id).sale_price, 1200.0)

db.close()
print("\n%s" % ("all passing" if not bad else "FAILED: " + ", ".join(bad)))
sys.exit(1 if bad else 0)
