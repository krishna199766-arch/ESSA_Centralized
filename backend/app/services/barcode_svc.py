"""
QR + label service.

A product has ONE identifier of ours: the `sku` (ESSA-00001). It is what the SKU
column shows, what prints under the QR on a label, and what a scan resolves to.

`Product.barcode` still exists but is no longer ours to issue. It holds the code
the SUPPLIER printed, when they print one, so `inventory.match_product` can key a
re-buy onto the product it already created — which is what stops one item's
weighted-average cost splitting across two records. It is never presented as this
product's code, and nothing generates it. We used to mint an internal EAN-13 there
so the label could carry a 1D stripe; once the label went QR-only that second
number bought nothing and gave two answers to "what is this product's code".

Labels and the Inventory list are QR-only:

  * QR — carries the whole product record (see qr_payload), so a phone camera or
    2D imager gets every attribute in one scan, and still works with no network.
    Owning the label alone lets it print bigger, which is what makes it scan
    first time in warehouse light.

The trade-off this accepts: a pure 1D laser handheld cannot read a 2D code at
all, so scanners that aren't 2D imagers can only be used by keying in the printed
SKU. `barcode_svg` is kept (and still served at /barcode.svg) for anyone who
needs a stripe back — it renders whatever code it is handed.

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
    """Deterministic internal EAN-13 for a product id (prefix 2 = in-store).

    No longer issued — kept so a barcode minted by an earlier build can still be
    recognised as ours rather than mistaken for a supplier's code."""
    body = "2" + f"{product_id:011d}"          # 12 digits
    return body + _ean13_check_digit(body)     # 13 digits


def _next_sku(db) -> str:
    n = db.query(models.Product).count() + 1
    # ensure uniqueness even if ids were deleted
    while db.query(models.Product).filter(models.Product.sku == f"ESSA-{n:05d}").first():
        n += 1
    return f"ESSA-{n:05d}"


def assign_identifiers(db, product) -> dict:
    """Give a product its SKU. Returns {sku, barcode, generated:[...]}.

    The SKU is the only identifier we issue. We used to also mint an internal
    EAN-13 so the label could carry a 1D stripe; the label is QR-only now, and
    the QR resolves on `id`/`sku`, so a second number of our own bought nothing
    and gave two answers to "what is this product's code".

    `product.barcode` is untouched here on purpose. It holds the code the
    SUPPLIER printed, when they print one, and `inventory.match_product` keys a
    re-buy on it — which is what keeps one product's weighted-average cost in one
    place. It is never shown as our code and never generated."""
    generated = []
    if not product.sku:
        product.sku = _next_sku(db)
        generated.append("sku")
    if product.id is None:
        db.flush()
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


# ---- per-piece (unit) labels -------------------------------------------------
#
# A third tag, `EU1`, for the code on an individual garment. It carries its own
# unit code AND the sku it belongs to, so one scan answers both "which piece" and
# "which product" — and `resolve()` below returns the product for it, which is what
# keeps every existing scan point (GRN linking, lookup, outward) working when
# someone scans a piece label instead of a shelf one.
UNIT_TAG = "EU1"
UNIT_ORDER = [
    ("u", "code"), ("sku", "_sku"), ("id", "product_id"), ("d", "_description"),
    ("sz", "_size"), ("col", "_color"), ("mrp", "_mrp"),
]


def _unit_field(u, attr):
    if not attr.startswith("_"):
        return getattr(u, attr, None)
    p = u.product
    return getattr(p, attr[1:], None) if p else None


def unit_qr_payload(u) -> str:
    """What one garment's QR carries: which piece it is, which SKU it belongs to,
    and enough of the record to read it with no network."""
    vals = []
    for _, attr in UNIT_ORDER:
        v = _unit_field(u, attr)
        vals.append("" if v in (None, "") else _qesc(v))
    while vals and vals[-1] == "":
        vals.pop()
    return "|".join([UNIT_TAG] + vals)


def parse_unit_payload(text):
    """Decode a scanned per-piece payload, or None if it isn't one."""
    if not text:
        return None
    s = str(text).strip()
    if not s.startswith(UNIT_TAG + "|"):
        return None
    parts = _split_escaped(s)[1:]
    d = {"kind": "unit"}
    for (key, _), val in zip(UNIT_ORDER, parts):
        if val != "":
            d[key] = val
    return d


def unit_qr_svg(u, scale: int = 3) -> str:
    return qr_svg(unit_qr_payload(u), scale=scale)


def unit_qr_png(u, scale: int = 6) -> bytes:
    return qr_png(unit_qr_payload(u), scale=scale)


def _resolve_unit_row(db, code):
    """The ProductUnit a scanned piece label refers to, or None."""
    payload = parse_unit_payload(code)
    if payload:
        code = payload.get("u") or ""
    code = str(code or "").strip()
    if not code:
        return None
    return db.query(models.ProductUnit).filter(
        models.ProductUnit.code.ilike(code)).first()


def _unit_label_card(u) -> str:
    p = u.product
    bits = [b for b in [getattr(p, "size", None), getattr(p, "color", None),
                        getattr(p, "material", None)] if b]
    mrp = f"₹{p.mrp:g}" if p and p.mrp else ""
    return f"""
    <div class="label">
      <div class="head">
        <div class="txt">
          <div class="name">{_hesc(((p.description if p else '') or '')[:34])}</div>
          <div class="meta">{_hesc(' · '.join(str(b) for b in bits))}</div>
          <div class="cat">{_hesc((p.category if p else '') or '')}</div>
          <div class="foot"><span class="sku">{_hesc(p.sku if p else '')}</span><span class="mrp">{mrp}</span></div>
        </div>
        <div class="qr">
          {unit_qr_svg(u, scale=3)}
          <div class="code">{_hesc(u.code)}</div>
        </div>
      </div>
    </div>"""


def unit_labels_sheet(units) -> str:
    """Print-ready sheet of PER-PIECE labels.

    Same shape as the SKU label so a picker sees one kind of garment tag, but the
    code under each QR is that piece's own — which is the whole point: eight
    garments off one SKU leave the warehouse with eight distinguishable tags
    instead of eight copies of the same one."""
    return _garment_sheet("Piece labels", "piece label",
                          [_unit_label_card(u) for u in units])


# ---- bundle labels ----------------------------------------------------------
#
# A carton's label carries its OWN tag, `EB1`, not the product one. The two answer
# different questions — "which box is this?" versus "which garment is this?" — and
# a scanner that cannot tell them apart would happily dispatch a carton as if it
# were one shirt. The tag makes that impossible: `resolve()` below returns products
# and refuses a bundle payload outright, so the mistake surfaces as "that is a
# bundle code" instead of silently moving the wrong stock.
BUNDLE_TAG = "EB1"
BUNDLE_PREFIX = "ESSA-B-"


def _is_bundle_code(code) -> bool:
    return str(code or "").strip().upper().startswith(BUNDLE_PREFIX)

BUNDLE_ORDER = [
    ("code", "code"), ("d", "description"), ("qty", "qty"), ("uom", "uom"),
    ("items", "item_count"), ("grn", "grn_no"), ("inv", "invoice_number"),
    ("loc", "location"),
]


def bundle_qr_payload(b) -> str:
    """What a carton's QR carries: enough to identify the box on a rack with no
    network — what is in it, how many, and which receipt it came from."""
    vals = []
    for _, attr in BUNDLE_ORDER:
        v = getattr(b, attr, None)
        vals.append("" if v in (None, "") else _qesc(v))
    while vals and vals[-1] == "":
        vals.pop()
    return "|".join([BUNDLE_TAG] + vals)


def bundle_qr_svg(b, scale: int = 3) -> str:
    return qr_svg(bundle_qr_payload(b), scale=scale)


def bundle_qr_png(b, scale: int = 6) -> bytes:
    return qr_png(bundle_qr_payload(b), scale=scale)


def parse_bundle_payload(text):
    """Decode a scanned BUNDLE payload, or None if it isn't one."""
    if not text:
        return None
    s = str(text).strip()
    if not s.startswith(BUNDLE_TAG + "|"):
        return None
    parts = _split_escaped(s)[1:]
    d = {"kind": "bundle"}
    for (key, _), val in zip(BUNDLE_ORDER, parts):
        if val != "":
            d[key] = val
    return d


def parse_qr_payload(text):
    """Decode a scanned PRODUCT payload into {key: value}, or None if it isn't one.

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


def qr_png(payload: str, scale: int = 6) -> bytes:
    """The same QR as a PNG. Returns b"" when the QR library is unavailable.

    Exists for the warehouse phone app: React Native's <Image> renders PNG/JPEG
    and cannot draw an SVG without pulling in a native renderer, so the label a
    picker checks on screen has to arrive as raster. Same payload and the same
    'M' error correction as the printed label — it is the identical code, only
    rendered for a screen rather than a sticker."""
    payload = str(payload or "")
    if _HAVE_QR and payload:
        try:
            buf = io.BytesIO()
            _segno.make(payload, error="m").save(buf, kind="png", scale=scale, border=2)
            return buf.getvalue()
        except Exception:
            pass
    return b""


def product_qr_png(p, scale: int = 6) -> bytes:
    return qr_png(qr_payload(p), scale=scale)


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

    # a carton is not a garment: never let a bundle label resolve to one of the
    # products inside it, however tempting the fallbacks below would make it
    if parse_bundle_payload(code) or _is_bundle_code(code):
        return None

    # a per-piece code IS a garment — the one whose SKU it hangs off. Resolving it
    # here is what lets every existing scan point (GRN linking, lookup, outward)
    # accept a label off an individual item without knowing units exist.
    unit = _resolve_unit_row(db, code)
    if unit:
        return unit.product

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
    # our SKU, never p.barcode — that is the supplier's number, and printing it
    # under our QR would put someone else's code on our label
    code = p.sku or str(p.id)
    return f"""
    <div class="label">
      <div class="head">
        <div class="txt">
          <div class="name">{_hesc((p.description or '')[:34])}</div>
          <div class="meta">{_hesc(sub)}</div>
          <div class="cat">{_hesc(p.category or '')}</div>
          <div class="foot"><span class="sku">{_hesc(p.sku or '')}</span><span class="mrp">{mrp}</span></div>
        </div>
        <div class="qr">
          {product_qr_svg(p, scale=3)}
          <div class="code">{_hesc(code)}</div>
        </div>
      </div>
    </div>"""


def bundle_labels_sheet(bundles) -> str:
    """Print-ready sheet of CARTON labels — one per bundle, bigger than a garment
    tag because it is read across a rack rather than held in the hand.

    Deliberately shows the mix (10 S · 20 M · 15 L) rather than a single size: the
    whole point of the carton label is to say what is inside without opening it."""
    def card(b):
        prods = b.products
        mix = " · ".join(
            f"{(s.qty or 0):g} {s.variant_label or '—'}"
            for s in (b.line.splits if b.line and b.line.is_split else [])) \
            or f"{(b.qty or 0):g} {_hesc(b.uom or 'PCS')}"
        skus = ", ".join(p.sku for p in prods if p.sku)
        return f"""
    <div class="blabel">
      <div class="top">
        <div class="txt">
          <div class="bname">{_hesc((b.description or '')[:40])}</div>
          <div class="qty">{(b.qty or 0):g} {_hesc(b.uom or 'PCS')}
            <span class="items">· {b.item_count} item{'' if b.item_count == 1 else 's'}</span></div>
          <div class="mix">{_hesc(mix)}</div>
          <div class="grn">GRN {_hesc(b.grn_no or '—')} · Inv {_hesc(b.invoice_number or '—')}</div>
          <div class="grn">{_hesc(b.supplier.name if b.supplier else '')}</div>
        </div>
        <div class="bqr">
          {bundle_qr_svg(b, scale=3)}
          <div class="bcode">{_hesc(b.code)}</div>
        </div>
      </div>
      <div class="loc">LOCATION: {_hesc(b.location or '________________')}</div>
      <div class="skus">{_hesc(skus[:90])}</div>
    </div>"""

    cards = "".join(card(b) for b in bundles)
    n = len(bundles)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Bundle labels ({n})</title>
<style>
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 10px; }}
  .bar {{ margin: 0 0 10px; }}
  .bar button {{ padding: 8px 16px; font-size: 14px; cursor: pointer; }}
  .sheet {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .blabel {{ width: 99mm; border: 2px solid #000; border-radius: 5px; padding: 8px 10px;
             box-sizing: border-box; page-break-inside: avoid; }}
  .top {{ display: flex; align-items: stretch; gap: 8px; }}
  .txt {{ flex: 1 1 auto; min-width: 0; }}
  .bname {{ font-size: 15px; font-weight: 700; }}
  .qty {{ font-size: 20px; font-weight: 800; margin-top: 2px; }}
  .items {{ font-size: 12px; font-weight: 600; color: #444; }}
  .mix {{ font-size: 10px; color: #333; margin-top: 3px; }}
  .grn {{ font-size: 10px; color: #555; margin-top: 2px; }}
  /* 34mm: a carton label is scanned from further away than a garment tag, and the
     payload is smaller, so the modules can afford to be much bigger */
  .bqr {{ flex: 0 0 34mm; }}
  .bqr svg {{ width: 34mm; height: 34mm; display: block; }}
  .bcode {{ font-family: monospace; font-size: 10px; text-align: center; font-weight: 700; }}
  .loc {{ margin-top: 6px; border-top: 1px dashed #999; padding-top: 4px;
          font-size: 12px; font-weight: 700; letter-spacing: .5px; }}
  .skus {{ font-family: monospace; font-size: 8px; color: #666; margin-top: 3px;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  @media print {{ .bar {{ display: none; }} }}
</style></head><body>
  <div class="bar"><button onclick="window.print()">🖨 Print {n} bundle label{'s' if n != 1 else ''}</button></div>
  <div class="sheet">{cards}</div>
</body></html>"""


def labels_sheet(products) -> str:
    """A print-ready HTML sheet of product labels (browser → Print).

    Each label carries the QR (full record, for phone/2D scanning) plus the code
    in human-readable digits underneath, for keying in by hand."""
    return _garment_sheet("Product labels", "label",
                          [_label_card(p) for p in products])


def _garment_sheet(title, noun, cards) -> str:
    """The shared garment-tag sheet: same stylesheet whether the code under each QR
    is a SKU or one piece's own, so the warehouse only ever learns one label."""
    cards = "".join(cards)
    n = len(cards.split('<div class="label">')) - 1
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title} ({n})</title>
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
  /* The QR is the only code on the label, so it takes the space the EAN-13 stripe
     used to occupy: 26mm square instead of 20mm. At ~45 modules that is ~0.55mm
     per module, comfortably above the ~0.33mm a phone camera needs in warehouse
     light. Text column sits beside it and carries sku/MRP at its foot — hence
     `stretch`, so that column matches the QR's height and `margin-top:auto` can
     pin sku/MRP to the bottom of the label. */
  .head {{ display: flex; align-items: stretch; gap: 6px; }}
  .txt {{ flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; }}
  .qr {{ flex: 0 0 26mm; }}
  .qr svg {{ width: 26mm; height: 26mm; display: block; }}
  .code {{ font-family: monospace; font-size: 8px; text-align: center; letter-spacing: .3px; }}
  .foot {{ display: flex; justify-content: space-between; font-size: 10px; margin-top: auto;
           padding-top: 4px; }}
  .sku {{ font-family: monospace; }}
  .mrp {{ font-weight: 700; }}
  @media print {{ .bar {{ display: none; }} .label {{ border-color: #000; }} }}
</style></head><body>
  <div class="bar"><button onclick="window.print()">🖨 Print {n} {noun}{'s' if n != 1 else ''}</button></div>
  <div class="sheet">{cards}</div>
</body></html>"""
