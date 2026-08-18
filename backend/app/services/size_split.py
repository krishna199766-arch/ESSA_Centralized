"""
"30:2, 32:4, 34:4, 36:2" — the size run, and the twelve garments hiding in it.

A supplier bills WOMENS PANT, 12 Pcs, and prints the mix in the size column as a
run: 2 of size 30, 4 of 32, 4 of 34, 2 of 36. That single cell is the whole
breakdown, already counted by the person who packed the carton — and until now it
was carried through as a string, shown on the review screen, and then thrown away.
The GRN posted one product called "WOMENS PANT" holding 12, and the four sizes
that actually exist in the box had no SKU, no price and no QR of their own.

So this reads it. The rows it returns are exactly the rows the breakdown editor
would otherwise be typed by hand, which is the point: the count has been done, and
re-keying a number someone has already written down is how it gets keyed wrong.

WHAT COUNTS AS A RUN
--------------------
Suppliers write the same thing several ways, and two of the separators are
genuinely ambiguous in this trade:

  * ``-`` — "30-2" is thirty, two of them; "30-32" is a size RANGE.
  * ``x`` — "30x2" is thirty, two of them; "127 X 200" is a bedsheet.

Guessing wrong turns a bedsheet into 127 pieces. So the unambiguous separators
(``:`` ``=`` and ``30(2)``) are trusted on their own, and the ambiguous ones are
accepted **only when the arithmetic proves them** — when the quantities add up to
what the line actually received. A run that does not add up is not offered, and
the operator types the breakdown as before. It is better to ask than to invent a
size mix.

Nothing here writes anything. It offers rows; a human accepts them; the existing
breakdown machinery (services/inventory.set_line_splits) does the rest, so a
parsed run and a typed one post through the same code and cannot behave
differently.
"""
import re

#: separators that can only mean "size, then how many"
STRICT = r"[:=]"
#: separators that also mean other things — allowed only when the total agrees
LOOSE = r"[-x×*]"
#: what one row is split on
DELIMS = re.compile(r"[,;|\n/]+")

#: quantities are floats everywhere else in this system, so they are here too
_QTY = r"([0-9]+(?:\.[0-9]+)?)"
_SIZE = r"([A-Za-z0-9][A-Za-z0-9.\s'\"/-]*?)"

_STRICT_RE = re.compile(rf"^\s*{_SIZE}\s*{STRICT}\s*{_QTY}\s*$")
_LOOSE_RE = re.compile(rf"^\s*{_SIZE}\s*{LOOSE}\s*{_QTY}\s*$")
_PAREN_RE = re.compile(rf"^\s*{_SIZE}\s*[\(\[]\s*{_QTY}\s*[\)\]]\s*$")

TOLERANCE = 0.001


def _rows(text, pattern):
    """Every token in `text` read by `pattern`, or None if any token isn't."""
    tokens = [t for t in DELIMS.split(str(text or "")) if t.strip()]
    if not tokens:
        return None
    out = []
    for tok in tokens:
        m = pattern.match(tok)
        if not m:
            return None
        size = " ".join(m.group(1).split()).strip(" -")
        qty = float(m.group(2))
        if not size or qty <= 0:
            return None
        out.append({"size": size, "qty": qty})
    return out or None


def _merge(rows):
    """Fold a size named twice into one row — "30:2, 30:1" is three of size 30,
    and two rows for one size is something set_line_splits refuses outright."""
    merged, order = {}, []
    for r in rows:
        key = r["size"].upper()
        if key not in merged:
            merged[key] = dict(r)
            order.append(key)
        else:
            merged[key]["qty"] += r["qty"]
    return [merged[k] for k in order]


def parse(text, expect=None):
    """Read a size run. Returns {"rows", "total", "matches", "confident", "why"}.

    `expect` is what the line actually received. It is what licences the
    ambiguous separators, and what lets the caller say "this run accounts for 10
    of 12" instead of silently offering an incomplete breakdown.
    """
    blank = {"rows": [], "total": 0.0, "matches": False, "confident": False, "why": ""}
    text = str(text or "").strip()
    if not text:
        return blank

    rows = _rows(text, _STRICT_RE) or _rows(text, _PAREN_RE)
    confident = rows is not None
    if rows is None:
        # "30-2" and "30x2" are only a run if the count comes out right; otherwise
        # they are a size range and a bedsheet, and reading them as a run would
        # invent stock that never existed
        loose = _rows(text, _LOOSE_RE)
        if loose is None:
            return blank
        total = round(sum(r["qty"] for r in loose), 3)
        if expect is None or abs(total - float(expect)) > TOLERANCE:
            return {**blank, "why": ("looks like a size range rather than a "
                                     "size-wise quantity — enter the breakdown by hand")}
        rows = loose

    rows = _merge(rows)
    total = round(sum(r["qty"] for r in rows), 3)
    matches = expect is not None and abs(total - float(expect)) <= TOLERANCE
    why = ""
    if expect is not None and not matches:
        short = round(float(expect) - total, 3)
        why = (f"the size run accounts for {total:g} of {float(expect):g} received"
               + (f" — {short:g} still to place" if short > 0
                  else f" — {-short:g} more than was received"))
    return {"rows": rows, "total": total, "matches": matches,
            "confident": confident, "why": why}


def suggest(line, expect=None):
    """The size run for a GRN line, read from wherever it was printed.

    The size column first, because that is where a supplier puts it. The
    description second: some bills have no size column and write the run into the
    item name instead."""
    if expect is None:
        expect = line.received_qty
    for source, text in (("size", getattr(line, "size", None)),
                         ("description", getattr(line, "description", None))):
        got = parse(text, expect)
        if got["rows"]:
            return {**got, "source": source, "text": text}
    return {"rows": [], "total": 0.0, "matches": False, "confident": False,
            "why": "", "source": None, "text": None}


def to_split_rows(line, rows):
    """Turn parsed sizes into breakdown rows, carrying the line's own pricing.

    A size is a size — everything else about the garment (its category, its cost,
    its retail price) is the same for all of them, so each row inherits the line's
    and the operator changes only what actually differs."""
    return [{
        "size": r["size"], "qty": r["qty"],
        "rate": line.rate, "mrp": line.mrp,
        "sale_price": line.sale_price, "sale_discount_pct": line.sale_discount_pct,
        "category": line.category,
    } for r in rows]
