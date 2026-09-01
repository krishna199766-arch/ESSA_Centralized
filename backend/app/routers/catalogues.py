"""Catalogues — the business lines this company trades in, and their vocabulary.

One router for the catalogue, its attributes, its attribute values and its
categories, because they are one thing: what a warehouse deals in. See
services/catalogues.py for why the unit of separation is the business line and
not the building.

Nothing here deletes a catalogue that is in use. A line with products filed under
it has stock, receipts and labels naming it; removing the row to tidy a dropdown
would orphan all of it. `active=false` is how a line is retired.
"""
from typing import List, Optional

import re

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..services import catalogues as svc
from ..services import master_import as imp

router = APIRouter(prefix="/api/catalogues", tags=["catalogues"])

#: A master is a few thousand short strings. Anything much larger is a workbook
#: with a year of sales in it, and reading it would tie the server up for a file
#: that was never going to be a category list.
MAX_UPLOAD = 8 * 1024 * 1024


XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")


def _safe(text: str) -> str:
    """A filename with nothing in it that would break a Content-Disposition."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "master")).strip("-") or "master"


def _download(grid, stem: str, fmt: str) -> Response:
    """One grid, as the file the browser asked for.

    Both formats come out of the same grid, so the CSV and the spreadsheet can
    never disagree about what a master contains — and both are shapes
    services/master_import reads back, which is what makes download → edit →
    upload a real workflow rather than a one-way export.
    """
    fmt = (fmt or "xlsx").lower()
    try:
        if fmt == "csv":
            return Response(imp.to_csv(grid), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition":
                                     f'attachment; filename="{_safe(stem)}.csv"'})
        if fmt in ("xlsx", "excel"):
            return Response(imp.to_xlsx(grid, sheet_title=stem), media_type=XLSX_MIME,
                            headers={"Content-Disposition":
                                     f'attachment; filename="{_safe(stem)}.xlsx"'})
    except imp.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    raise HTTPException(400, "format must be xlsx or csv")


async def _rows(file: UploadFile):
    """The uploaded file as a grid, or a 400 that says what was wrong with it."""
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, f"that file is larger than "
                                 f"{MAX_UPLOAD // (1024 * 1024)}MB")
    try:
        return imp.read_rows(file.filename or "", data)
    except imp.ImportError_ as exc:
        raise HTTPException(400, str(exc))


class CatalogueIn(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    #: Attribute keys to start with. One that matches a Product column uses it;
    #: anything else is stored in Product.attrs. Empty is the normal case — a new
    #: line is built by the people who trade in it.
    attributes: Optional[List[str]] = None


class CataloguePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class AttributeIn(BaseModel):
    key: str
    label: Optional[str] = None
    identity: Optional[bool] = True
    sort: Optional[int] = None
    active: Optional[bool] = None


class OptionsIn(BaseModel):
    """Values to offer for one attribute. Adds; never removes what is not listed."""
    values: List[str]


class CategoryIn(BaseModel):
    name: str
    section: Optional[str] = None


def _get(cid: int, db: Session) -> models.Catalogue:
    c = db.get(models.Catalogue, cid)
    if not c:
        raise HTTPException(404, "catalogue not found")
    return c


def _clean(s: Optional[str]) -> Optional[str]:
    s = (s or "").strip()
    return s or None


# ---------------------------------------------------------------------------
#  catalogues
# ---------------------------------------------------------------------------
@router.get("")
def list_catalogues(db: Session = Depends(get_db)):
    """Every business line, with what each one holds.

    Seeds on read, so a database that predates catalogues answers with its
    existing masters already filed under the garment line rather than looking
    empty until somebody presses something."""
    svc.ensure_seed(db)
    return {"catalogues": svc.listing(db),
            # the attributes Product has a real column for, so the editor can say
            # which keys map to one and which will live in the JSON bag
            "column_attributes": [{"key": k, "label": svc.COLUMN_LABELS.get(k, k)}
                                  for k in svc.COLUMN_ATTRS]}


@router.post("")
def create_catalogue(body: CatalogueIn, db: Session = Depends(get_db)):
    try:
        c = svc.create(db, body.code, body.name, body.description, body.attributes)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    return svc.out(c, db)


@router.get("/{cid}")
def get_catalogue(cid: int, db: Session = Depends(get_db)):
    c = _get(cid, db)
    return {**svc.out(c, db),
            "attributes": svc.attributes(db, c.id),
            "options": svc.options(db, c.id),
            "categories": [{"id": x.id, "name": x.name, "section": x.section}
                           for x in svc.categories(db, c.id, include_shared=False)],
            "warehouses": [{"id": w.id, "name": w.name, "code": w.code}
                           for w in c.warehouses]}


@router.patch("/{cid}")
def update_catalogue(cid: int, body: CataloguePatch, db: Session = Depends(get_db)):
    c = _get(cid, db)
    if body.name is not None:
        if not _clean(body.name):
            raise HTTPException(400, "a catalogue needs a name")
        c.name = body.name.strip()
    if body.description is not None:
        c.description = _clean(body.description)
    if body.active is not None:
        # The default line cannot be switched off: it is what every untagged row
        # belongs to and what a warehouse falls back to, so a disabled one would
        # leave receipts with nowhere to file their goods.
        if not body.active and c.is_default:
            raise HTTPException(400, "the default catalogue can't be switched off")
        c.active = bool(body.active)
    db.commit()
    db.refresh(c)
    return svc.out(c, db)


@router.delete("/{cid}")
def delete_catalogue(cid: int, db: Session = Depends(get_db)):
    c = _get(cid, db)
    if c.is_default:
        raise HTTPException(400, "the default catalogue can't be deleted")
    for label, n in (
        ("warehouse", db.query(models.Warehouse).filter(
            models.Warehouse.catalogue_id == c.id).count()),
        ("product", db.query(models.Product).filter(
            models.Product.catalogue_id == c.id).count()),
        ("category", db.query(models.Category).filter(
            models.Category.catalogue_id == c.id).count()),
    ):
        if n:
            raise HTTPException(400, f"{n} {label}(s) belong to “{c.name}” — "
                                     f"switch it off instead of deleting it")
    # The wordings this line had been taught. They point at categories that are
    # already gone, so they cannot be applied again and would only fail this
    # delete on a foreign key. Removed explicitly rather than cascaded, because
    # an alias is evidence somebody corrected the system and deleting one is
    # worth doing on purpose.
    db.query(models.CategoryAlias).filter(
        models.CategoryAlias.catalogue_id == c.id).delete(synchronize_session=False)
    db.delete(c)          # its attributes and their values go with it
    db.commit()
    return {"ok": True, "deleted": cid}


# ---------------------------------------------------------------------------
#  attributes — what this line records about an item
# ---------------------------------------------------------------------------
@router.get("/{cid}/attributes")
def list_attributes(cid: int, db: Session = Depends(get_db)):
    _get(cid, db)
    return {"attributes": svc.attributes(db, cid), "options": svc.options(db, cid)}


@router.post("/{cid}/attributes")
def add_attribute(cid: int, body: AttributeIn, db: Session = Depends(get_db)):
    c = _get(cid, db)
    key = (body.key or "").strip().lower().replace(" ", "_")
    if not key:
        raise HTTPException(400, "an attribute needs a key")
    have = db.query(models.CatalogueAttribute).filter(
        models.CatalogueAttribute.catalogue_id == c.id,
        models.CatalogueAttribute.key == key).first()
    if have:
        # Re-adding one that was switched off turns it back on rather than
        # failing — which is what the person clicking "add" means by it.
        have.active = True
        if body.label is not None:
            have.label = _clean(body.label)
        if body.identity is not None:
            have.identity = bool(body.identity)
        db.commit()
        return {"ok": True, "attributes": svc.attributes(db, c.id)}
    n = db.query(models.CatalogueAttribute).filter(
        models.CatalogueAttribute.catalogue_id == c.id).count()
    db.add(models.CatalogueAttribute(
        catalogue_id=c.id, key=key,
        label=_clean(body.label) or svc.COLUMN_LABELS.get(key),
        # A key the Product table already has a column for writes THERE, so this
        # line's colours land beside every other line's and every screen, label
        # and report that reads `color` sees them.
        column=key if key in svc.COLUMN_ATTRS else None,
        identity=True if body.identity is None else bool(body.identity),
        sort=n if body.sort is None else body.sort, active=True))
    db.commit()
    return {"ok": True, "attributes": svc.attributes(db, c.id)}


@router.patch("/{cid}/attributes/{key}")
def update_attribute(cid: int, key: str, body: AttributeIn,
                     db: Session = Depends(get_db)):
    _get(cid, db)
    a = db.query(models.CatalogueAttribute).filter(
        models.CatalogueAttribute.catalogue_id == cid,
        models.CatalogueAttribute.key == key).first()
    if not a:
        raise HTTPException(404, "attribute not found")
    if body.label is not None:
        a.label = _clean(body.label)
    if body.identity is not None:
        a.identity = bool(body.identity)
    if body.sort is not None:
        a.sort = body.sort
    if body.active is not None:
        a.active = bool(body.active)
    db.commit()
    return {"ok": True, "attributes": svc.attributes(db, cid)}


@router.delete("/{cid}/attributes/{key}")
def remove_attribute(cid: int, key: str, db: Session = Depends(get_db)):
    """Stop recording an attribute. Switched off, not erased.

    Items already carrying a value keep it — it is what they were received as,
    and deleting it would rewrite the record of goods nobody has touched."""
    _get(cid, db)
    a = db.query(models.CatalogueAttribute).filter(
        models.CatalogueAttribute.catalogue_id == cid,
        models.CatalogueAttribute.key == key).first()
    if not a:
        raise HTTPException(404, "attribute not found")
    a.active = False
    db.commit()
    return {"ok": True, "attributes": svc.attributes(db, cid)}


@router.post("/{cid}/attributes/{key}/options")
def set_options(cid: int, key: str, body: OptionsIn, db: Session = Depends(get_db)):
    """Offer these values for this attribute. Additive and idempotent."""
    _get(cid, db)
    added = 0
    for v in body.values or []:
        if svc.add_option(db, cid, key, v) is not None:
            added += 1
    db.commit()
    return {"ok": True, "added": added, "options": svc.options(db, cid).get(key, [])}


@router.get("/{cid}/attributes/export")
def export_attributes(cid: int, format: str = "xlsx", db: Session = Depends(get_db)):
    """This line's attributes and every value they offer, as a spreadsheet.

    Comes out in the WIDE shape the importer reads, so the file can be edited and
    uploaded straight back. An attribute with no values still gets its column —
    that column is the template for filling it in, which is why a catalogue that
    has been set up but not populated still downloads something useful.
    """
    c = _get(cid, db)
    grid = imp.attribute_grid(svc.attributes(db, c.id), svc.options(db, c.id))
    return _download(grid, f"{c.code}-attributes", format)


@router.post("/{cid}/attributes/import")
async def import_attributes(cid: int, file: UploadFile = File(...),
                            commit: bool = False, db: Session = Depends(get_db)):
    """Read many attributes AND their values out of one spreadsheet or PDF.

    PREVIEWS BY DEFAULT. With `commit=false` (the default) nothing is written and
    the answer is exactly what would be — every attribute, how many values are
    new, and which are already there. A file that silently added four hundred
    wrong values to a live master would not be noticed until products had been
    classified against them, so the guess this module makes about somebody's
    layout is always shown before it is acted on.

    Accepts a column per attribute, or two columns headed Attribute and Value —
    see services/master_import, which detects which rather than asking.
    """
    c = _get(cid, db)
    rows = await _rows(file)
    try:
        found = imp.parse_attributes(rows)
    except imp.ImportError_ as exc:
        raise HTTPException(400, str(exc))

    have_attrs = {a["key"]: a for a in svc.attributes(db, c.id)}
    have_opts = svc.options(db, c.id)
    plan, added_attrs, added_values = [], 0, 0
    for key, entry in found.items():
        known = {v.lower() for v in have_opts.get(key, [])}
        fresh = [v for v in entry["values"] if v.lower() not in known]
        is_new = key not in have_attrs
        plan.append({
            "key": key, "label": entry["label"], "new_attribute": is_new,
            # Said per row, because "this one will write to the column every
            # screen reads" and "this one lives on the item" are different facts
            # and the person importing should see which they are getting.
            "stored": "column" if key in svc.COLUMN_ATTRS else "attrs",
            "values_in_file": len(entry["values"]),
            "values_new": len(fresh),
            "sample": fresh[:8],
        })
        added_attrs += 1 if is_new else 0
        added_values += len(fresh)

    plan.sort(key=lambda p: (-p["values_new"], p["key"]))
    result = {"committed": False, "attributes": plan,
              "new_attributes": added_attrs, "new_values": added_values,
              "source": file.filename}
    if not commit:
        return result

    for key, entry in found.items():
        a = db.query(models.CatalogueAttribute).filter(
            models.CatalogueAttribute.catalogue_id == c.id,
            models.CatalogueAttribute.key == key).first()
        if a:
            a.active = True
        else:
            n = db.query(models.CatalogueAttribute).filter(
                models.CatalogueAttribute.catalogue_id == c.id).count()
            db.add(models.CatalogueAttribute(
                catalogue_id=c.id, key=key,
                label=svc.COLUMN_LABELS.get(key) or entry["label"],
                column=key if key in svc.COLUMN_ATTRS else None,
                identity=True, sort=n, active=True))
            db.flush()
        for v in entry["values"]:
            svc.add_option(db, c.id, key, v)
    db.commit()
    result["committed"] = True
    result["attributes_now"] = svc.attributes(db, c.id)
    return result


@router.get("/{cid}/attributes/{key}/options/export")
def export_options(cid: int, key: str, format: str = "xlsx",
                   db: Session = Depends(get_db)):
    """One attribute's values, as a single column headed with its own name."""
    c = _get(cid, db)
    a = db.query(models.CatalogueAttribute).filter(
        models.CatalogueAttribute.catalogue_id == c.id,
        models.CatalogueAttribute.key == key).first()
    if not a:
        raise HTTPException(404, "attribute not found")
    label = a.label or svc.COLUMN_LABELS.get(a.key) or a.key
    grid = imp.values_grid(label, svc.options(db, c.id).get(key, []))
    return _download(grid, f"{c.code}-{key}", format)


@router.post("/{cid}/attributes/{key}/options/import")
async def import_options(cid: int, key: str, file: UploadFile = File(...),
                         commit: bool = False, db: Session = Depends(get_db)):
    """Read the values for ONE attribute out of a file — a single column of them.

    The individual counterpart to the bulk import above: somebody has this
    attribute's list and only this one. Previews the same way.
    """
    c = _get(cid, db)
    a = db.query(models.CatalogueAttribute).filter(
        models.CatalogueAttribute.catalogue_id == c.id,
        models.CatalogueAttribute.key == key).first()
    if not a:
        raise HTTPException(404, "attribute not found")
    rows = await _rows(file)
    try:
        # Told which attribute this is for, so a file headed with the attribute's
        # own name has that header recognised rather than imported as a value.
        values = imp.parse_values(rows, attr_key=a.key, attr_label=a.label)
    except imp.ImportError_ as exc:
        raise HTTPException(400, str(exc))

    known = {v.lower() for v in svc.options(db, c.id).get(key, [])}
    fresh = [v for v in values if v.lower() not in known]
    result = {"committed": False, "key": key,
              "values_in_file": len(values), "values_new": len(fresh),
              "sample": fresh[:20], "source": file.filename}
    if not commit:
        return result
    for v in values:
        svc.add_option(db, c.id, key, v)
    db.commit()
    result["committed"] = True
    result["options"] = svc.options(db, c.id).get(key, [])
    return result


@router.delete("/{cid}/attributes/{key}/options")
def remove_option(cid: int, key: str, value: str, db: Session = Depends(get_db)):
    """Withdraw one value from a dropdown.

    Items already carrying it keep it — the list decides what is OFFERED, never
    what the goods on the shelf are."""
    _get(cid, db)
    row = db.query(models.AttributeOption).filter(
        models.AttributeOption.catalogue_id == cid,
        models.AttributeOption.attr == key,
        models.AttributeOption.value == value).first()
    if not row:
        raise HTTPException(404, "that value isn't in this list")
    db.delete(row)
    db.commit()
    return {"ok": True, "options": svc.options(db, cid).get(key, [])}


# ---------------------------------------------------------------------------
#  categories — this line's own master
# ---------------------------------------------------------------------------
@router.get("/{cid}/categories")
def list_categories(cid: int, include_shared: bool = False,
                    db: Session = Depends(get_db)):
    _get(cid, db)
    rows = svc.categories(db, cid, include_shared=include_shared)
    return {"categories": [{"id": x.id, "name": x.name, "section": x.section,
                            "shared": x.catalogue_id is None} for x in rows]}


@router.post("/{cid}/categories")
def add_category(cid: int, body: CategoryIn, db: Session = Depends(get_db)):
    c = _get(cid, db)
    name = (body.name or "").strip().upper()
    if not name:
        raise HTTPException(400, "a category needs a name")
    if db.query(models.Category).filter(
            models.Category.name == name,
            models.Category.catalogue_id == c.id).first():
        raise HTTPException(409, f"“{name}” is already in this catalogue")
    row = models.Category(catalogue_id=c.id, name=name,
                          section=_clean(body.section) or "OVERALL")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "section": row.section}


@router.get("/{cid}/categories/export")
def export_categories(cid: int, include_shared: bool = False,
                      format: str = "xlsx", db: Session = Depends(get_db)):
    """This line's category master, with its sections, as a spreadsheet."""
    c = _get(cid, db)
    rows = svc.categories(db, c.id, include_shared=include_shared)
    return _download(imp.category_grid(rows), f"{c.code}-categories", format)


@router.post("/{cid}/categories/import")
async def import_categories(cid: int, file: UploadFile = File(...),
                            commit: bool = False, db: Session = Depends(get_db)):
    """Read this line's category master out of a spreadsheet or PDF.

    One column of names, and a `Section` column where there is one (the garment
    master splits OVERALL / KIDS / LADIES / MENS, and the section is what reports
    group by). Names are upper-cased, which is what the existing master is.

    Previews by default, like every import here — see import_attributes.
    """
    c = _get(cid, db)
    rows = await _rows(file)
    try:
        found = imp.parse_categories(rows)
    except imp.ImportError_ as exc:
        raise HTTPException(400, str(exc))

    have = {x.name.lower() for x in svc.categories(db, c.id, include_shared=False)}
    fresh = [r for r in found if r["name"].lower() not in have]
    result = {"committed": False, "in_file": len(found), "new": len(fresh),
              "already_there": len(found) - len(fresh),
              "sample": fresh[:20], "source": file.filename}
    if not commit:
        return result

    for r in fresh:
        db.add(models.Category(catalogue_id=c.id, name=r["name"],
                               section=r["section"]))
    db.commit()
    result["committed"] = True
    result["categories"] = [{"id": x.id, "name": x.name, "section": x.section}
                            for x in svc.categories(db, c.id, include_shared=False)]
    return result


@router.delete("/{cid}/categories/{cat_id}")
def remove_category(cid: int, cat_id: int, db: Session = Depends(get_db)):
    _get(cid, db)
    row = db.get(models.Category, cat_id)
    if not row or row.catalogue_id != cid:
        raise HTTPException(404, "category not found in this catalogue")
    n = db.query(models.Product).filter(models.Product.category == row.name).count()
    if n:
        raise HTTPException(400, f"{n} product(s) are filed under “{row.name}” — "
                                 f"reclassify them before removing it")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": cat_id}
