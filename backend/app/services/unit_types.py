"""
Dozens in, individual items out.

Suppliers bill Essa in dozens. The warehouse does not handle dozens — it handles
handkerchiefs, pillow covers and towels, and every one of them needs a label
somebody can stick on the thing itself. Until now a line reading "1 DOZ" put a
stock of 1 on the books and issued one QR code, and the twelve garments in the
carton had nothing on them at all.

The conversion is two steps, and BOTH of them are per-product configuration:

    billed qty × pieces-in-the-billed-unit ÷ pieces-in-one-stock-unit = stock units

    Handkerchief   1 DOZ  →  12 pieces  ÷ 1  =  12 PCS   → 12 QR codes
    Pillow cover   1 DOZ  →  12 pieces  ÷ 2  =   6 PAIR  →  6 QR codes
    Towel          1 DOZ  →  12 pieces  ÷ 1  =  12 PCS   → 12 QR codes

The first number comes from the unit printed on the invoice (DOZ = 12); the
second from the product's own **unit type** (a pillow cover is sold as a pair, so
one of them is two pieces). Both are rows in the same editable master — see
`models.UnitType` — because they are the same fact asked from two directions, and
one list means "half dozen = 6" is added once and works as a purchase unit and as
a selling unit.

WHAT THE STOCK FIGURE MEANS
---------------------------
The stock unit is the thing Essa counts, prices, labels and sells. Six pairs of
pillow covers is a stock of 6 at the pair rate (₹600 a dozen becomes ₹100 a
pair), six QR codes and six things a shop can sell. Not 12 — a pair is one
article, and carrying it as 12 would mean the shop selling "1" and half a pair
leaving the building. The invoice line is untouched either way: it still reads
1 DOZ @ 600, so the payables side reconciles against the supplier's document.

WHAT IS NOT CONVERTED
---------------------
Anything measured rather than counted. 43.5 MTR of fabric is not 43 or 44 of
anything, so a non-countable unit converts one-to-one and issues no piece codes —
the same line `units.can_serialise` has always drawn.

AND WHAT REFUSES TO CONVERT
---------------------------
5 pieces of something sold in pairs is two pairs and one loose piece. There is no
honest stock figure for that, so posting stops and says so, rather than rounding
a garment into or out of existence. The fix is on the receiving screen where it
belongs: record the odd piece as a shortage or an excess, or say the product is
not a pair after all.
"""
import math
from .. import models

#: The master as it ships. `pieces` is how many individual items are in one of
#: these — which is what makes DOZEN usable both as "the supplier billed 12" and
#: as "we sell them by the dozen". Aliases are the spellings that actually turn
#: up on invoices; without them a bill printed "DZN" reads as an unknown unit and
#: quietly converts one-to-one.
SEED_TYPES = [
    # code,      name,          pieces, aliases,                                    countable
    ("PCS", "Piece", 1, ["PC", "PCS", "PIECE", "PIECES", "NOS", "NO",
                         "EA", "EACH", "UNIT", "UNITS", "U"], True),
    ("PAIR", "Pair", 2, ["PAIR", "PAIRS", "PR", "PRS", "JODI"], True),
    ("SET", "Set", 1, ["SET", "SETS"], True),
    ("HALF-DOZEN", "Half dozen", 6, ["HALF DOZEN", "HALF-DOZEN", "HDZ", "1/2 DZ"], True),
    ("DOZEN", "Dozen", 12, ["DOZ", "DOZ.", "DZ", "DZN", "DOZEN", "DOZENS"], True),
    ("BOX", "Box", 1, ["BOX", "BOXES", "CTN", "CARTON", "CASE"], True),
    ("BUNDLE", "Bundle", 1, ["BUNDLE", "BUNDLES", "BDL"], True),
    # measured, not counted — these convert one-to-one and carry no piece codes
    ("MTR", "Metre", 1, ["MTR", "MTRS", "MTS", "M", "METER", "METERS",
                         "METRE", "METRES"], False),
    ("KG", "Kilogram", 1, ["KG", "KGS", "KILO", "KILOS", "KILOGRAM"], False),
]

#: What Essa's own goods are. The three the warehouse named, plus the other
#: things that are obviously pairs — a rule missing here is not a failure, it
#: only means somebody picks the unit on the GRN line once and it is remembered.
SEED_RULES = [
    ("pillow cover", "PAIR"), ("pillowcover", "PAIR"), ("pillow case", "PAIR"),
    ("pillowcase", "PAIR"), ("cushion cover", "PAIR"),
    ("socks", "PAIR"), ("gloves", "PAIR"),
    ("handkerchief", "PCS"), ("hanky", "PCS"), ("hankie", "PCS"), ("kerchief", "PCS"),
    ("towel", "PCS"), ("napkin", "PCS"),
]

#: What a countable product is when nothing says otherwise. The base individual
#: item — never the billed unit, because "billed by the dozen" is a fact about
#: the invoice, not about what Essa keeps on a shelf.
DEFAULT_CODE = "PCS"

TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
#  The master
# ---------------------------------------------------------------------------
def seed(db):
    """Put the shipped unit types and rules in place. Idempotent, and never
    overwrites an edit: a row that already exists is left exactly as the user
    left it, because `pieces` is precisely the thing they are here to change."""
    n = 0
    have = {t.code for t in db.query(models.UnitType).all()}
    for i, (code, name, pieces, aliases, countable) in enumerate(SEED_TYPES):
        if code in have:
            continue
        db.add(models.UnitType(code=code, name=name, pieces=float(pieces),
                               aliases=list(aliases), countable=countable,
                               is_seed=True, sort=i))
        n += 1
    if n:
        db.commit()
    m = 0
    known = {(r.scope, r.pattern) for r in db.query(models.UnitRule).all()}
    for pattern, code in SEED_RULES:
        if ("keyword", pattern) in known:
            continue
        db.add(models.UnitRule(pattern=pattern, scope="keyword", unit_type=code,
                               source="seed"))
        m += 1
    if m:
        db.commit()
    return n + m


def types(db, countable_only=False):
    q = db.query(models.UnitType)
    if countable_only:
        q = q.filter(models.UnitType.countable == True)      # noqa: E712
    return q.order_by(models.UnitType.sort, models.UnitType.code).all()


def get(db, code):
    """One unit type by its code (case-insensitive), or None."""
    code = (code or "").strip().upper()
    if not code:
        return None
    return db.query(models.UnitType).filter(
        models.UnitType.code == code).first()


def _norm(s):
    return (s or "").strip().upper().replace(".", "").replace(" ", "")


def match_uom(db, uom):
    """The unit type a printed UOM means — 'Doz.' → DOZEN — or None.

    Suppliers spell the same unit five ways, so this goes through the alias list
    as well as the code. An unrecognised unit deliberately returns None rather
    than guessing: converting by a unit nobody defined is how a stock figure ends
    up wrong by a factor of twelve with nothing on screen to say why."""
    key = _norm(uom)
    if not key:
        return None
    for t in types(db):
        if _norm(t.code) == key:
            return t
        if any(_norm(a) == key for a in (t.aliases or [])):
            return t
    return None


def pack_pieces(db, uom):
    """How many individual items are in ONE of the billed unit. 1.0 when the unit
    is unrecognised — an unknown unit must not silently multiply anything."""
    t = match_uom(db, uom)
    return t.pieces_per if t else 1.0


# ---------------------------------------------------------------------------
#  Which unit type a product is
# ---------------------------------------------------------------------------
def rules(db):
    """Rules longest-pattern-first, so 'pillow cover' beats a broader 'cover'."""
    return sorted(db.query(models.UnitRule).all(),
                  key=lambda r: -len(r.pattern or ""))


def rule_for(db, description, category=None):
    """(code, rule) the wording maps to, or (None, None)."""
    desc = (description or "").lower()
    cat = (category or "").lower()
    for r in rules(db):
        pat = (r.pattern or "").lower()
        if not pat:
            continue
        hay = cat if r.scope == "category" else desc
        if pat in hay:
            return r.unit_type, r
    return None, None


def default_code(db, uom=None):
    """The unit type for something nothing has a rule about.

    Countable goods default to the individual piece — never to the billed unit,
    or a dozen would stay a dozen and the twelve garments inside it would go
    untagged, which is the whole problem. Measured goods keep their own unit,
    because metres do not divide into pieces."""
    t = match_uom(db, uom)
    if t and not t.countable:
        return t.code
    return DEFAULT_CODE


def resolve(db, explicit=None, description=None, category=None, uom=None,
            product=None):
    """(code, pieces_per_unit, why) — the unit type to use, and what decided it.

    Order matters and the first entry is the important one: a product that
    already exists keeps its OWN unit, whatever this GRN line says. Re-buying
    pillow covers must land on the same stock record counted the same way; a line
    that disagrees is a line to correct, not a reason to restate the shelf."""
    if product is not None and product.unit_type:
        return (product.unit_type,
                float(product.pieces_per_unit or 1.0) or 1.0,
                f"{product.sku or 'this product'} is already counted in "
                f"{product.unit_type}")
    code = (explicit or "").strip().upper() or None
    why = "chosen on the GRN line" if code else ""
    if not code:
        code, rule = rule_for(db, description, category)
        if code:
            why = f"“{rule.pattern}” is configured as {code}"
    if not code:
        code = default_code(db, uom)
        why = (f"nothing configured for this product — defaulted to {code}")
    t = get(db, code)
    if not t:
        # a code someone typed that no longer exists in the master. Fall back
        # rather than fail: the alternative is a GRN that cannot post because a
        # master row was deleted.
        t = get(db, DEFAULT_CODE)
        why = f"unit type “{code}” is not in the master — using {DEFAULT_CODE}"
    return (t.code if t else DEFAULT_CODE), (t.pieces_per if t else 1.0), why


def learn(db, description, code, scope="keyword"):
    """Remember a unit chosen by hand, so the same wording is right next time.

    Keyed on a short, stable phrase from the description rather than the whole
    string — supplier wording carries sizes, colours and codes that never repeat,
    and a rule that matches one invoice line exactly is a rule that never fires
    again."""
    key = _rule_key(description)
    code = (code or "").strip().upper()
    if not key or not code:
        return None
    r = db.query(models.UnitRule).filter(
        models.UnitRule.scope == scope, models.UnitRule.pattern == key).first()
    if r:
        if r.unit_type != code:
            r.unit_type = code
            r.source = "human"
        r.hits = (r.hits or 0) + 1
    else:
        r = models.UnitRule(pattern=key, scope=scope, unit_type=code,
                            source="human", hits=1)
        db.add(r)
    db.flush()
    return r


#: Words that say nothing about what kind of article something is. Stripped
#: before a description becomes a rule, so "MENS COTTON TOWEL 60X30" teaches
#: "cotton towel" and not a phrase only that invoice will ever contain.
_NOISE = {"mens", "men", "ladies", "women", "womens", "kids", "boys", "girls",
          "gents", "new", "fancy", "super", "special", "printed", "plain",
          "assorted", "asstd", "set", "pc", "pcs", "piece", "pieces", "doz",
          "dozen", "size", "with", "and", "the", "of"}


def _rule_key(description):
    words = [w for w in "".join(
        c if (c.isalnum() or c.isspace()) else " "
        for c in (description or "").lower()).split()
        if w and not w.isdigit() and w not in _NOISE and len(w) > 2]
    return " ".join(words[:3])


# ---------------------------------------------------------------------------
#  The arithmetic
# ---------------------------------------------------------------------------
def convert(db, qty, uom, code, rate=None):
    """Billed quantity → stock units, with every step shown.

    Everything the GRN screen needs to explain itself ("1 DOZ → 12 pcs → 6 PAIR
    → 6 labels") and everything posting needs to act, from one call — so the
    number on the screen and the number that reaches the ledger cannot be
    computed two different ways."""
    qty = float(qty or 0)
    t = get(db, code) or get(db, DEFAULT_CODE)
    per = t.pieces_per if t else 1.0
    pack = pack_pieces(db, uom)
    pieces = qty * pack
    units = pieces / per if per else pieces
    whole = abs(units - round(units)) <= TOLERANCE
    # stock units per billed unit — what a rate is divided by, so the money per
    # stock unit comes out of the money per billed unit rather than being re-typed
    factor = (pack / per) if per else 1.0
    out = {
        "billed_qty": round(qty, 3), "billed_uom": (uom or "").strip() or None,
        "pack_size": pack, "pieces": round(pieces, 3),
        "unit_type": t.code if t else DEFAULT_CODE,
        "unit_name": t.name if t else DEFAULT_CODE,
        "pieces_per_unit": per, "countable": bool(t.countable) if t else True,
        "units": round(units, 3), "factor": factor, "whole": whole,
        # what is left over when the pieces do not divide into whole stock units
        "remainder_pieces": round(pieces - math.floor(units + TOLERANCE) * per, 3),
        "converted": abs(units - qty) > TOLERANCE,
        "rate_per_unit": (round(float(rate) / factor, 4)
                          if rate not in (None, "") and factor else
                          (float(rate) if rate not in (None, "") else None)),
    }
    out["labels"] = int(round(units)) if (out["countable"] and whole) else 0
    out["explain"] = explain(out)
    return out


def convert_for_product(db, product, qty, uom, rate=None):
    """The same conversion, pinned to a product's own frozen unit."""
    code, _, _ = resolve(db, product=product, uom=uom)
    return convert(db, qty, uom, code, rate)


def explain(conv):
    """"1 DOZ → 12 pcs → 6 PAIR · 6 labels" — the line the receiving screen shows.

    Written out in full even when nothing changes, because "12 PCS → 12 PCS" is
    what tells someone the rule was applied and came to the same number, which is
    a different thing from the rule never having run."""
    q, uom = conv["billed_qty"], conv["billed_uom"] or "PCS"
    bits = [f"{q:g} {uom}"]
    if conv["pack_size"] != 1:
        bits.append(f"{conv['pieces']:g} pcs")
    bits.append(f"{conv['units']:g} {conv['unit_type']}")
    line = " → ".join(dict.fromkeys(bits))
    if not conv["countable"]:
        return line + " · measured, no piece labels"
    if not conv["whole"]:
        return (line + f" · ⚠ {conv['remainder_pieces']:g} piece(s) left over — "
                       f"does not divide into whole {conv['unit_type']}")
    return line + f" · {conv['labels']} QR label(s)"


def apply_to_product(db, product, code, pieces_per):
    """Freeze a product's unit on it, and keep the displayed UOM in step.

    Set once, at creation. Stock already counted under a rule is not restated
    because the master was later edited — see models.Product.pieces_per_unit."""
    product.unit_type = code
    product.pieces_per_unit = float(pieces_per or 1.0)
    product.uom = code
    return product


def check_line(db, description, qty, uom, code):
    """(ok, message) — whether this quantity divides into whole units.

    Called before anything is written, so a GRN that cannot convert honestly is
    refused with the arithmetic in the message rather than posted with a
    quantity nobody can account for."""
    conv = convert(db, qty, uom, code)
    if conv["whole"]:
        return True, ""
    return False, (
        f"“{(description or 'line')[:32]}”: {conv['billed_qty']:g} "
        f"{conv['billed_uom'] or 'PCS'} is {conv['pieces']:g} piece(s), which does "
        f"not divide into whole {conv['unit_type']}s "
        f"({conv['pieces_per_unit']:g} pieces each) — "
        f"{conv['remainder_pieces']:g} left over. Record the odd piece as a "
        f"shortage or an excess, or set a different unit type for this line.")
