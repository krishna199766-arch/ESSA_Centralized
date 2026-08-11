"""Checks that the shop's copy of the warehouse stays honest.

    python test_warehouse_sync.py

No pytest — the shop has no test dependency and this needs none. Runs against a
throwaway database, so it never touches textile_shop.db.

The test that matters most is the first one. app/warehouse_items.py rebuilds the
warehouse's QR payload from its own copy of the field order, because the shop
prints the same code the warehouse does. Two copies of a format is a thing that
rots quietly: this asserts the shop's rebuild is byte-identical to what the
warehouse's own generator produces, for every product it holds. If someone
appends a field upstairs and not here, this fails instead of the labels.
"""
import os
import sys
import tempfile
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SHOP_DIR.parent / "backend"

failures = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got : {got!r}")
        print(f"        want: {want!r}")
        failures.append(name)


def ok(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if not cond and detail else ""))
    if not cond:
        failures.append(name)


def warehouse_truth():
    """The warehouse's own answers: {sku: item payload} and {code: piece payload}.

    Both codebases call their package `app`, and Python has room for one. The
    warehouse is imported first, asked for its answers, then lifted back out of
    sys.modules so `import app` below finds the shop's — the same swap
    backend/app/pos_mount.py performs to mount this shop in the first place.
    """
    if not (BACKEND_DIR / "app").is_dir():
        return None
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        from app.services import barcode_svc
        from app.database import SessionLocal
        from app import models
        db = SessionLocal()
        try:
            return (
                {p.sku: barcode_svc.qr_payload(p) for p in db.query(models.Product).all()},
                {u.code: barcode_svc.unit_qr_payload(u)
                 for u in db.query(models.ProductUnit).all()},
            )
        finally:
            db.close()
    finally:
        for name in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
            del sys.modules[name]
        sys.path.remove(str(BACKEND_DIR))


def main():
    truth = warehouse_truth()
    item_truth, piece_truth = truth if truth else (None, None)

    fd, path = tempfile.mkstemp(suffix=".db", prefix="shoptest-")
    os.close(fd)
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(path).as_posix()}"
    sys.path.insert(0, str(SHOP_DIR))

    from app import create_app, db
    from app.models import Product, StockMovement
    from app.master_categories import sync_master_categories
    from app import warehouse_items as wi

    app = create_app()
    with app.app_context():
        db.create_all()
        sync_master_categories()

        if not wi.available():
            print("SKIP  no warehouse database next door — nothing to check against")
            return 0

        rows = {r["sku"]: r for r in wi.fetch_items()}
        ok("warehouse has items to sync", bool(rows))

        # 1. the QRs the shop rebuilds are the QRs the warehouse prints
        if truth is None:
            print("SKIP  warehouse package not importable — QR equality unchecked")
        else:
            for sku, row in rows.items():
                check(f"item QR matches warehouse for {sku}",
                      wi.qr_payload(row), item_truth.get(sku))
            checked_pieces = 0
            for row in rows.values():
                for u in wi.fetch_units(row["id"]):
                    check(f"piece QR matches warehouse for {u['code']}",
                          u["qr"], piece_truth.get(u["code"]))
                    checked_pieces += 1
                    if checked_pieces >= 3:     # the format is positional; 3 is plenty
                        break
                if checked_pieces >= 3:
                    break
            ok("piece QRs were actually checked", checked_pieces > 0)

        # 2. every warehouse item lands in the shop
        r = wi.sync_warehouse_items()
        ok("sync reported the items", r["added"] == len(rows), f"added={r['added']} of {len(rows)}")
        for sku, row in rows.items():
            p = Product.query.filter_by(sku=sku).first()
            ok(f"{sku} imported", p is not None)
            if not p:
                continue
            # The shop names a product after its category code, not the free
            # text a supplier wrote on an invoice.
            check(f"{sku} name is the category code", p.name, row["category"])
            check(f"{sku} keeps the supplier's wording", p.description, row["description"])
            check(f"{sku} warehouse_id", p.warehouse_id, row["id"])
            check(f"{sku} warehouse_qr", p.warehouse_qr, wi.qr_payload(row))
            ok(f"{sku} category linked", p.category_id is not None or not row["category"])
            # opening stock comes from the warehouse, once
            check(f"{sku} opening stock", p.stock_qty, float(row["stock_qty"] or 0))
            if p.stock_qty:
                moves = StockMovement.query.filter_by(product_id=p.id, reason="opening").all()
                ok(f"{sku} opening logged", len(moves) == 1 and moves[0].change == p.stock_qty)
            # the attribute tuple travels with it
            for warehouse_col, shop_attr in [("material", "fabric"), ("size", "size"),
                                             ("color", "color"), ("product_type", "product_type"),
                                             ("pattern", "pattern"), ("fit", "fit"),
                                             ("design_no", "design_no")]:
                if row[warehouse_col]:
                    check(f"{sku} {shop_attr}", getattr(p, shop_attr), row[warehouse_col])

        # 3. a second sync changes nothing
        r2 = wi.sync_warehouse_items()
        check("re-sync adds nothing", (r2["added"], r2["updated"]), (0, 0))

        # 4. and never overwrites what the shop holds — a sale must survive it.
        # Pick an item the warehouse actually holds stock of: one sitting at zero
        # has no opening movement to count, and the warehouse's figures move.
        stocked = [s for s, r in rows.items() if (r["stock_qty"] or 0) > 0]
        sku = stocked[0] if stocked else next(iter(rows))
        p = Product.query.filter_by(sku=sku).first()
        p.stock_qty = 12
        db.session.commit()
        wi.sync_warehouse_items()
        check("sync leaves shop stock alone", Product.query.filter_by(sku=sku).first().stock_qty, 12)
        if stocked:
            check("no second opening movement",
                  StockMovement.query.filter_by(product_id=p.id, reason="opening").count(), 1)
        else:
            print("SKIP  warehouse holds no stock — opening movement unchecked")

        # 5. every shape of scan resolves to the same product
        row = rows[sku]
        pid = Product.query.filter_by(sku=sku).first().id
        forms = {
            "shop SKU": sku,
            "E1 payload": wi.qr_payload(row),
            "EU1 payload": f"EU1|{sku}-001|{sku}|{row['id']}|{row['description']}|S|White|500",
        }
        unit = wi.fetch_item(unit_code=f"{sku}-001")
        if unit is not None:
            forms["bare per-piece code"] = f"{sku}-001"
        for label, code in forms.items():
            got = wi.resolve_scan(code)
            ok(f"resolve_scan — {label}", got is not None and got.id == pid)

        ok("unknown code resolves to nothing", wi.resolve_scan("NOPE-9999") is None)
        ok("empty code resolves to nothing", wi.resolve_scan("") is None)

        # 6. the escape the printer applies survives a round trip
        parsed = wi.parse_payload("EU1|U-1|SKU\\|X|7")
        check("escaped pipe kept in a field", (parsed or {}).get("sku"), "SKU|X")
        check("field after an escape stays aligned", (parsed or {}).get("id"), "7")
        ok("a JSON QR is not mistaken for a label", wi.parse_payload('{"sku":"X"}') is None)

        # 6b. naming falls back rather than leaving a product nameless
        check("uncategorised falls back to the description",
              wi.item_name({"category": "", "description": "Loose cloth", "sku": "X-1"}),
              "Loose cloth")
        check("nothing but a SKU still names it",
              wi.item_name({"category": None, "description": None, "sku": "X-1"}), "X-1")

        # 7. the QRs the shop draws are ones a scanner can actually read
        from app.utils import qr_svg, qr_module_size_mm
        PX_MM = 25.4 / 96.0                     # CSS px -> mm at 96dpi
        sample = Product.query.filter(Product.warehouse_qr.isnot(None)).first()
        if sample is None:
            print("SKIP  no warehouse QR to measure")
        else:
            svg = qr_svg(sample.warehouse_qr)
            # Without a viewBox, CSS resizes the viewport and clips the drawing
            # instead of scaling it — the code still looks like a QR and cannot
            # be decoded. This is the one thing that must never regress.
            ok("QR svg carries a viewBox", "viewBox" in svg)
            ok("QR svg renders crisp edges", "crispEdges" in svg)

            piece = wi.fetch_units(sample.warehouse_id)
            # Every size the shop draws a QR at, and the box it gets in CSS px.
            sizes = [("item label", sample.warehouse_qr, 104),
                     ("item on the form", sample.warehouse_qr, 140),
                     ("item in the list", sample.warehouse_qr, 72)]
            if piece:
                sizes += [("piece label", piece[0]["qr"], 96),
                          ("piece chip on the form", piece[0]["qr"], 88)]
            for label, payload, px in sizes:
                mm = qr_module_size_mm(payload, px * PX_MM)
                ok(f"{label} is scannable ({mm:.2f}mm/module)", mm >= 0.33)

        # 8. the shop notices when the warehouse is written, and not otherwise
        wi.sync_if_stale()                       # prime the stored signature
        ok("no re-sync while the warehouse is untouched", wi.sync_if_stale() is None)
        wi._last_signature = ("stale",)          # as if the file had moved
        ok("re-syncs once the warehouse changes", wi.sync_if_stale() is not None)

    print()
    if failures:
        print(f"{len(failures)} FAILED: " + ", ".join(failures))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        pass
