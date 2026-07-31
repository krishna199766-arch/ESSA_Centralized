"""
Barcode + QR + label service.

Once a product is fully detailed, every product gets two permanent identifiers:
  * sku      — human code we assign (ESSA-00001)
  * barcode  — a scannable code. If the supplier already printed one we keep it;
               otherwise we mint an INTERNAL EAN-13 in the '2' in-store range
               (2 + 11-digit zero-padded product id + check digit). This is a
               real, scannable retail barcode that needs no GS1 registration.

Labels carry BOTH symbologies, side by side:

  * EAN-13 — what a 1D laser scanner and a retail POS expect. A laser scanner
    cannot read a 2D code at all, so dropping this would silently break every
    handheld already on the floor.
  * QR     — carries the whole product record (see qr_payload), so a phone camera
    or 2D imager gets every attribute in one scan, and still works with no network.

The QR payload is compact JSON, not a URL: a URL would need the server reachable
and would carry no attributes of its own. Scanning resolves the embedded `sku`
against the live database so details are always current, and falls back to the
attributes baked into the code when offline — which is the point of putting them
there. `parse_qr_payload` + `resolve` accept a scanned payload anywhere a bare
code is accepted, so nothing downstream needs to know which symbology was used.
"""
import io
import json
from .. import models

try:
    import barcode as _barcode
    from barcode.writer import SVGWriter
    _HAVE_BARCODE = True
except Exception:                      # pragma: no cover
    _HAVE_BARCODE = False

try:
    import segno as _segno
    _HAVE_QR = True
except Exception:                      # pragma: no cover
    _HAVE_QR = False


def _ean13_check_digit(d12: str) -> str:
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d12))
    return str((10 - s % 10) % 10)


def internal_ean13(product_id: int) -> str:
    """Deterministic internal EAN-13 for a product id (prefix 2 = in-store)."""
    body = "2" + f"{product_id:011d}"          # 12 digits
    return body + _ean13_check_digit(body)     # 13 digits


def _next_sku(db) -> str:
    n = db.query(models.Product).count() + 1
    # ensure uniqueness even if ids were deleted
    while db.query(models.Product).filter(models.Product.sku == f"ESSA-{n:05d}").first():
        n += 1
    return f"ESSA-{n:05d}"


def assign_identifiers(db, product) -> dict:
    """Give a product its sku + barcode if it doesn't have them. Keeps any
    supplier-printed barcode. Returns {sku, barcode, generated:[...]}. """
    generated = []
    if not product.sku:
        product.sku = _next_sku(db)
        generated.append("sku")
    if not product.barcode:
        if product.id is None:
            db.flush()
        product.barcode = internal_ean13(product.id)
        generated.append("barcode")
    db.flush()
    return {"sku": product.sku, "barcode": product.barcode, "generated": generated}


# ---- QR payload -------------------------------------------------------------
#
# Positional pipe-delimited, NOT JSON. This is a physical constraint, not a style
# preference: a QR's module count grows with its payload, and the printed module
# size is what decides whether a phone camera can read it. The same record as
# JSON runs ~262 bytes -> a 65-module (version 12) symbol -> 0.25mm per module on
# a 17mm label, below the ~0.33mm a phone needs in warehouse light. Dropping the
# key names and quotes roughly halves it to ~130 bytes -> ~45 modules -> 0.4mm+,
# which scans first time. JSON would be the nicer format if the label were bigger.
#
# `E1` is the schema tag. Fields are append-only: a new attribute goes on the END,
# never in the middle, so a label printed last year still decodes correctly.
QR_SCHEMA = 1
QR_TAG = "E1"

# payload position -> (dict key, Product attribute). ORDER IS THE FORMAT.
QR_ORDER = [
    ("id", "id"), ("sku", "sku"), ("bc", "barcode"), ("d", "description"),
    ("hsn", "hsn"), ("uom", "uom"), ("mrp", "mrp"), ("sp", "sale_price"),
    ("cat", "category"), ("sec", "category_section"), ("ty", "product_type"),
    ("sz", "size"), ("col", "color"), ("pat", "pattern"), ("fit", "fit"),
    ("mat", "material"), ("dsn", "design_no"),
]


def _num(v):
    """Trim pointless decimals — '499' not '499.0' saves bytes on every label."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _qesc(v):
    """Escape a value for the pipe-delimited QR payload."""
    return _num(v).replace("\\", "\\\\").replace("|", "\\|")


def _split_escaped(s):
    """Split on unescaped '|' only, honouring backslash escapes."""
    out, cur, esc = [], [], False
    for ch in s:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def qr_payload(p) -> str:
    """The complete product record, as the compact string printed into the QR.

    Blank attributes collapse to an empty field, so a sparsely-detailed product
    produces a small, low-density code."""
    vals = []
    for _, attr in QR_ORDER:
        v = getattr(p, attr, None)
        vals.append("" if v in (None, "") else _qesc(v))
    # trailing empties carry no information — drop them to shrink the symbol
    while vals and vals[-1] == "":
        vals.pop()
    return "|".join([QR_TAG] + vals)


def parse_qr_payload(text):
    """Decode a scanned payload into {key: value}, or None if it isn't one of ours.

    Accepts the compact `E1|...` form and the earlier JSON form, so labels printed
    before the format changed still scan. Deliberately forgiving: anything
    unrecognised returns None and the caller treats the input as a bare code."""
    if not text:
        return None
    s = str(text).strip()

    if s.startswith(QR_TAG + "|"):
        parts = _split_escaped(s)[1:]
        d = {"v": QR_SCHEMA}
        for (key, _), val in zip(QR_ORDER, parts):
            if val != "":
                d[key] = val
        return d

    if s.startswith("{") and s.endswith("}"):
        try:
            d = json.loads(s)
        except Exception:
            return None
        return d if isinstance(d, dict) and "v" in d else None

    return None


def qr_svg(payload: str, scale: int = 3) -> str:
    """Render a payload as an inline SVG QR code.

    Error correction 'M' (~15% recoverable) is the retail-label default: enough to
    survive a scuffed or partly peeled sticker without inflating the module count
    the way 'Q'/'H' would."""
    payload = str(payload or "")
    if _HAVE_QR and payload:
        try:
            qr = _segno.make(payload, error="m")
            # segno writes bytes, so this needs a BytesIO — a StringIO raises.
            # svgns=True keeps the xmlns, which the /qr.svg response requires and
            # which is harmless when the same markup is inlined into a label.
            buf = io.BytesIO()
            qr.save(buf, kind="svg", scale=scale, border=2,
                    xmldecl=False, svgns=True)
            return buf.getvalue().decode()
        except Exception:
            pass
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80">'
            '<rect width="80" height="80" fill="#eee"/>'
            '<text x="6" y="44" font-family="monospace" font-size="9">no QR lib</text></svg>')


def product_qr_svg(p, scale: int = 3) -> str:
    return qr_svg(qr_payload(p), scale=scale)


def resolve(db, code):
    """Fetch a product by QR payload, barcode, sku, or numeric id — the
    system-wide lookup.

    A scanned QR resolves by the identifiers inside it (id, then sku, then
    barcode) so the caller always gets the LIVE record rather than the snapshot
    printed on the label."""
    if code is None:
        return None
    code = str(code).strip()
    if not code:
        return None

    payload = parse_qr_payload(code)
    if payload:
        for getter in (lambda: db.get(models.Product, int(payload["id"]))
                       if str(payload.get("id", "")).isdigit() else None,
                       lambda: db.query(models.Product).filter(
                           models.Product.sku == payload.get("sku")).first()
                       if payload.get("sku") else None,
                       lambda: db.query(models.Product).filter(
                           models.Product.barcode == payload.get("bc")).first()
                       if payload.get("bc") else None):
            try:
                p = getter()
            except Exception:
                p = None
            if p:
                return p
        return None

    p = db.query(models.Product).filter(models.Product.barcode == code).first()
    if p:
        return p
    p = db.query(models.Product).filter(models.Product.sku == code).first()
    if p:
        return p
    if code.isdigit():
        p = db.get(models.Product, int(code))
        if p:
            return p
    # case-insensitive sku fallback
    return db.query(models.Product).filter(
        models.Product.sku.ilike(code)).first()


def barcode_svg(code: str) -> str:
    """Render a code as an SVG barcode. 13-digit numeric → EAN-13, else Code128.
    Falls back to a plain text SVG if the barcode lib is unavailable."""
    code = str(code or "").strip()
    if _HAVE_BARCODE and code:
        try:
            sym = "ean13" if (code.isdigit() and len(code) in (12, 13)) else "code128"
            klass = _barcode.get_barcode_class(sym)
            obj = klass(code, writer=SVGWriter())
            buf = io.BytesIO()
            obj.write(buf, options={"module_height": 12.0, "font_size": 8,
                                    "text_distance": 3.0, "quiet_zone": 2.0})
            svg = buf.getvalue().decode()
            # strip the XML prolog so it can be inlined inside HTML
            i = svg.find("<svg")
            return svg[i:] if i >= 0 else svg
        except Exception:
            pass
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40">' \
           f'<text x="4" y="24" font-family="monospace" font-size="14">{code}</text></svg>'


def _hesc(s) -> str:
    """Escape for HTML text — descriptions carry & and < from supplier bills."""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _label_card(p) -> str:
    bits = [b for b in [p.size, p.color, p.material] if b]
    sub = " · ".join(str(b) for b in bits)
    mrp = f"₹{p.mrp:g}" if p.mrp else ""
    return f"""
    <div class="label">
      <div class="head">
        <div class="txt">
          <div class="name">{_hesc((p.description or '')[:34])}</div>
          <div class="meta">{_hesc(sub)}</div>
          <div class="cat">{_hesc(p.category or '')}</div>
        </div>
        <div class="qr">{product_qr_svg(p, scale=3)}</div>
      </div>
      <div class="bc">{barcode_svg(p.barcode or p.sku or str(p.id))}</div>
      <div class="foot"><span class="sku">{_hesc(p.sku or '')}</span><span class="mrp">{mrp}</span></div>
    </div>"""


def labels_sheet(products) -> str:
    """A print-ready HTML sheet of product labels (browser → Print).

    Each label carries the QR (full record, for phone/2D scanning) and the EAN-13
    (for the existing 1D handhelds and the POS)."""
    cards = "".join(_label_card(p) for p in products)
    n = len(products)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Product labels ({n})</title>
<style>
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 10px; }}
  .bar {{ margin: 0 0 10px; }}
  .bar button {{ padding: 8px 16px; font-size: 14px; cursor: pointer; }}
  .sheet {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .label {{ width: 58mm; border: 1px solid #bbb; border-radius: 4px; padding: 6px 8px;
            box-sizing: border-box; page-break-inside: avoid; }}
  .name {{ font-size: 11px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .meta {{ font-size: 9px; color: #444; min-height: 11px; }}
  .cat {{ font-size: 8.5px; color: #666; font-family: monospace; min-height: 10px;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  /* Codes are STACKED, not side by side: sharing one row starves both — the QR
     drops below the ~0.33mm/module a phone camera needs, and the EAN-13 falls
     under ~80% of its nominal 37mm width, where laser scanners get unreliable.
     QR sits beside the text (square, 20mm), barcode gets the full label width. */
  .head {{ display: flex; align-items: flex-start; gap: 5px; }}
  .txt {{ flex: 1 1 auto; min-width: 0; }}
  .qr {{ flex: 0 0 20mm; }}
  .qr svg {{ width: 20mm; height: 20mm; display: block; }}
  .bc {{ margin-top: 1px; }}
  .bc svg {{ width: 100%; height: 36px; }}
  .foot {{ display: flex; justify-content: space-between; font-size: 10px; margin-top: 2px; }}
  .sku {{ font-family: monospace; }}
  .mrp {{ font-weight: 700; }}
  @media print {{ .bar {{ display: none; }} .label {{ border-color: #000; }} }}
</style></head><body>
  <div class="bar"><button onclick="window.print()">🖨 Print {n} label{'s' if n!=1 else ''}</button></div>
  <div class="sheet">{cards}</div>
</body></html>"""
