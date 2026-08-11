"""Adding a column to a database that predates it.

The shop carries no migration tool, and adopting one for the handful of columns
that postdate the original schema would be heavier than the problem. SQLite takes
a single-column ALTER cheaply and without rewriting the table, so each module that
introduces a column asks for it here at startup and moves on.

Other engines are left alone: there, the schema is someone else's job and a stray
ALTER from a till would be the wrong thing entirely.
"""
from sqlalchemy import text

from app import db


def table_columns(table):
    """The column names of `table`, or an empty set if it doesn't exist yet."""
    if db.engine.dialect.name != "sqlite":
        return set()
    rows = db.session.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in rows}


def ensure_column(table, column, ddl_type):
    """Add `column` to `table` if it's missing. True if it was added.

    No-op on a table that doesn't exist — create_all is what builds those, and it
    runs before any of this.
    """
    cols = table_columns(table)
    if not cols or column in cols:
        return False
    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    db.session.commit()
    return True


# Every column that postdates the original schema, patched as one step.
COLUMNS = [
    ("categories", "section", "VARCHAR(16)"),      # app/master_categories.py
    ("products", "warehouse_id", "INTEGER"),       # app/warehouse_items.py
    ("products", "warehouse_qr", "TEXT"),          # app/warehouse_items.py
    ("products", "mrp", "FLOAT"),                  # the warehouse attribute tuple,
    ("products", "product_type", "VARCHAR(64)"),   # copied so the shop can show a
    ("products", "pattern", "VARCHAR(64)"),        # product in full on its own
    ("products", "fit", "VARCHAR(64)"),
    ("products", "design_no", "VARCHAR(64)"),
    ("invoices", "staff_id", "INTEGER"),           # who served the sale
]


_applied = False


def apply_all(force=False):
    """Patch the whole list before anything queries the models that declare it.

    All of it, not just the table a caller cares about: the models are a graph,
    and a query on one loads another. Syncing categories walks `Category.products`
    to see what may be pruned, which selects every products column SQLAlchemy
    knows about — so an unpatched `products` breaks a categories-only sync. Doing
    them together removes the ordering trap rather than documenting it.

    Runs its PRAGMAs once per process — it is called from before_request, and a
    schema does not change under a running shop. `force` re-checks anyway.
    """
    global _applied
    if _applied and not force:
        return []
    added = [f"{t}.{c}" for t, c, ddl in COLUMNS if ensure_column(t, c, ddl)]
    _applied = True
    return added
