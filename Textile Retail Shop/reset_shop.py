"""Clear the shop's trading data — catalogue and everything it moved.

    python reset_shop.py            # show what would go, change nothing
    python reset_shop.py --yes      # actually delete
    python reset_shop.py --yes --keep-warehouse   # leave synced items in place
    python reset_shop.py --yes --customers              # the customer list too

For handing a shop over to real trading after it has been demonstrated, or after
the sample data has served its purpose. What goes is the catalogue and every
record that moved stock or money:

    products, stock_movements, invoice_items, invoices, loyalty_txns,
    sale_sessions, sale_session_items

What stays is the shop itself: customers, staff logins, attendance, and the
category master. --customers takes the customer list too; that is safe only once
the records above are gone, since invoices point at customers, so it is opt-in.

Staff logins are never deleted, and not by accident: the shop re-seeds its whole
demo catalogue when it starts with no users at all (see backend/app/pos_mount.py),
so emptying `users` is what would bring the sample data back.

Two of those need explaining. Invoices go because their lines point at products
being deleted — an invoice whose items reference nothing renders as a blank bill,
which is worse than no bill. Loyalty transactions go with them, and customers'
points are zeroed, because every point on the books was earned on an invoice that
is about to stop existing; leaving the balances would let someone redeem against
a history with nothing behind it.

Deleting is not archiving. `active = 0` already hides a product from Inventory,
the counter and the floor app while keeping its history intact — reach for that
instead if the past still matters.

Warehouse items come back on the next sync or restart, by design: the warehouse
is the catalogue's source, and clearing the shop's copy doesn't unmake the stock.
Pass --keep-warehouse to leave them alone.
"""
import sys

from app import create_app, db
from app.dbpatch import apply_all
from app.models import (Customer, Invoice, InvoiceItem, LoyaltyTxn, Product,
                        SaleSession, SaleSessionItem, StockMovement)

# Children before parents — nothing is left pointing at a row that has gone.
ORDER = [
    ("floor session lines", SaleSessionItem),
    ("floor sessions", SaleSession),
    ("loyalty transactions", LoyaltyTxn),
    ("invoice lines", InvoiceItem),
    ("invoices", Invoice),
    ("stock movements", StockMovement),
    ("products", Product),
]


def main(argv):
    confirmed = "--yes" in argv
    keep_warehouse = "--keep-warehouse" in argv

    order = list(ORDER)
    # Appended, so they are deleted last: everything that could still point at
    # them has gone by then.
    if "--customers" in argv:
        order.append(("customers", Customer))

    app = create_app()
    with app.app_context():
        # Before the first model query: the models declare columns that a
        # database made by an older build hasn't got, and counting products
        # selects every one of them. Requests get this from create_app's
        # before_request hook; a script has to ask for it.
        apply_all()

        counts = [(label, model, db.session.query(model).count()) for label, model in order]
        kept = db.session.query(Product).filter(Product.warehouse_id.isnot(None)).count() \
            if keep_warehouse else 0

        print("Would delete:" if not confirmed else "Deleting:")
        for label, _, n in counts:
            if label == "products" and keep_warehouse:
                print(f"  {n - kept:>6}  {label}  ({kept} warehouse-synced kept)")
            else:
                print(f"  {n:>6}  {label}")
        zero_points = "--customers" not in argv
        if zero_points:
            pts = db.session.query(Customer).filter(Customer.loyalty_points > 0).count()
            print(f"  {pts:>6}  customer loyalty balances reset to 0")

        kept = ["staff", "attendance", "categories"]
        if "--customers" not in argv:
            kept.insert(0, "customers")
        print("\nKept: " + ", ".join(kept) + ".")

        if not confirmed:
            print("\nNothing changed. Re-run with --yes to delete.")
            return 0

        for label, model, _ in counts:
            q = db.session.query(model)
            if label == "products" and keep_warehouse:
                q = q.filter(Product.warehouse_id.is_(None))
            q.delete(synchronize_session=False)
        if zero_points:
            db.session.query(Customer).update({Customer.loyalty_points: 0.0},
                                              synchronize_session=False)
        db.session.commit()

        print("\nDone. Remaining products:", db.session.query(Product).count(),
              "| customers:", db.session.query(Customer).count())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
