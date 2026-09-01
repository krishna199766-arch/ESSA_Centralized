"""Sample warehouses, catalogues and receipts — so the Central Dashboard has
something to draw before real data exists.

WHY THIS IS A SCRIPT AND NOT A SEED. Everything in `seed.py` describes what this
company IS: its category master, its unit types, its accounts. This does not. It
invents four warehouses and a handful of receipts purely so a screen can be
looked at, and inventing business records is exactly the thing a warehouse system
must not do quietly. So it is opt-in, it is labelled, and it is REMOVABLE.

EVERYTHING IT CREATES IS TAGGED. Warehouses, stores and catalogues it makes carry
the DEMO_MARK in a field nobody else writes, and its GRNs carry it in their
invoice number. `remove()` finds them by that mark and takes them out — unposting
each GRN first, which reverses the stock movements and deletes the products those
receipts created. Nothing it did not create is touched.

    python -m app.demo_data --load       # add it
    python -m app.demo_data --remove     # take it all out again
    python -m app.demo_data --status     # what is currently loaded
"""
import argparse
import datetime as dt
import sys

from .database import SessionLocal
from . import models
from .services import catalogues as cat_svc
from .services import inventory as inv
from .services import outward as out_svc
from .services import stock_locations as stock_loc

#: Stamped on everything this module creates, and the only thing `remove` trusts.
#: A name match would be a guess — somebody may legitimately open a real
#: warehouse called Madurai — and guessing wrong here deletes real stock.
DEMO_MARK = "[demo]"

WAREHOUSES = [
    # (name, code, address, catalogue code)
    ("Taqua Silks Warehouse", "TQWH1", "Erode, TN", "SILKS"),
    ("Palakkad Warehouse", "PKWH1", "Palakkad, KL", "GARMENTS"),
    ("Madurai Warehouse", "MDWH1", "Madurai, TN", "GARMENTS"),
]

#: Stands for "the warehouse this install already had", whatever it is called.
#: Naming it literally was a bug: a fresh database calls it "Main Warehouse",
#: Essa's calls it "ESSA Warehouse", and a hard-coded string matches neither
#: reliably — the receipts meant for it were silently skipped.
HOME = None

STORES = [
    ("Essa Textiles - Main Store", "ESST01", HOME),
    ("Essa Textiles - Branch 1", "ESST02", HOME),
    ("Taqua Silks - Showroom", "TQST01", "Taqua Silks Warehouse"),
]

SILK_CATEGORIES = ["SILK-SAREE", "SILK-DHOTI", "SILK-FABRIC", "SILK-BLOUSE"]
SILK_ATTRS = ["color", "material", "design_no", "weave", "zari", "border"]
SILK_OPTIONS = {
    "weave": ["Kanchipuram", "Banarasi", "Mysore", "Arani"],
    "zari": ["Pure", "Half fine", "Tested"],
    "border": ["Temple", "Plain", "Contrast", "Zari"],
}

#: (warehouse name or HOME, description, qty, rate, hsn, category)
RECEIPTS = [
    (HOME, "MENS COTTON SHIRT", 240, 320, "6205", "MENS-SHIRT"),
    (HOME, "LADIES COTTON T-SHIRT", 180, 210, "6109", "LADIES-T-SHIRT"),
    ("Palakkad Warehouse", "MENS COTTON SHIRT", 120, 335, "6205", "MENS-SHIRT"),
    ("Madurai Warehouse", "LADIES COTTON T-SHIRT", 90, 205, "6109", "LADIES-T-SHIRT"),
    ("Taqua Silks Warehouse", "KANCHIPURAM SILK SAREE", 60, 4200, "5007", "SILK-SAREE"),
    ("Taqua Silks Warehouse", "SILK DHOTI", 75, 1150, "5007", "SILK-DHOTI"),
]


def _mark(text):
    return f"{text} {DEMO_MARK}"


def _is_demo(text):
    return DEMO_MARK in (text or "")


# ---------------------------------------------------------------------------
#  load
# ---------------------------------------------------------------------------
def load(db):
    made = {"catalogues": 0, "warehouses": 0, "stores": 0, "grns": 0,
            "transfers": 0, "skipped": []}
    cat_svc.ensure_seed(db)

    # The warehouse this install already had. Created if there genuinely is none
    # — a brand new database has no warehouse until something needs one — and
    # NOT marked as demo, because on every real install it already exists and
    # must survive `remove`.
    home = stock_loc.default_warehouse(db)
    db.commit()

    # --- the silk line ---
    silks = db.query(models.Catalogue).filter(
        models.Catalogue.code == "SILKS").first()
    if not silks:
        silks = cat_svc.create(db, "SILKS", _mark("Silks"),
                               "Sarees, dhotis and silk fabric.", attrs=SILK_ATTRS)
        made["catalogues"] += 1
        for name in SILK_CATEGORIES:
            db.add(models.Category(catalogue_id=silks.id, name=name, section="OVERALL"))
        for attr, values in SILK_OPTIONS.items():
            for v in values:
                cat_svc.add_option(db, silks.id, attr, v)
        db.commit()

    by_code = {"SILKS": silks.id,
               "GARMENTS": cat_svc.default_catalogue(db).id}

    # --- warehouses ---
    for name, code, address, cat_code in WAREHOUSES:
        if db.query(models.Warehouse).filter(models.Warehouse.name == name).first():
            made["skipped"].append(name)
            continue
        db.add(models.Warehouse(name=name, code=code, address=_mark(address),
                                catalogue_id=by_code[cat_code], active=True))
        made["warehouses"] += 1
    db.commit()

    def find(wh_name):
        """A warehouse by name, or the install's own one when HOME is meant."""
        if wh_name is HOME:
            return home
        return db.query(models.Warehouse).filter(
            models.Warehouse.name == wh_name).first()

    # --- stores ---
    for name, code, wh_name in STORES:
        if db.query(models.Store).filter(models.Store.name == name).first():
            continue
        wh = find(wh_name)
        if not wh:
            continue
        db.add(models.Store(name=name, code=code, warehouse_id=wh.id,
                            address=_mark("sample store"), active=True))
        made["stores"] += 1
    db.commit()

    # --- a supplier to buy from ---
    sup = db.query(models.Supplier).filter(
        models.Supplier.name == _mark("Sample Mills")).first()
    if not sup:
        sup = models.Supplier(name=_mark("Sample Mills"))
        db.add(sup)
        db.commit()
        db.refresh(sup)

    # --- receipts, spread over the last fortnight so the chart has a shape ---
    today = dt.datetime.utcnow()
    for i, (wh_name, desc, qty, rate, hsn, category) in enumerate(RECEIPTS):
        wh = find(wh_name)
        if not wh:
            made["skipped"].append(f"{desc}: no warehouse “{wh_name}”")
            continue
        invoice = _mark(f"SMPL-{i + 1:03d}")
        if db.query(models.Purchase).filter(
                models.Purchase.invoice_number == invoice).first():
            continue
        when = today - dt.timedelta(days=(len(RECEIPTS) - i) * 2)
        p = models.Purchase(supplier_id=sup.id, warehouse_id=wh.id,
                            grn_no=inv.next_grn_no(db, when), invoice_number=invoice,
                            invoice_date=when.strftime("%Y-%m-%d"),
                            status="draft", created_at=when)
        db.add(p)
        db.flush()
        cat_id = inv.purchase_catalogue_id(db, p)
        match = inv.match_product(db, None, desc, hsn, sup.id, catalogue_id=cat_id)
        db.add(models.PurchaseLine(
            purchase_id=p.id, description=desc, qty=qty, uom="PCS", rate=rate,
            hsn=hsn, category=category,
            product_id=match.id if match else None, is_new_product=match is None))
        db.commit()
        db.refresh(p)
        res = inv.post_grn(db, p)
        if not res.get("ok"):
            db.rollback()
            made["skipped"].append(f"{desc} at {wh_name}: {res.get('error')}")
            continue
        db.commit()
        # Backdate the movements so the chart shows a fortnight rather than a
        # single spike today. The ledger's own ordering is by id, which this does
        # not disturb — only the timestamps the chart buckets by.
        db.query(models.StockMovement).filter(
            models.StockMovement.ref_type == "purchase",
            models.StockMovement.ref_id == p.id).update(
            {"created_at": when}, synchronize_session=False)
        db.commit()
        made["grns"] += 1

    # --- one transfer, so the dashboard shows movement in both directions ---
    essa = home
    pkd = db.query(models.Warehouse).filter(
        models.Warehouse.name == "Palakkad Warehouse").first()
    if essa and pkd and not db.query(models.StockOutward).filter(
            models.StockOutward.packed_by == _mark("sample")).first():
        shirt = (db.query(models.Product)
                   .filter(models.Product.description == "MENS COTTON SHIRT").first())
        if shirt and stock_loc.qty_at(db, shirt.id, essa.id) >= 40:
            when = today - dt.timedelta(days=3)
            o = out_svc.create_outward(db, {
                "date": when.strftime("%Y-%m-%d"),
                "from_warehouse_id": essa.id, "to_warehouse_id": pkd.id,
                "packed_by": _mark("sample"),
                "lines": [{"product_id": shirt.id, "qty": 40}]})
            db.commit()
            out_svc.post_outward(db, o)
            db.commit()
            out_svc.receive_outward(db, o, received_by=_mark("sample"))
            db.commit()
            db.query(models.StockMovement).filter(
                models.StockMovement.ref_id == o.id,
                models.StockMovement.ref_type.in_(("outward", "transfer_in"))).update(
                {"created_at": when}, synchronize_session=False)
            db.commit()
            made["transfers"] += 1

    return made


# ---------------------------------------------------------------------------
#  remove
# ---------------------------------------------------------------------------
def remove(db):
    """Take out everything `load` put in, and nothing else.

    Order matters: the GRNs are unposted first (which reverses their stock and
    deletes the products they created), then the transfer, then the places. A
    warehouse still holding stock cannot be deleted, so undoing the receipts has
    to come first — and if any of it refuses, it is REPORTED rather than forced.
    """
    out = {"grns": 0, "transfers": 0, "stores": 0, "warehouses": 0,
           "catalogues": 0, "suppliers": 0, "blocked": []}

    # Noted BEFORE anything is deleted, because the warehouse step below has to
    # tell this module's own ledger rows from somebody else's. Unposting a GRN
    # leaves a compensating `reversal` row behind — that is the whole point of an
    # append-only ledger — and those rows still name the warehouse, so a delete
    # that ignored them fails on a foreign key.
    demo_purchase_ids = {p.id for p in db.query(models.Purchase).filter(
        models.Purchase.invoice_number.like(f"%{DEMO_MARK}%")).all()}
    demo_outward_ids = {o.id for o in db.query(models.StockOutward).filter(
        models.StockOutward.packed_by.like(f"%{DEMO_MARK}%")).all()}

    def _ours(mv):
        """Whether a ledger row was written by this module's own documents."""
        if mv.ref_type in ("purchase", "purchase_unpost"):
            return mv.ref_id in demo_purchase_ids
        if mv.ref_type in ("outward", "transfer_in"):
            return mv.ref_id in demo_outward_ids
        return False

    # --- transfers ---
    for o in db.query(models.StockOutward).filter(
            models.StockOutward.packed_by.like(f"%{DEMO_MARK}%")).all():
        # A received transfer has landed at the far end; reverse both halves by
        # deleting its movements and rebuilding the affected products from the
        # ledger, which is what `rebuild` exists for.
        pids = {m.product_id for m in db.query(models.StockMovement).filter(
            models.StockMovement.ref_id == o.id,
            models.StockMovement.ref_type.in_(("outward", "transfer_in"))).all()}
        db.query(models.StockMovement).filter(
            models.StockMovement.ref_id == o.id,
            models.StockMovement.ref_type.in_(("outward", "transfer_in"))).delete(
            synchronize_session=False)
        db.flush()
        for pid in pids:
            p = db.get(models.Product, pid)
            if p:
                stock_loc.rebuild(db, p)
        db.delete(o)
        out["transfers"] += 1
    db.commit()

    # --- GRNs ---
    for p in db.query(models.Purchase).filter(
            models.Purchase.invoice_number.like(f"%{DEMO_MARK}%")).all():
        if p.status == "posted":
            res = inv.unpost_grn(db, p)
            if not res.get("ok"):
                out["blocked"].append(f"GRN {p.grn_no}: {res.get('error')}")
                db.rollback()
                continue
            db.commit()
        db.delete(p)
        out["grns"] += 1
    db.commit()

    # --- the zero-stock shells unposting deliberately left behind ---
    # A GRN that is unposted will not delete a product carrying history from
    # ANOTHER receipt — which is right, and is why an item this sample received
    # into two warehouses survives both unposts at zero stock, holding only its
    # reversal rows. Those are ours too, so they go; anything whose ledger has a
    # single row this sample did not write is left exactly alone.
    out["products"] = 0
    for p in db.query(models.Product).all():
        if round(float(p.stock_qty or 0), 3) != 0 or p.detailed:
            continue
        movements = p.movements
        if not movements or not all(_ours(m) for m in movements):
            continue
        db.delete(p)              # cascades its movements and its balances
        out["products"] += 1
    db.commit()

    # --- stores, then warehouses ---
    for s in db.query(models.Store).filter(
            models.Store.address.like(f"%{DEMO_MARK}%")).all():
        db.delete(s)
        out["stores"] += 1
    db.commit()

    for w in db.query(models.Warehouse).filter(
            models.Warehouse.address.like(f"%{DEMO_MARK}%")).all():
        held = db.query(models.StockBalance).filter(
            models.StockBalance.warehouse_id == w.id,
            models.StockBalance.qty > 0).count()
        if held:
            out["blocked"].append(f"{w.name} still holds {held} item(s)")
            continue
        left = db.query(models.StockMovement).filter(
            models.StockMovement.warehouse_id == w.id).all()
        foreign = [m for m in left if not _ours(m)]
        if foreign:
            # Somebody used this warehouse for real work. Leaving it is the only
            # safe answer: deleting it would take their ledger rows with it.
            out["blocked"].append(
                f"{w.name} has {len(foreign)} movement(s) this sample did not "
                f"create — left in place")
            continue
        for m in left:
            db.delete(m)
        db.query(models.StockBalance).filter(
            models.StockBalance.warehouse_id == w.id).delete(synchronize_session=False)
        db.flush()
        db.delete(w)
        out["warehouses"] += 1
    db.commit()

    # --- the silk catalogue, only if nothing real ended up in it ---
    silks = db.query(models.Catalogue).filter(
        models.Catalogue.code == "SILKS", models.Catalogue.name.like(f"%{DEMO_MARK}%")).first()
    if silks:
        real = (db.query(models.Product).filter(
            models.Product.catalogue_id == silks.id).count()
            + db.query(models.Warehouse).filter(
                models.Warehouse.catalogue_id == silks.id).count())
        if real:
            out["blocked"].append(f"catalogue “{silks.name}” has {real} record(s) "
                                  f"of its own — left in place")
        else:
            db.query(models.Category).filter(
                models.Category.catalogue_id == silks.id).delete(synchronize_session=False)
            db.query(models.CategoryAlias).filter(
                models.CategoryAlias.catalogue_id == silks.id).delete(synchronize_session=False)
            db.delete(silks)
            out["catalogues"] += 1
    db.commit()

    # --- the supplier, if it is left with nothing ---
    sup = db.query(models.Supplier).filter(
        models.Supplier.name.like(f"%{DEMO_MARK}%")).first()
    if sup:
        n = db.query(models.Purchase).filter(
            models.Purchase.supplier_id == sup.id).count()
        if n:
            out["blocked"].append(f"supplier “{sup.name}” still has {n} purchase(s)")
        else:
            db.query(models.Product).filter(
                models.Product.primary_supplier_id == sup.id).update(
                {"primary_supplier_id": None}, synchronize_session=False)
            db.delete(sup)
            out["suppliers"] += 1
    db.commit()
    return out


def status(db):
    return {
        "warehouses": [w.name for w in db.query(models.Warehouse).filter(
            models.Warehouse.address.like(f"%{DEMO_MARK}%")).all()],
        "stores": [s.name for s in db.query(models.Store).filter(
            models.Store.address.like(f"%{DEMO_MARK}%")).all()],
        "grns": [p.grn_no for p in db.query(models.Purchase).filter(
            models.Purchase.invoice_number.like(f"%{DEMO_MARK}%")).all()],
        "transfers": [o.code for o in db.query(models.StockOutward).filter(
            models.StockOutward.packed_by.like(f"%{DEMO_MARK}%")).all()],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--load", action="store_true", help="add the sample data")
    ap.add_argument("--remove", action="store_true", help="take it all out again")
    ap.add_argument("--status", action="store_true", help="what is loaded")
    args = ap.parse_args()
    if not (args.load or args.remove or args.status):
        ap.print_help()
        return 1

    db = SessionLocal()
    try:
        if args.remove:
            r = remove(db)
            print("removed:", {k: v for k, v in r.items() if k != "blocked"})
            for b in r["blocked"]:
                print("  KEPT —", b)
        if args.load:
            r = load(db)
            print("loaded:", {k: v for k, v in r.items() if k != "skipped"})
            for s in r["skipped"]:
                print("  skipped —", s)
        if args.status or args.load:
            print("\nnow present:")
            for k, v in status(db).items():
                print(f"  {k:<12} {v}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
