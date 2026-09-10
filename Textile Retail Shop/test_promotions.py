"""Checks that a promotion gives away exactly what it was configured to.

    python test_promotions.py

No pytest — the shop has no test dependency and this needs none. Runs against a
throwaway database with no warehouse attached, so it never touches
textile_shop.db.

The acceptance case from the brief is at the bottom and is deliberately built
the way an admin would build it — a scheme row, condition rows, reward rows —
rather than by calling anything the engine keeps for itself. Nothing in the
engine knows what a chudithar is, and this file is where that gets proved: the
same code that gives away a leggings gives away a dupatta if somebody types a
different category into the form.

The checks that matter most are the ones where a promotion must NOT fire, or
must fire less than it looks like it should. Every one of them is a way stock
walks out of the shop for nothing: a free item that isn't on the shelf, a
maximum nobody enforces, one garment earning two schemes' rewards, a page that
asks for a free line and is believed, and goods kept after the purchase that
earned them was refunded. A promotion engine whose guards are decorative is an
engine that gives away whatever it is asked for.
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent

# A Windows console is cp1252 by default, and these checks are named in the
# shop's own prose — arrows, rupees, ellipses. Without this the run dies on the
# first check whose NAME it cannot print, which reports an encoding fault as a
# test failure and hides whatever the check actually found.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):        # already redirected somewhere
        pass

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'promo_test.db'}"
os.environ["ESSA_WAREHOUSE_DB"] = str(Path(tempfile.mkdtemp()) / "no-warehouse.db")
sys.path.insert(0, str(SHOP_DIR))

from app import create_app, db, promotions                          # noqa: E402
from app.models import (Category, Company, Counter, Customer,        # noqa: E402
                        Invoice, InvoiceItem, Location, Product,
                        PromotionApplication, PromotionAudit,
                        PromotionCondition, PromotionConditionItem,
                        PromotionPlace, PromotionReward,
                        PromotionRewardItem, PromotionScheme,
                        StockMovement, User)

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


# ---- the shop these tests run in -------------------------------------------

def build_scheme(name, code, buys, gets, **kw):
    """Create a scheme the way the admin form does: rows, not special cases.

    `buys` is [(min_qty, [(product|None, category|None), …]), …]
    `gets` is [(qty, value, [(product|None, category|None), …]), …]
    """
    s = PromotionScheme(name=name, code=code,
                        scheme_type=kw.pop("scheme_type", "buy_get_free"),
                        active=kw.pop("active", True),
                        priority=kw.pop("priority", 100),
                        max_applications=kw.pop("max_applications", None),
                        stackable=kw.pop("stackable", False),
                        reward_selection=kw.pop("reward_selection", "cheapest"),
                        out_of_stock=kw.pop("out_of_stock", "block"),
                        return_policy=kw.pop("return_policy", "reclaim_value"),
                        start_date=kw.pop("start_date", None),
                        end_date=kw.pop("end_date", None))
    db.session.add(s)
    db.session.flush()
    for i, (qty, targets) in enumerate(buys):
        c = PromotionCondition(scheme_id=s.id, min_qty=qty, sort_order=i)
        db.session.add(c)
        db.session.flush()
        for prod, cat in targets:
            db.session.add(PromotionConditionItem(
                condition_id=c.id, product_id=prod.id if prod else None,
                category_id=cat.id if cat else None))
    for i, (qty, value, targets) in enumerate(gets):
        r = PromotionReward(scheme_id=s.id, qty=qty, value=value, sort_order=i)
        db.session.add(r)
        db.session.flush()
        for prod, cat in targets:
            db.session.add(PromotionRewardItem(
                reward_id=r.id, product_id=prod.id if prod else None,
                category_id=cat.id if cat else None))
    for place in kw.pop("places", []):
        db.session.add(PromotionPlace(scheme_id=s.id, **place))
    db.session.commit()
    return s


with app.app_context():
    db.create_all()

    admin = User(username="admin", full_name="A Rahman", role="admin")
    admin.set_password("x")
    till = User(username="ravi", full_name="R Kumar", role="cashier")
    till.set_password("x")

    chudithar = Category(name="LADIES-CHUDITHAR", section="LADIES")
    leggings = Category(name="LADIES-LEGGINGS", section="LADIES")
    dupatta = Category(name="LADIES-DUPATTA", section="LADIES")
    db.session.add_all([admin, till, chudithar, leggings, dupatta])
    db.session.flush()

    def product(sku, name, cat, price, stock, gst=5.0):
        p = Product(sku=sku, name=name, category_id=cat.id, selling_price=price,
                    stock_qty=stock, gst_rate=gst, cost_price=price * 0.6)
        db.session.add(p)
        return p

    ch_m = product("ESSA-00001", "CHUDITHAR M", chudithar, 1200.0, 20)
    ch_l = product("ESSA-00002", "CHUDITHAR L", chudithar, 1200.0, 20)
    leg_a = product("ESSA-00003", "LEGGINGS BLACK", leggings, 350.0, 10)
    leg_b = product("ESSA-00004", "LEGGINGS NAVY", leggings, 420.0, 10)
    dup = product("ESSA-00005", "DUPATTA", dupatta, 500.0, 10)
    db.session.commit()

    scheme = build_scheme(
        "Buy 3 Chudithars, get a Leggings free", "PROMO-000001",
        buys=[(3, [(None, chudithar)])],
        gets=[(1, 0, [(None, leggings)])])

    # ---- what the engine sees --------------------------------------------
    print("\n-- the offer, read back --")
    check("the scheme describes itself from its own rows",
          promotions.describe(scheme),
          "Buy 3 × any LADIES-CHUDITHAR → 1 × any LADIES-LEGGINGS free")

    print("\n-- counting sets --")
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 2}])
    check("2 chudithars earn nothing", len(out.awards), 0)

    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3}])
    check("3 chudithars earn one set", out.awards[0].times, 1)
    check("…and one leggings", out.awards[0].reward_qty, 1.0)
    check("the cheapest eligible leggings is the one given",
          out.awards[0].rewards[0].product.sku, leg_a.sku)
    check("the free item is priced at zero", out.awards[0].rewards[0].unit_price, 0.0)
    check("…and its value is what it would have sold for",
          out.awards[0].benefit, 350.0)

    # 6 ÷ 3 = 2, and 9 ÷ 3 = 3 — the brief's own arithmetic.
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 6}])
    check("6 chudithars earn two sets", out.awards[0].times, 2)
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 9}])
    check("9 chudithars earn three sets", out.awards[0].times, 3)

    # The category is what qualifies, so two different chudithars count together.
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 2},
                               {"product_id": ch_l.id, "quantity": 1}])
    check("two different chudithars add up to one set", out.awards[0].times, 1)

    # A garment outside the category is not a chudithar however many are bought.
    out = promotions.evaluate([{"product_id": dup.id, "quantity": 5}])
    check("5 dupattas earn nothing from a chudithar scheme", len(out.awards), 0)

    print("\n-- the maximum a bill may earn --")
    scheme.max_applications = 2
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 12}])
    check("12 qualifying items with a max of 2 give 2, not 4", out.awards[0].times, 2)
    check("…and 2 free items, not 4", out.awards[0].reward_qty, 2.0)
    scheme.max_applications = None
    db.session.commit()

    print("\n-- stock the shop hasn't got --")
    leg_a.stock_qty = 0
    leg_b.stock_qty = 0
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3}])
    check("no leggings in stock, so nothing is given", len(out.awards), 0)
    ok("…and the till is told why",
       out.notices and "out of stock" in out.notices[0].message.lower(),
       f"notices: {[n.message for n in out.notices]}")

    # One in stock, two sets earned: give the one, say so about the other.
    leg_a.stock_qty = 1
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 6}])
    check("two sets earned, one leggings on the shelf → one given",
          out.awards[0].times, 1)
    ok("…and the shortfall is reported, not swallowed", bool(out.notices))

    print("\n-- substitution --")
    # The cheapest is gone; the scheme is allowed to reach for the next one.
    leg_a.stock_qty = 0
    leg_b.stock_qty = 5
    scheme.out_of_stock = "substitute"
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3}])
    check("a scheme set to substitute gives the next one in stock",
          out.awards[0].rewards[0].product.sku, leg_b.sku)
    ok("…and says it was a substitute", out.awards[0].rewards[0].substituted)

    scheme.out_of_stock = "block"
    leg_a.stock_qty = 10
    leg_b.stock_qty = 10
    db.session.commit()

    print("\n-- the reward is also being bought --")
    # Buy 3 chudithars and the last leggings; the free one has to come from
    # somewhere, and it must not be the one already in the cart.
    leg_a.stock_qty = 1
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3},
                               {"product_id": leg_a.id, "quantity": 1}])
    given = out.awards[0].rewards[0].product.sku if out.awards else None
    ok("the one leggings in the cart is not also given away free",
       given != leg_a.sku,
       f"gave {given} when the only {leg_a.sku} was already being bought")
    leg_a.stock_qty = 10
    db.session.commit()

    print("\n-- a mixed scheme: two conditions, both required --")
    combo = build_scheme(
        "Buy 2 Chudithars + 1 Dupatta, get a Leggings free", "PROMO-000002",
        buys=[(2, [(None, chudithar)]), (1, [(None, dupatta)])],
        gets=[(1, 0, [(None, leggings)])],
        priority=10)
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 2}])
    got = {a.scheme.code for a in out.awards}
    check("2 chudithars alone satisfy neither scheme", got, set())
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 2},
                               {"product_id": dup.id, "quantity": 1}])
    check("2 chudithars and a dupatta satisfy the mixed one",
          {a.scheme.code for a in out.awards}, {"PROMO-000002"})

    print("\n-- one garment cannot earn two schemes --")
    # The mixed scheme runs first (priority 10) and takes 2 chudithars. Only one
    # is left, so the buy-3 scheme cannot also claim them.
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3},
                               {"product_id": dup.id, "quantity": 1}])
    check("the higher-priority scheme takes the garments it uses",
          [a.scheme.code for a in out.awards], ["PROMO-000002"])

    # 5 chudithars: two go to the mixed scheme, three are left for buy-3.
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 5},
                               {"product_id": dup.id, "quantity": 1}])
    check("what is left over still earns the next scheme",
          sorted(a.scheme.code for a in out.awards),
          ["PROMO-000001", "PROMO-000002"])

    print("\n-- …unless stacking is deliberately allowed --")
    scheme.stackable = True
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3},
                               {"product_id": dup.id, "quantity": 1}])
    check("a stackable scheme reads the whole cart regardless",
          sorted(a.scheme.code for a in out.awards),
          ["PROMO-000001", "PROMO-000002"])
    scheme.stackable = False
    combo.active = False
    db.session.commit()

    print("\n-- dates and switches --")
    yesterday = date.today() - timedelta(days=1)
    scheme.end_date = yesterday
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3}])
    check("a scheme that ended yesterday gives nothing today", len(out.awards), 0)
    check("…and says so on the list screen", scheme.status(date.today()), "expired")
    scheme.end_date = None
    scheme.start_date = date.today() + timedelta(days=7)
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3}])
    check("a scheme that starts next week gives nothing today", len(out.awards), 0)
    check("…and reads as scheduled", scheme.status(date.today()), "scheduled")
    scheme.start_date = None
    scheme.active = False
    db.session.commit()
    out = promotions.evaluate([{"product_id": ch_m.id, "quantity": 3}])
    check("a switched-off scheme gives nothing", len(out.awards), 0)
    scheme.active = True
    db.session.commit()

    print("\n-- where it runs --")
    company = Company(name="TAQUA SILKS", is_default=True, active=True)
    db.session.add(company)
    db.session.flush()
    tirupur = Location(name="TIRUPUR", company_id=company.id, active=True)
    karur = Location(name="KARUR", company_id=company.id, active=True)
    db.session.add_all([tirupur, karur])
    db.session.flush()
    till_1 = Counter(name="Counter 1", location_id=tirupur.id)
    db.session.add(till_1)
    db.session.commit()

    db.session.add(PromotionPlace(scheme_id=scheme.id, location_id=tirupur.id))
    db.session.commit()

    cart3 = [{"product_id": ch_m.id, "quantity": 3}]
    out = promotions.evaluate(cart3, place=promotions.Place(location=tirupur))
    check("the branch it was configured for gets it", len(out.awards), 1)
    out = promotions.evaluate(cart3, place=promotions.Place(location=karur))
    check("another branch does not", len(out.awards), 0)
    out = promotions.evaluate(cart3, place=promotions.Place(location=tirupur,
                                                            counter=till_1))
    check("a row naming only the branch covers any till at it", len(out.awards), 1)

    PromotionPlace.query.filter_by(scheme_id=scheme.id).delete()
    db.session.commit()
    out = promotions.evaluate(cart3, place=promotions.Place(location=karur))
    check("a scheme with no places at all runs everywhere", len(out.awards), 1)

    print("\n-- a discount reward rather than a free one --")
    # On its own: the buy-3 scheme wants the same three chudithars and, running
    # first, takes them — which is the non-stacking rule two checks above doing
    # its job. Testing the percentage arithmetic means giving it a cart nobody
    # else has a claim on.
    scheme.active = False
    db.session.commit()
    pct = build_scheme("Buy 3 Chudithars, 50% off a Dupatta", "PROMO-000003",
                       buys=[(3, [(None, chudithar)])],
                       gets=[(1, 50, [(dup, None)])],
                       scheme_type="buy_get_percent", priority=200)
    out = promotions.evaluate(cart3)
    check("only the percentage scheme is left to claim the cart",
          [a.scheme.code for a in out.awards], ["PROMO-000003"])
    award = out.awards[0]
    check("a 50% reward is billed at half price",
          award.rewards[0].unit_price, 250.0)
    check("…and the benefit is the half that wasn't charged", award.benefit, 250.0)
    check("…and it describes itself as a discount, not a giveaway",
          promotions.describe(pct),
          "Buy 3 × any LADIES-CHUDITHAR → 1 × DUPATTA (ESSA-00005) at 50% off")
    pct.active = False
    scheme.active = True
    db.session.commit()


# ---- the acceptance case, end to end ---------------------------------------
#
# Straight from the brief: 20 chudithars and 10 leggings on the shelf, a customer
# buys 3, and afterwards there are 17 and 9 with the leggings on the bill as a
# free item. Everything below goes through the real checkout route — the same
# code path a cashier uses — because the point of the whole exercise is that the
# billing screen needs no special case for this.
with app.app_context():
    print("\n-- acceptance: buy 3 chudithars, get 1 leggings free --")
    ch = Product.query.filter_by(sku="ESSA-00001").first()
    leg = Product.query.filter_by(sku="ESSA-00003").first()
    other = Product.query.filter_by(sku="ESSA-00004").first()
    ch.stock_qty = 20
    leg.stock_qty = 10
    other.stock_qty = 0          # so the cheapest-in-stock answer is unambiguous
    cust = Customer(name="S Lakshmi", phone="9876500002", state_code="33")
    db.session.add(cust)
    db.session.commit()
    ch_id, leg_id, cust_id = ch.id, leg.id, cust.id

    client = app.test_client()
    client.post("/login", data={"username": "ravi", "password": "x"},
                follow_redirects=True)

    r = client.post("/pos/checkout", json={
        "staff_code": "ravi", "customer_id": cust_id,
        "items": [{"product_id": ch_id, "quantity": 3, "unit_price": 1200.0}],
        "payments": [{"method": "cash", "amount": 3780.0, "tendered": 4000.0}],
    })
    body = r.get_json()
    ok("the bill goes through", r.status_code == 200 and body.get("success"),
       f"{r.status_code}: {body}")

    if body and body.get("success"):
        inv = Invoice.query.get(body["invoice_id"])
        ch = db.session.get(Product, ch_id)
        leg = db.session.get(Product, leg_id)

        check("the bill has two lines — three chudithars and a leggings",
              len(inv.items), 2)
        free = [i for i in inv.items if i.promo_role == "reward"]
        check("one of them is a promotion line", len(free), 1)
        check("…for one leggings", (free[0].product.sku, free[0].quantity),
              ("ESSA-00003", 1.0))
        check("…billed at nothing", free[0].line_total, 0.0)
        check("…with no tax on it either", free[0].tax_amount, 0.0)
        ok("…and the bill can say it was free", free[0].is_free_item)
        check("what it was worth is remembered", free[0].promo_value, 350.0)

        qual = [i for i in inv.items if i.promo_role == "qualifying"]
        check("the three chudithars are marked as what earned it",
              (len(qual), qual[0].promo_qty if qual else None), (1, 3.0))

        check("the customer paid for the chudithars only", inv.total, 3780.0)
        check("Ladies Chudithar 20 → 17", ch.stock_qty, 17.0)
        check("Leggings 10 → 9", leg.stock_qty, 9.0)

        moves = StockMovement.query.filter_by(
            reference=inv.invoice_number).all()
        check("both movements are against this same bill", len(moves), 2)
        check("…and the free one says what it was",
              sorted(m.reason for m in moves), ["promo", "sale"])
        check("the free movement is one piece out",
              [m.change for m in moves if m.reason == "promo"], [-1.0])

        app_rows = PromotionApplication.query.filter_by(invoice_id=inv.id).all()
        check("the promotion is recorded against the bill", len(app_rows), 1)
        check("…with the scheme's name as it was at the time",
              app_rows[0].scheme_name, "Buy 3 Chudithars, get a Leggings free")
        check("…what it gave and what that cost",
              (app_rows[0].times_applied, app_rows[0].qualifying_qty,
               app_rows[0].reward_qty, app_rows[0].benefit_value),
              (1, 3.0, 1.0, 350.0))
        ok("…and an audit row saying it was applied",
           PromotionAudit.query.filter_by(invoice_id=inv.id,
                                          event="applied").count() == 1)
        check("the counter is told what to put in the bag",
              [(f["sku"], f["qty"]) for f in body["free_items"]],
              [("ESSA-00003", 1.0)])

        print("\n-- a page cannot ask for a free line --")
        before = db.session.get(Product, leg_id).stock_qty
        r2 = client.post("/pos/checkout", json={
            "staff_code": "ravi",
            "items": [
                {"product_id": ch_id, "quantity": 1, "unit_price": 1200.0},
                # a hand-crafted free line, exactly as a modified page would send
                {"product_id": leg_id, "quantity": 1, "unit_price": 0.0,
                 "promo_role": "reward"},
            ],
            "payments": [{"method": "cash", "amount": 1260.0, "tendered": 1260.0}],
        })
        b2 = r2.get_json()
        ok("a bill claiming its own free item still goes through",
           r2.status_code == 200 and b2.get("success"), f"{r2.status_code}: {b2}")
        if b2 and b2.get("success"):
            inv2 = Invoice.query.get(b2["invoice_id"])
            check("…but it is billed as one chudithar and nothing else",
                  len(inv2.items), 1)
            check("…and no leggings left the shelf",
                  db.session.get(Product, leg_id).stock_qty, before)

        print("\n-- the same offer on the floor-sales path --")
        # A customer served by someone walking the floor has bought the same
        # three chudithars. An offer that depends on which desk rang it up is an
        # offer the shop cannot explain to the person who missed out.
        from app.models import SaleSession, SaleSessionItem
        ch_now = db.session.get(Product, ch_id)
        leg_before = db.session.get(Product, leg_id).stock_qty
        seller = User.query.filter_by(username="ravi").first()
        sess = SaleSession(code="FLOOR1", salesperson_id=seller.id, status="approved")
        db.session.add(sess)
        db.session.flush()
        db.session.add(SaleSessionItem(
            session_id=sess.id, product_id=ch_id, quantity=3,
            unit_price=1200.0, gst_rate=5.0, line_total=3600.0, tax_amount=180.0))
        db.session.commit()

        r4 = client.post("/floor/s/FLOOR1/finalize", json={})
        b4 = r4.get_json()
        ok("the floor sale bills", r4.status_code == 200 and b4.get("success"),
           f"{r4.status_code}: {b4}")
        if b4 and b4.get("success"):
            finv = Invoice.query.get(b4["invoice_id"])
            check("…and earns the same free leggings",
                  [(i.product.sku, i.promo_role) for i in finv.items
                   if i.promo_role == "reward"], [("ESSA-00003", "reward")])
            check("…which came off the shelf too",
                  db.session.get(Product, leg_id).stock_qty, leg_before - 1)
            check("…and the salesperson is told to fetch it",
                  [(f["sku"], f["qty"]) for f in b4["free_items"]],
                  [("ESSA-00003", 1.0)])

        print("\n-- returning the purchase that earned it --")
        # One chudithar back out of three: the set no longer holds, so the free
        # leggings is no longer earned. The customer keeps it, and its value
        # comes off the refund.
        first_item = next(i for i in inv.items if i.promo_role == "qualifying")
        findings = promotions.review_return(inv, {first_item.id: 1})
        check("the promotion is found to be broken", len(findings), 1)
        check("…by exactly one free leggings", findings[0].unearned_qty, 1.0)
        check("…worth what it would have sold for", findings[0].unearned_value, 350.0)
        check("…which the customer is keeping", findings[0].kept_qty, 1.0)
        check("…so that much comes off the refund", findings[0].clawback, 350.0)

        # Returning the free item WITH it settles the scheme instead.
        with_free = promotions.review_return(
            inv, {first_item.id: 1, free[0].id: 1})
        check("handing the free item back too costs nothing",
              with_free[0].clawback, 0.0)

        # …and a return that leaves the set intact is not a promotion event.
        r3 = client.post("/returns/create", data={
            "invoice_id": inv.id, "staff_code": "ravi",
            f"qty_{first_item.id}": "1", f"cond_{first_item.id}": "resellable",
            "refund_method": "cash", "reason": "size",
        }, follow_redirects=True)
        ok("the credit note is raised", r3.status_code == 200)
        from app.models import CreditNote
        note = CreditNote.query.order_by(CreditNote.id.desc()).first()
        check("the refund is the chudithar less the free leggings kept",
              note.total, round(1200.0 + 60.0 - 350.0, 2))
        check("…and says how much was clawed back", note.promo_clawback, 350.0)
        check("the application is reversed, so the reports net down",
              (app_rows[0].status, app_rows[0].times_applied,
               app_rows[0].reward_qty), ("reversed", 0, 0.0))
        ok("…and the reversal is in the audit trail",
           PromotionAudit.query.filter_by(invoice_id=inv.id,
                                          event="reversed").count() == 1)


# ---- the screens, and the registers behind them ----------------------------
#
# A promotion engine nobody can configure is a promotion engine nobody uses, so
# the admin screens are checked the same way the delivery desk's are: by asking
# for them. A template that references a field the model does not have builds
# perfectly and 500s the first time somebody opens it.
with app.app_context():
    print("\n-- every screen renders --")
    boss = app.test_client()
    boss.post("/login", data={"username": "admin", "password": "x"},
              follow_redirects=True)
    sid = PromotionScheme.query.filter_by(code="PROMO-000001").first().id
    inv_id = Invoice.query.first().id

    for path in (f"/promotions/", "/promotions/new", f"/promotions/{sid}",
                 f"/promotions/{sid}/edit", "/promotions/api/products?q=legg",
                 "/pos/", f"/pos/invoice/{inv_id}", f"/pos/invoice/{inv_id}/print",
                 "/returns/?q=INV-000001", "/reports/"):
        r = boss.get(path)
        ok(f"GET {path}", r.status_code == 200, f"status {r.status_code}")

    print("\n-- a scheme can be built through the form --")
    r = boss.post("/promotions/new", data={
        "name": "Buy 2 Chudithars, get a Dupatta free",
        "scheme_type": "buy_get_free", "active": "on", "priority": "50",
        "reward_selection": "cheapest", "out_of_stock": "block",
        "return_policy": "keep",
        "conditions_json": f'[{{"min_qty": 2, "items": [{{"category_id": '
                           f'{Category.query.filter_by(name="LADIES-CHUDITHAR").first().id}}}]}}]',
        "rewards_json": f'[{{"qty": 1, "value": 0, "items": [{{"product_id": '
                        f'{Product.query.filter_by(sku="ESSA-00005").first().id}}}]}}]',
        "places_json": "[]",
    }, follow_redirects=True)
    made = PromotionScheme.query.filter_by(
        name="Buy 2 Chudithars, get a Dupatta free").first()
    ok("the form creates it", r.status_code == 200 and made is not None)
    if made:
        check("…with a code issued for it", made.code.startswith("PROMO-"), True)
        check("…and the rules it was given",
              (len(made.conditions), made.conditions[0].min_qty,
               len(made.rewards), made.rewards[0].qty), (1, 2.0, 1, 1.0))

    print("\n-- a scheme that means nothing is refused --")
    before = PromotionScheme.query.count()
    r = boss.post("/promotions/new", data={
        "name": "Broken", "scheme_type": "buy_get_free", "active": "on",
        "conditions_json": '[{"min_qty": 3, "items": []}]',
        "rewards_json": '[{"qty": 1, "items": [{"category_id": 1}]}]',
        "places_json": "[]",
    })
    check("a buy rule naming nothing is not saved",
          PromotionScheme.query.count(), before)
    ok("…and the page says why",
       b"at least one" in r.data or b"one product or one category" in r.data)

    print("\n-- the reports --")
    from app import reports_lib
    start, end = date.today() - timedelta(days=1), date.today() + timedelta(days=1)
    for key in ("promotions", "promotion_items", "promotion_places",
                "promotion_daily", "promotion_audit"):
        try:
            out = reports_lib.run(key, start, end)
            ok(f"{key} runs", "columns" in out and "rows" in out)
        except Exception as exc:                     # noqa: BLE001
            ok(f"{key} runs", False, str(exc))

    # The store report is the one whose query could quietly return a cross
    # product, and it cannot show that on a bill with no branch against it. So
    # give one a branch and a till and check the row lands where it should.
    placed = Invoice.query.join(PromotionApplication,
                                PromotionApplication.invoice_id == Invoice.id).first()
    if placed is not None:
        loc = Location.query.filter_by(name="TIRUPUR").first()
        cnt = Counter.query.filter_by(location_id=loc.id).first() if loc else None
        placed.location_id = loc.id if loc else None
        placed.counter_id = cnt.id if cnt else None
        db.session.commit()
        by_place = reports_lib.run("promotion_places", start, end)
        rows_for_tirupur = [r for r in by_place["rows"] if r[0] == "TIRUPUR"]
        check("the store report puts the bill at its own branch, once",
              len(rows_for_tirupur), 1)
        check("…at the till that rang it", rows_for_tirupur[0][1] if rows_for_tirupur else None,
              cnt.name if cnt else None)

    scheme_report = reports_lib.run("promotions", start, end)
    # One bill applied it and a return then reversed that application, so the
    # register nets down to nothing given away rather than counting a leggings
    # the customer no longer qualifies for.
    ok("the scheme register lists the scheme that ran",
       any(row[0] == "PROMO-000001" for row in scheme_report["rows"]),
       f"rows: {scheme_report['rows']}")

    audit = reports_lib.run("promotion_audit", start, end)
    ok("the audit register carries both the application and its reversal",
       sum(1 for r in audit["rows"] if r[1] == "PROMO-000001") >= 2,
       f"rows: {audit['rows']}")

    print("\n-- the ask bar reaches them --")
    # With no API key the router matches on keywords, and a keyword broader than
    # its own report steals questions from its neighbours. These are the ones
    # that would go wrong: "reward" used to belong to loyalty points, and a
    # promotion question about free goods was answered with a points ledger.
    from app import nlq
    for question, want in [
            ("promotion scheme report", "promotions"),
            ("what did the offers give away this month", "promotions"),
            ("free items issued last week", "promotion_items"),
            ("promotions by store", "promotion_places"),
            ("promotions by date", "promotion_daily"),
            ("promotion audit", "promotion_audit"),
            # …and the neighbours it must NOT steal
            ("loyalty points", "loyalty"),
            ("which items are running low", "low_stock"),
            ("returns last 7 days", "returns"),
            ("gst for this month", "gst_summary")]:
        got = nlq._offline(question)["report_key"]
        check(f"“{question}”", got, want)


# ---- a broken offer must not cost the shop a sale --------------------------
#
# The checkout wraps the promotion work in a SAVEPOINT, and that is only safe if
# the paid lines and their stock decrements are already flushed when the
# savepoint opens. If they were not, rolling it back would expire the same
# Product rows and discard the SALE's own stock reduction — and the shop would
# bill three garments it never took off the shelf. This forces a failure after
# the promotion has already written rows, and checks what survives.
with app.app_context():
    print("\n-- a promotion that blows up mid-write --")
    ch = Product.query.filter_by(sku="ESSA-00001").first()
    leg = Product.query.filter_by(sku="ESSA-00003").first()
    ch.stock_qty, leg.stock_qty = 20, 10
    db.session.commit()
    ch_id, leg_id = ch.id, leg.id
    apps_before = PromotionApplication.query.count()
    audit_before = PromotionAudit.query.count()

    real_apply = promotions.apply_to_invoice

    def exploding(invoice, outcome, sold_items, place=None, user_id=None):
        made = real_apply(invoice, outcome, sold_items, place=place, user_id=user_id)
        db.session.flush()               # the rows really are in the transaction
        raise RuntimeError("a misconfigured offer, mid-write")

    promotions.apply_to_invoice = exploding
    try:
        r5 = client.post("/pos/checkout", json={
            "staff_code": "ravi",
            "items": [{"product_id": ch_id, "quantity": 3, "unit_price": 1200.0}],
            "payments": [{"method": "cash", "amount": 3780.0, "tendered": 3780.0}],
        })
    finally:
        promotions.apply_to_invoice = real_apply

    b5 = r5.get_json()
    ok("the sale still bills", r5.status_code == 200 and b5.get("success"),
       f"{r5.status_code}: {b5}")
    if b5 and b5.get("success"):
        binv = Invoice.query.get(b5["invoice_id"])
        check("…carrying the paid line and nothing else", len(binv.items), 1)
        check("…at the price of the goods alone", binv.total, 3780.0)
    check("the chudithars still came off stock",
          db.session.get(Product, ch_id).stock_qty, 17.0)
    check("the leggings did not", db.session.get(Product, leg_id).stock_qty, 10.0)
    check("no half-written promotion survived",
          (PromotionApplication.query.count() - apps_before,
           PromotionAudit.query.count() - audit_before), (0, 0))
    if b5 and b5.get("success"):
        moves = StockMovement.query.filter_by(
            reference=binv.invoice_number).all()
        check("…and one movement, for the sale",
              [(m.reason, m.change) for m in moves], [("sale", -3.0)])


print("\n" + ("=" * 60))
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All promotion checks passed.")
