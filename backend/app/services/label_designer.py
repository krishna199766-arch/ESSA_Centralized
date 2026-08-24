"""
Label Designer — the layout half of labelling, kept apart from the data half.

Three things live here and nothing else does:

  1. FIELDS — the catalogue of what a label MAY show. This is the palette the
     designer drags from, and it is also the only list that decides what a
     template is allowed to reference. Adding a printable attribute means one
     entry here; the designer, the properties panel, the preview and the print
     sheet all pick it up without another edit.

  2. `context()` — what one product (or one physical piece) actually says for
     each of those fields, resolved fresh at PRINT time. A template stores
     `{"field": "mrp", "x": 4, "y": 26}`, never "₹1,499", so re-pricing a
     product re-prices its labels and nobody re-opens the designer.

  3. `render_label` / `render_sheet` — the design plus the data, as print-ready
     HTML in millimetres.

Millimetres everywhere. A label is a physical object cut to a physical size, the
person checking the output holds a ruler against it, and the printer's own unit
is mm — so px would be a translation layer with nothing on the other side of it.
Font sizes are the exception and stay in points, which is what a font size is.

The QR keeps the guarantee it has in barcode_svc: a symbol printed too small is
not a slightly worse label, it is one no scanner will read. `qr_warning()` turns
the element's own box size into the module size it implies and says so in the
designer, at design time, rather than after a roll of stickers has been printed.
"""
import datetime as dt

from .. import models
from ..config import COMPANY_NAME
from . import barcode_svc
from . import dates as date_svc
from . import stock_view


# ---------------------------------------------------------------------------
#  The palette — what a label may carry
# ---------------------------------------------------------------------------
#: kind drives BOTH the designer (which properties are offered) and the renderer
#: (how the element is drawn):
#:   text   — a value off the product, drawn as a line of type
#:   qr     — the 2D symbol; the element's box is the printed size of the symbol
#:   barcode— a 1D stripe of the SKU, for sites with laser handhelds
#:   static — text the template itself owns (company name, custom text)
#:   image  — a data-URI picture the template owns (logo)
#:
#: `w`/`h` are the millimetres a freshly dropped element gets. They are starting
#: points, not constraints — everything is resizable afterwards.
FIELDS = [
    {"key": "qr_code", "label": "QR Code", "group": "Code", "kind": "qr",
     "w": 16, "h": 16, "hint": "The whole product record — a scan works with no network"},
    {"key": "barcode", "label": "Barcode (1D)", "group": "Code", "kind": "barcode",
     "w": 30, "h": 10, "hint": "The SKU as a stripe, for laser handhelds that cannot read a QR"},
    {"key": "piece_code", "label": "Piece Code", "group": "Code", "kind": "text",
     "w": 24, "h": 4, "size": 7, "hint": "ESSA-00008-003 — which garment. Blank on a SKU label"},
    {"key": "sku", "label": "SKU", "group": "Code", "kind": "text",
     "w": 24, "h": 4, "size": 8, "bold": True},

    {"key": "product_name", "label": "Product Name", "group": "Product", "kind": "text",
     "w": 42, "h": 5, "size": 9, "bold": True},
    {"key": "category", "label": "Category", "group": "Product", "kind": "text",
     "w": 30, "h": 4, "size": 7},
    {"key": "size", "label": "Size", "group": "Product", "kind": "text",
     "w": 16, "h": 4, "size": 8, "bold": True},
    {"key": "color", "label": "Colour", "group": "Product", "kind": "text",
     "w": 20, "h": 4, "size": 8},
    {"key": "material", "label": "Material", "group": "Product", "kind": "text",
     "w": 24, "h": 4, "size": 8},
    {"key": "pattern", "label": "Pattern", "group": "Product", "kind": "text",
     "w": 20, "h": 4, "size": 8},
    {"key": "fit", "label": "Fit", "group": "Product", "kind": "text",
     "w": 20, "h": 4, "size": 8},
    {"key": "product_type", "label": "Type", "group": "Product", "kind": "text",
     "w": 20, "h": 4, "size": 8},
    {"key": "brand", "label": "Brand", "group": "Product", "kind": "text",
     "w": 22, "h": 4, "size": 8},
    {"key": "style", "label": "Style", "group": "Product", "kind": "text",
     "w": 22, "h": 4, "size": 8},
    {"key": "sleeve", "label": "Sleeve", "group": "Product", "kind": "text",
     "w": 18, "h": 4, "size": 8},
    {"key": "design_no", "label": "Design No", "group": "Product", "kind": "text",
     "w": 20, "h": 4, "size": 8},
    {"key": "hsn", "label": "HSN", "group": "Product", "kind": "text",
     "w": 18, "h": 4, "size": 7},
    {"key": "uom", "label": "Unit", "group": "Product", "kind": "text",
     "w": 16, "h": 4, "size": 7,
     "hint": "PCS / PAIR — with the pieces in one of them when that is more than one"},

    {"key": "mrp", "label": "MRP", "group": "Price", "kind": "text",
     "w": 20, "h": 5, "size": 9, "bold": True},
    {"key": "sale_price", "label": "Sale Price", "group": "Price", "kind": "text",
     "w": 20, "h": 5, "size": 9, "bold": True},
    {"key": "discount_pct", "label": "Discount %", "group": "Price", "kind": "text",
     "w": 16, "h": 4, "size": 8},

    {"key": "batch", "label": "Batch", "group": "Receipt", "kind": "text",
     "w": 24, "h": 4, "size": 7,
     "hint": "The supplier bill this stock came in on — its batch, in this warehouse"},
    {"key": "grn_no", "label": "GRN No", "group": "Receipt", "kind": "text",
     "w": 24, "h": 4, "size": 7},
    {"key": "received_date", "label": "Received Date", "group": "Receipt", "kind": "text",
     "w": 22, "h": 4, "size": 7},
    {"key": "supplier", "label": "Supplier", "group": "Receipt", "kind": "text",
     "w": 34, "h": 4, "size": 7},

    {"key": "company_name", "label": "Company Name", "group": "Fixed", "kind": "static",
     "w": 42, "h": 5, "size": 9, "bold": True, "align": "center"},
    {"key": "custom_text", "label": "Custom Text", "group": "Fixed", "kind": "static",
     "w": 30, "h": 4, "size": 8,
     "hint": "Whatever this template should say — a care instruction, a season code"},
    {"key": "logo", "label": "Logo", "group": "Fixed", "kind": "image",
     "w": 16, "h": 8, "hint": "An image pasted into the template — stored with the design"},
    {"key": "line", "label": "Divider Line", "group": "Fixed", "kind": "line",
     "w": 42, "h": 0.4},
]

FIELD_BY_KEY = {f["key"]: f for f in FIELDS}

#: Fonts offered in the properties panel. Deliberately short and deliberately
#: all system fonts: a label sheet is rendered by whatever browser the warehouse
#: prints from, and a webfont that fails to load re-flows every element on it.
FONTS = [
    {"key": "Arial, Helvetica, sans-serif", "label": "Arial (sans)"},
    {"key": '"Courier New", monospace', "label": "Courier (mono)"},
    {"key": '"Times New Roman", Times, serif', "label": "Times (serif)"},
    {"key": '"Segoe UI", Roboto, sans-serif', "label": "Segoe UI"},
    {"key": "Verdana, Geneva, sans-serif", "label": "Verdana"},
]

#: Label stock this warehouse is likely to hold, in millimetres. These are
#: SHORTCUTS and nothing more — any size between MIN_MM and MAX_MM is designable
#: by typing it, and the designer's size box says so. They exist because "50 × 35"
#: is a roll someone buys, and picking the roll you loaded is faster and safer
#: than typing two numbers and hoping they match the die-cut.
SIZES = [
    {"key": "38x25", "w": 38, "h": 25, "label": "38 × 25 — small price tag"},
    {"key": "50x25", "w": 50, "h": 25, "label": "50 × 25 — slim tag"},
    {"key": "50x35", "w": 50, "h": 35, "label": "50 × 35 — standard garment tag"},
    {"key": "60x40", "w": 60, "h": 40, "label": "60 × 40 — roomy tag"},
    {"key": "75x50", "w": 75, "h": 50, "label": "75 × 50 — big tag / bin label"},
    {"key": "100x50", "w": 100, "h": 50, "label": "100 × 50 — carton strip"},
    {"key": "100x75", "w": 100, "h": 75, "label": "100 × 75 — carton"},
    {"key": "100x100", "w": 100, "h": 100, "label": "100 × 100 — square carton"},
    {"key": "100x150", "w": 100, "h": 150, "label": "100 × 150 — shipping (4 × 6 in)"},
    {"key": "105x148", "w": 105, "h": 148, "label": "105 × 148 — A6 sheet"},
]

#: The designable range. The floor is the smallest sticker a QR and one line of
#: type both fit on; the ceiling is A4's long edge, past which a "label" is a
#: poster and the sheet renderer is the wrong tool.
MIN_MM = 10.0
MAX_MM = 300.0


# ---------------------------------------------------------------------------
#  Values
# ---------------------------------------------------------------------------
def _money(v):
    """₹1,499 — Indian grouping, no decimals when there are none to show."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return ""
    whole = int(abs(n))
    frac = abs(n) - whole
    s = str(whole)
    if len(s) > 3:                      # 12,34,567 — last three, then pairs
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    if frac > 0.004:
        s += f"{frac:.2f}"[1:]
    return ("-" if n < 0 else "") + "₹" + s


def _unit_text(p):
    """"PAIR · 2 pcs" when one of these is more than one thing, else "PCS"."""
    code = getattr(p, "unit_type", None) or getattr(p, "uom", None) or ""
    per = float(getattr(p, "pieces_per_unit", 1) or 1)
    if code and per > 1:
        return f"{code} · {per:g} pcs"
    return code


def receipt_index(db, product_ids):
    """{product_id: {grn_no, batch, received_date, supplier}} for a whole sheet.

    One query for the batch rather than one per label: a sheet of 400 piece
    labels is 400 lookups of the same handful of receipts otherwise.

    The LATEST posted receipt wins. A product re-bought three times has three
    batches behind one SKU and no label can name all three — the newest is the
    stock most likely being labelled today, and the piece code is what actually
    traces a garment to its own GRN."""
    ids = [i for i in set(product_ids or []) if i]
    if not ids:
        return {}
    rows = db.query(models.PurchaseLine, models.Purchase).join(
        models.Purchase, models.PurchaseLine.purchase_id == models.Purchase.id).filter(
        models.PurchaseLine.product_id.in_(ids),
        models.Purchase.status == "posted").order_by(models.Purchase.id).all()
    # splits carry the product for a broken-up line, so a variant has a receipt too
    srows = db.query(models.PurchaseLineSplit, models.PurchaseLine, models.Purchase).join(
        models.PurchaseLine, models.PurchaseLineSplit.line_id == models.PurchaseLine.id).join(
        models.Purchase, models.PurchaseLine.purchase_id == models.Purchase.id).filter(
        models.PurchaseLineSplit.product_id.in_(ids),
        models.Purchase.status == "posted").order_by(models.Purchase.id).all()

    out = {}
    def put(pid, pur):
        out[pid] = {
            "grn_no": pur.grn_no or "",
            "batch": pur.invoice_number or pur.grn_no or "",
            # Printed on the tag, so it reads the way the shop floor reads a
            # date. The invoice date is STORED ISO; the posting fallback is
            # already DD-MM-YYYY, and a tag that used one form for some receipts
            # and the other for the rest is worse than either.
            "received_date": date_svc.to_display(pur.invoice_date) or (
                pur.posted_at.strftime(date_svc.DISPLAY) if pur.posted_at else ""),
            "supplier": pur.supplier.name if pur.supplier else "",
        }
    for line, pur in rows:                       # ordered by purchase id, so the
        put(line.product_id, pur)                # last write is the newest receipt
    for split, _line, pur in srows:
        put(split.product_id, pur)
    return out


def context(product, unit=None, receipt=None):
    """Every field's value for one label, as strings ready to print.

    `unit` is the individual garment when a per-piece label is being printed:
    it changes the QR (that piece's own code, which still resolves to this
    product) and fills `piece_code`. Everything else is the product's, because
    the piece IS one of them."""
    p = product
    r = receipt or {}
    return {
        "qr_code": barcode_svc.unit_qr_payload(unit) if unit else barcode_svc.qr_payload(p),
        "barcode": p.sku or p.barcode or str(p.id or ""),
        "piece_code": unit.code if unit else "",
        "sku": p.sku or "",
        # what the article IS, which is its category — see stock_view.display_name.
        # A garment tag reading "TISSOT Lycra" names a mill, not a product.
        "product_name": stock_view.display_name(p),
        "category": p.category or "",
        "size": p.size or "",
        "color": p.color or "",
        "material": p.material or "",
        "pattern": p.pattern or "",
        "fit": p.fit or "",
        "product_type": p.product_type or "",
        "brand": p.brand or "",
        "style": p.style or "",
        "sleeve": p.sleeve or "",
        "design_no": p.design_no or "",
        "hsn": p.hsn or "",
        "uom": _unit_text(p),
        "mrp": _money(p.mrp) if p.mrp else "",
        "sale_price": _money(p.sale_price) if p.sale_price else "",
        "discount_pct": f"{p.sale_discount_pct:g}" if p.sale_discount_pct else "",
        "batch": r.get("batch", ""),
        "grn_no": r.get("grn_no", ""),
        "received_date": r.get("received_date", ""),
        "supplier": r.get("supplier", "") or (
            p.primary_supplier.name if p.primary_supplier else ""),
        "company_name": COMPANY_NAME,
    }


#: What the canvas shows while nobody has picked a real product. Same keys as
#: `context`, so the designer is drawing the same thing the printer will.
SAMPLE = {
    "qr_code": "E1|8|ESSA-00008||WOMEN'S COTTON T-SHIRT|6109|PCS|1499|1199|"
               "T-SHIRT|LADIES|ROUND NECK|M|Red|Plain|Regular Fit|Cotton|D-2291",
    "barcode": "ESSA-00008", "piece_code": "ESSA-00008-003", "sku": "ESSA-00008",
    # the name IS the category, as it will be on a real product — the canvas has
    # to draw what the printer will, not something tidier
    "product_name": "T-SHIRT / LADIES", "category": "T-SHIRT / LADIES",
    "size": "M", "color": "Red", "material": "Cotton", "pattern": "Plain",
    "fit": "Regular Fit", "product_type": "Round Neck", "brand": "Taqua",
    "style": "Casual", "sleeve": "Half", "design_no": "D-2291", "hsn": "6109",
    "uom": "PCS", "mrp": "₹1,499", "sale_price": "₹1,199", "discount_pct": "20",
    "batch": "INV-4471", "grn_no": "GRN-00031", "received_date": "12-08-2026",
    "supplier": "Sri Krishna Textiles", "company_name": COMPANY_NAME,
}


# ---------------------------------------------------------------------------
#  Elements — validation on the way in
# ---------------------------------------------------------------------------
ALIGNS = ("left", "center", "right")


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def normalise_elements(raw, width_mm, height_mm):
    """Validate what the designer sent, and drop what makes no sense.

    Positions are clamped to the label rather than rejected: a drag that ends
    two millimetres past the edge is a slip, and snapping it back is what the
    person meant. An element naming a field that does not exist IS rejected —
    it would print as a permanent blank nobody could explain."""
    out = []
    for i, e in enumerate(raw or []):
        if not isinstance(e, dict):
            continue
        field = str(e.get("field") or "")
        spec = FIELD_BY_KEY.get(field)
        if not spec:
            continue
        w = _clamp(_f(e.get("w"), spec.get("w", 20)), 0.5, width_mm)
        h = _clamp(_f(e.get("h"), spec.get("h", 4)), 0.2, height_mm)
        out.append({
            "id": str(e.get("id") or f"e{i + 1}"),
            "field": field,
            "x": round(_clamp(_f(e.get("x")), 0, max(0, width_mm - w)), 2),
            "y": round(_clamp(_f(e.get("y")), 0, max(0, height_mm - h)), 2),
            "w": round(w, 2), "h": round(h, 2),
            "size": round(_clamp(_f(e.get("size"), spec.get("size", 8)), 3, 72), 1),
            "bold": bool(e.get("bold", spec.get("bold", False))),
            "italic": bool(e.get("italic", False)),
            "align": e.get("align") if e.get("align") in ALIGNS else spec.get("align", "left"),
            "font": str(e.get("font") or ""),          # blank = the template's font
            "color": str(e.get("color") or "#000000"),
            "prefix": str(e.get("prefix") or ""),
            "suffix": str(e.get("suffix") or ""),
            "uppercase": bool(e.get("uppercase", False)),
            "border": bool(e.get("border", False)),
            "visible": bool(e.get("visible", True)),
            "locked": bool(e.get("locked", False)),
            "text": str(e.get("text") or ""),          # the words this box owns
            # An element whose words the TEMPLATE decides, not the product. Set by
            # typing over a field on the canvas. It is stored as a flag beside the
            # field rather than by swapping the field for custom_text, so the box
            # remembers what it used to show and un-ticking gives the live value
            # back — and so the designer can say, in words, that this one line has
            # stopped following the database.
            "use_text": bool(e.get("use_text", False)),
            "src": str(e.get("src") or ""),            # logo data URI
            "z": int(_f(e.get("z"), i + 1)),
        })
    out.sort(key=lambda e: e["z"])
    for n, e in enumerate(out, 1):                     # re-number, no gaps
        e["z"] = n
    return out


# ---------------------------------------------------------------------------
#  Rendering
# ---------------------------------------------------------------------------
def _hesc(s) -> str:
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def qr_warning(box_mm, payload=None):
    """"0.28mm per module — below the 0.33mm a phone camera needs" or "".

    The one check that cannot be left to the eye: a QR element scaled down to
    fit a crowded label still LOOKS like a QR on screen and in the PDF, and only
    stops working once it is on a garment in warehouse light. See
    barcode_svc.qr_module_size_mm."""
    mm = barcode_svc.qr_module_size_mm(payload or SAMPLE["qr_code"], _f(box_mm))
    if not mm:
        return ""
    if mm < 0.33:
        return (f"{mm:.2f}mm per module — under the ~0.33mm a phone camera reads in "
                f"warehouse light. Make the QR bigger.")
    if mm < 0.4:
        return (f"{mm:.2f}mm per module — readable, but with nothing to spare for "
                f"thermal bleed or a scuffed sticker.")
    return ""


def _element_html(el, values, template_font):
    """One positioned element. Absolute mm from the label's top-left."""
    if not el.get("visible", True):
        return ""
    spec = FIELD_BY_KEY.get(el["field"])
    if not spec:
        return ""
    kind = spec["kind"]
    box = (f'position:absolute;left:{el["x"]}mm;top:{el["y"]}mm;'
           f'width:{el["w"]}mm;height:{el["h"]}mm;'
           + ("border:.2mm solid #000;box-sizing:border-box;" if el.get("border") else ""))

    if kind == "qr":
        payload = values.get("qr_code") or ""
        # white behind the symbol: the quiet zone is part of the code, and a
        # tinted label stock creeping into it is a code a scanner cannot find
        return (f'<div style="{box}background:#fff">'
                f'{barcode_svc.qr_svg(payload, scale=3)}</div>')
    if kind == "barcode":
        return (f'<div style="{box}background:#fff">'
                f'{barcode_svc.barcode_svg(values.get("barcode") or "")}</div>')
    if kind == "line":
        return f'<div style="{box}background:{_hesc(el.get("color") or "#000")}"></div>'
    if kind == "image":
        src = el.get("src") or ""
        if not src.startswith("data:image/"):
            return ""
        return (f'<div style="{box}"><img src="{_hesc(src)}" alt="" '
                f'style="width:100%;height:100%;object-fit:contain"></div>')

    # text and static
    if el.get("use_text") or (kind == "static" and el["field"] == "custom_text"):
        val = el.get("text") or ""      # words the template owns — see `use_text`
    else:
        val = values.get(el["field"], "")
    if val == "" and not el.get("prefix"):
        return ""                       # an empty attribute prints nothing at all
    if el.get("uppercase"):
        val = str(val).upper()
    text = f'{el.get("prefix", "")}{val}{el.get("suffix", "")}'
    # clipped, never wrapped: only the browser knows how wide a supplier's
    # description renders, and a name that overflows takes the label with it
    style = (f'{box}font-family:{el.get("font") or template_font};'
             f'font-size:{el["size"]}pt;line-height:{el["h"]}mm;'
             f'font-weight:{700 if el.get("bold") else 400};'
             + ("font-style:italic;" if el.get("italic") else "")
             + f'text-align:{el.get("align", "left")};color:{_hesc(el.get("color") or "#000")};'
             f'overflow:hidden;white-space:nowrap;text-overflow:ellipsis;')
    return f'<div style="{style}">{_hesc(text)}</div>'


def render_label(tpl, values) -> str:
    """One label — the template's design, filled with one item's values."""
    font = tpl.font or "Arial, Helvetica, sans-serif"
    els = "".join(_element_html(e, values, font)
                  for e in (tpl.elements or []))
    border = "border:.25mm solid #000;" if tpl.border else "border:.2mm dashed #ccc;"
    return (f'<div class="dlabel" style="position:relative;box-sizing:border-box;'
            f'width:{tpl.width_mm}mm;height:{tpl.height_mm}mm;{border}'
            f'background:#fff;overflow:hidden;font-family:{font}">{els}</div>')


def render_sheet(tpl, items, title=None) -> str:
    """A print-ready page of labels. `items` is a list of value dicts — one per
    label, already repeated for quantity by the caller.

    A4 with the labels flowing across it, rather than a driver-specific roll
    format: the warehouse prints from a browser, and "Print at 100%" on A4 is
    the one instruction that holds on every printer in the building. Each label
    keeps its exact millimetre size, so a sheet is cuttable and a roll printer
    given the same HTML still lays one label per page correctly."""
    n = len(items)
    title = title or tpl.name
    cards = "".join(render_label(tpl, v) for v in items)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{_hesc(title)} ({n})</title>
<style>
  @page {{ size: A4; margin: 6mm; }}
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 10px; color: #000; }}
  .bar {{ margin: 0 0 10px; }}
  .bar button {{ padding: 8px 16px; font-size: 14px; cursor: pointer; }}
  .hint {{ font-size: 12px; color: #555; margin: 0 0 10px; }}
  .sheet {{ display: flex; flex-wrap: wrap; gap: 3mm; align-content: flex-start; }}
  .dlabel {{ page-break-inside: avoid; break-inside: avoid; }}
  /* A symbol fills the box the designer drew for it, and nothing else decides
     its size. segno writes its own width/height in PIXELS — 159px for a garment
     payload — so without this the QR is printed at 42mm inside a 20mm box and
     the label's overflow:hidden takes a bite out of it. It still looks like a
     QR, which is the dangerous part: a symbol missing its finder pattern is one
     no scanner will ever read, and that is only discovered on the shop floor.
     The built-in carton and garment sheets in barcode_svc have always sized
     their symbols this way; this sheet is the one that did not. */
  .dlabel svg {{ width: 100%; height: 100%; display: block; }}
  @media print {{
    .bar, .hint {{ display: none; }}
    * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  }}
</style></head><body>
  <div class="bar"><button onclick="window.print()">🖨 Print {n} label{'' if n == 1 else 's'}</button></div>
  <div class="hint">{_hesc(tpl.name)} — {tpl.width_mm:g} × {tpl.height_mm:g} mm.
    Print at 100% / “Actual size”.</div>
  <div class="sheet">{cards}</div>
</body></html>"""


# ---------------------------------------------------------------------------
#  Serialisation + the template every install starts with
# ---------------------------------------------------------------------------
def to_dict(t) -> dict:
    return {
        "id": t.id, "name": t.name, "description": t.description or "",
        "width_mm": t.width_mm, "height_mm": t.height_mm,
        "padding_mm": t.padding_mm, "border": bool(t.border),
        "target": t.target or "product", "font": t.font,
        "elements": t.elements or [], "is_default": bool(t.is_default),
        "active": bool(t.active), "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


#: The 50 × 35 mm garment tag, laid out the way the warehouse reads one: the two
#: full-width rows at the top (whose label it is, what it is), then the
#: attributes a mis-pick turns on, then code and price at the foot — with the
#: text kept in a 26mm column so the QR has a clear 20mm square to itself.
#:
#: 20mm is the smallest square that keeps the WORST realistic payload (a long
#: description with every attribute filled — 53 modules including the quiet zone)
#: at 0.38mm per module, against the ~0.33mm floor for a phone camera in
#: warehouse light. It is why the text column stops at 26mm rather than running
#: the width and leaving the symbol whatever is left: on a sticker this small the
#: QR's size is the constraint everything else is laid out around, and a label
#: that reads beautifully and does not scan is not a label.
DEFAULT_ELEMENTS = [
    {"field": "company_name", "x": 2, "y": 1.5, "w": 46, "h": 4, "size": 7.5,
     "bold": True, "align": "center", "z": 1},
    {"field": "product_name", "x": 2, "y": 5.8, "w": 46, "h": 4.2, "size": 8,
     "bold": True, "z": 2},
    {"field": "size", "x": 2, "y": 10.5, "w": 12, "h": 3.6, "size": 7.5, "bold": True,
     "prefix": "Size: ", "z": 3},
    {"field": "color", "x": 14, "y": 10.5, "w": 14, "h": 3.6, "size": 7.5, "z": 4},
    {"field": "material", "x": 2, "y": 14.2, "w": 26, "h": 3.6, "size": 7, "z": 5},
    {"field": "line", "x": 2, "y": 18.4, "w": 26, "h": 0.3, "color": "#999999", "z": 6},
    {"field": "sku", "x": 2, "y": 19.4, "w": 26, "h": 4, "size": 7.5, "bold": True,
     "font": '"Courier New", monospace', "z": 7},
    {"field": "mrp", "x": 2, "y": 24, "w": 26, "h": 5, "size": 10, "bold": True,
     "prefix": "MRP ", "z": 8},
    {"field": "qr_code", "x": 28, "y": 12.5, "w": 20, "h": 20, "z": 9},
]


def ensure_default(db):
    """Create the starter template on an install that has none.

    Called at startup for the same reason the category and unit-type masters are
    seeded there: the printing screen is unusable with an empty template list,
    and making the warehouse design a label before it can print one is a wall in
    front of the first sticker. It is an ordinary template afterwards — editable,
    renamable, deletable once a better one is default."""
    if db.query(models.LabelTemplate).count():
        return None
    t = models.LabelTemplate(
        name="Garment Label 50 × 35 mm",
        description="The standard garment tag — name, size, colour, price and the QR.",
        width_mm=50, height_mm=35, padding_mm=2, border=True, target="product",
        font="Arial, Helvetica, sans-serif",
        elements=normalise_elements(DEFAULT_ELEMENTS, 50, 35),
        is_default=True, active=True, created_by="system",
        created_at=dt.datetime.utcnow())
    db.add(t)
    db.commit()
    return t
