"""What the shop has sold, read from the shop's own database.

The till is a separate Flask application with its own SQLite file (see
pos_mount.py). It already opens OUR database `mode=ro` to build its catalogue —
this is the mirror of that arrangement and obeys the same rule: read-only, never
written. The shop is the system of record for what was sold, exactly as the
warehouse is for what exists, and a report is not a reason to reach into another
application's tables with a pen.

`products.warehouse_id` in the shop is the join. Everything the shop sells came
from a warehouse item and carries our product id (the shop's
app/warehouse_items.py puts it there); anything the shop added on its own has
none and is invisible here — correctly, because the warehouse holds no stock of
it.

Returns are netted off. A kurti sold and brought back on a credit note is not a
kurti that sold, and counting it as one would keep a genuinely dead line looking
alive on the strength of a sale that was undone.

Every failure degrades to "no sales known" rather than raising: the shop may not
be installed beside us, may never have been opened, or may be locked mid-write.
A dead-stock report that refuses to open because the till is switched off is
worse than one that answers from the warehouse's own ledger and says so — which
is why `available()` is part of the answer and every caller reports it.
"""
import os
import sqlite3
import datetime as dt
from pathlib import Path

# …/backend/app/services/pos_sales.py → …/essa-intake/Textile Retail Shop
_SHOP_DIR = Path(__file__).resolve().parents[3] / "Textile Retail Shop"
_DB_NAME = "textile_shop.db"


def db_path():
    """The shop's database file, or None if the shop isn't installed here."""
    env = os.environ.get("ESSA_POS_DB")
    if env:
        return Path(env) if os.path.exists(env) else None
    p = _SHOP_DIR / _DB_NAME
    return p if p.exists() else None


def available() -> bool:
    return db_path() is not None


def _connect():
    p = db_path()
    if not p:
        return None
    try:
        # uri=ro rather than a plain path: opening read-write would create an
        # empty database where a missing one was, and the shop would then start
        # against a file with no schema
        return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def _day_end(d):
    """An end date the till's timestamps compare against.

    invoice_date is stored as ISO text with a time on it, so `<= '2026-08-17'`
    would exclude everything sold ON the 17th — the last day of a campaign would
    silently count nothing."""
    return f"{d} 23:59:59.999999"


def _rows(sql, params):
    con = _connect()
    if not con:
        return []
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.Error:
        # a shop database older than the columns asked for here, or locked. Both
        # mean "nothing known", not "the warehouse cannot show dead stock"
        return []
    finally:
        con.close()


def _window(start, end, col):
    where, params = "", []
    if start:
        where += f" AND {col} >= ?"
        params.append(str(start))
    if end:
        where += f" AND {col} <= ?"
        params.append(_day_end(end))
    return where, params


def sales_by_product(start=None, end=None):
    """Net sales per WAREHOUSE product id, optionally inside a date window.

    Returns {product_id: {"qty", "amount", "bills", "last_sold"}} where amount is
    the taxable value the customer was billed (line_total), net of returns, and
    `last_sold` is an ISO date string.

    Sales and returns are counted in the window each HAPPENED in, not the window
    the original bill fell in: a campaign that sold nine kurtis and took two back
    within its own dates realised seven, and a return keyed in after the campaign
    closed does not reach back and change what that campaign achieved.
    """
    sold_where, sold_params = _window(start, end, "i.invoice_date")
    sold = _rows(
        "SELECT p.warehouse_id, SUM(ii.quantity), SUM(ii.line_total),"
        "       COUNT(DISTINCT i.id), MAX(i.invoice_date) "
        "FROM invoice_items ii "
        "JOIN invoices i ON i.id = ii.invoice_id "
        "JOIN products p ON p.id = ii.product_id "
        "WHERE p.warehouse_id IS NOT NULL" + sold_where +
        " GROUP BY p.warehouse_id", sold_params)

    ret_where, ret_params = _window(start, end, "cn.created_at")
    returned = _rows(
        "SELECT p.warehouse_id, SUM(ci.quantity), SUM(ci.line_total) "
        "FROM credit_note_items ci "
        "JOIN credit_notes cn ON cn.id = ci.credit_note_id "
        "JOIN products p ON p.id = ci.product_id "
        "WHERE p.warehouse_id IS NOT NULL" + ret_where +
        " GROUP BY p.warehouse_id", ret_params)

    out = {}
    for pid, qty, amount, bills, last in sold:
        if pid is None:
            continue
        out[int(pid)] = {"qty": float(qty or 0), "amount": float(amount or 0),
                         "bills": int(bills or 0),
                         "last_sold": (last or "")[:10] or None}
    for pid, qty, amount in returned:
        if pid is None:
            continue
        row = out.setdefault(int(pid), {"qty": 0.0, "amount": 0.0, "bills": 0,
                                        "last_sold": None})
        row["qty"] = round(row["qty"] - float(qty or 0), 3)
        row["amount"] = round(row["amount"] - float(amount or 0), 2)
    return out


def last_sold_index():
    """{product_id: ISO date} — the most recent till sale of each warehouse item.

    Kept separate from `sales_by_product` because this is the one fact the dead
    stock check turns on, and it is wanted for every product ever sold, whatever
    window a campaign happens to be looking at."""
    return {pid: row["last_sold"] for pid, row in sales_by_product().items()
            if row.get("last_sold")}


def status():
    """What the register should say about where its sales figures came from."""
    p = db_path()
    if not p:
        return {"available": False, "reason": "The shop (POS) module is not installed here",
                "path": None, "last_sale": None, "linked_products": 0}
    rows = _rows("SELECT MAX(i.invoice_date), COUNT(DISTINCT p.warehouse_id) "
                 "FROM invoices i "
                 "JOIN invoice_items ii ON ii.invoice_id = i.id "
                 "JOIN products p ON p.id = ii.product_id "
                 "WHERE p.warehouse_id IS NOT NULL", [])
    last, linked = (rows[0] if rows else (None, 0))
    return {"available": True, "reason": None, "path": str(p),
            "last_sale": (last or "")[:10] or None,
            "linked_products": int(linked or 0)}


def today():
    return dt.date.today()
