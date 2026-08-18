from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import masters as svc
from ..services import unit_types as ut

router = APIRouter(prefix="/api/masters", tags=["masters"])


class NameIn(BaseModel):
    name: str
    phone: Optional[str] = None


@router.get("/categories")
def categories(section: str = "", q: str = "", db: Session = Depends(get_db)):
    query = db.query(models.Category)
    if section:
        query = query.filter(models.Category.section == section)
    rows = query.order_by(models.Category.section, models.Category.name).all()
    if q:
        ql = q.lower()
        rows = [c for c in rows if ql in c.name.lower()]
    sections = sorted({c.section for c in db.query(models.Category.section).distinct()} - {None})
    return {"count": len(rows), "sections": sections,
            "items": [{"id": c.id, "section": c.section, "name": c.name} for c in rows]}


@router.get("/agents")
def agents(db: Session = Depends(get_db)):
    return [{"id": a.id, "name": a.name, "phone": a.phone}
            for a in db.query(models.Agent).order_by(models.Agent.name).all()]


@router.post("/agents")
def add_agent(body: NameIn, db: Session = Depends(get_db)):
    a = svc.get_or_create_agent(db, body.name)
    if a and body.phone:
        a.phone = body.phone
    db.commit()
    return {"ok": bool(a), "id": a.id if a else None}


@router.get("/transports")
def transports(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "phone": t.phone}
            for t in db.query(models.Transport).order_by(models.Transport.name).all()]


@router.post("/transports")
def add_transport(body: NameIn, db: Session = Depends(get_db)):
    t = svc.get_or_create_transport(db, body.name)
    if t and body.phone:
        t.phone = body.phone
    db.commit()
    return {"ok": bool(t), "id": t.id if t else None}


# ---- unit types + the rules that pick one ------------------------------------
#
# "Make Unit Type configurable per product" is two lists, not one. The TYPES say
# what a unit is worth in individual items (a pair is 2, a dozen is 12) and are
# what the dozen→pieces conversion is done against; the RULES say which type a
# product is, read off its description, so nobody re-picks "pillow cover = pair"
# on every receipt. Both are editable here, and both take effect on the next GRN
# posted — never retroactively, because stock already counted was counted under
# the rule in force when it arrived.

class UnitTypeIn(BaseModel):
    code: str
    name: Optional[str] = None
    pieces: float = 1.0
    aliases: Optional[list] = None
    countable: bool = True


class UnitTypeEdit(BaseModel):
    name: Optional[str] = None
    pieces: Optional[float] = None
    aliases: Optional[list] = None
    countable: Optional[bool] = None


class UnitRuleIn(BaseModel):
    pattern: str
    unit_type: str
    scope: str = "keyword"           # keyword (description) | category


def _type_out(t: models.UnitType):
    return {"id": t.id, "code": t.code, "name": t.name, "pieces": t.pieces,
            "aliases": t.aliases or [], "countable": bool(t.countable),
            "is_seed": bool(t.is_seed)}


def _rule_out(r: models.UnitRule):
    return {"id": r.id, "pattern": r.pattern, "scope": r.scope,
            "unit_type": r.unit_type, "source": r.source, "hits": r.hits or 0}


@router.get("/unit-types")
def unit_types(db: Session = Depends(get_db)):
    """The unit master and the rules that assign it, for the Masters screen and
    for the unit picker on a GRN line."""
    return {"types": [_type_out(t) for t in ut.types(db)],
            "rules": [_rule_out(r) for r in ut.rules(db)],
            "default": ut.DEFAULT_CODE}


@router.post("/unit-types")
def add_unit_type(body: UnitTypeIn, db: Session = Depends(get_db)):
    code = (body.code or "").strip().upper()
    if not code:
        raise HTTPException(400, "a unit needs a code")
    if ut.get(db, code):
        raise HTTPException(400, f"“{code}” is already a unit type")
    if body.pieces <= 0:
        raise HTTPException(400, "one of a unit has to contain at least some of an item")
    t = models.UnitType(code=code, name=(body.name or code.title()),
                        pieces=float(body.pieces),
                        aliases=[str(a).strip().upper() for a in (body.aliases or []) if str(a).strip()],
                        countable=bool(body.countable),
                        sort=len(ut.types(db)))
    db.add(t)
    db.commit()
    return _type_out(t)


@router.patch("/unit-types/{code}")
def edit_unit_type(code: str, body: UnitTypeEdit, db: Session = Depends(get_db)):
    """Change what a unit means. Applies to receipts posted from now on — every
    product already in stock froze its own factor when it was created, so
    correcting a typo here can never restate goods sitting on a shelf."""
    t = ut.get(db, code)
    if not t:
        raise HTTPException(404, "no such unit type")
    d = body.model_dump(exclude_unset=True)
    if "pieces" in d and d["pieces"] is not None:
        if float(d["pieces"]) <= 0:
            raise HTTPException(400, "one of a unit has to contain at least some of an item")
        t.pieces = float(d["pieces"])
    if d.get("name"):
        t.name = d["name"]
    if d.get("aliases") is not None:
        t.aliases = [str(a).strip().upper() for a in d["aliases"] if str(a).strip()]
    if d.get("countable") is not None:
        t.countable = bool(d["countable"])
    db.commit()
    return _type_out(t)


@router.delete("/unit-types/{code}")
def delete_unit_type(code: str, db: Session = Depends(get_db)):
    """Remove a unit nobody uses. Refused while products are counted in it —
    deleting it would leave their quantities meaning nothing."""
    t = ut.get(db, code)
    if not t:
        raise HTTPException(404, "no such unit type")
    used = db.query(models.Product).filter(
        models.Product.unit_type == t.code).count()
    if used:
        raise HTTPException(400, f"{used} product(s) are counted in {t.code} — "
                                 f"it can't be removed")
    for r in db.query(models.UnitRule).filter(
            models.UnitRule.unit_type == t.code).all():
        db.delete(r)
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.post("/unit-rules")
def add_unit_rule(body: UnitRuleIn, db: Session = Depends(get_db)):
    """Say that a wording means a unit — "pillow cover" is a PAIR — so every line
    that says it is born counted in pairs."""
    pattern = (body.pattern or "").strip().lower()
    code = (body.unit_type or "").strip().upper()
    if not pattern:
        raise HTTPException(400, "a rule needs some wording to match")
    if not ut.get(db, code):
        raise HTTPException(400, f"“{code}” is not a unit type")
    if body.scope not in ("keyword", "category"):
        raise HTTPException(400, "scope must be 'keyword' or 'category'")
    r = db.query(models.UnitRule).filter(
        models.UnitRule.scope == body.scope,
        models.UnitRule.pattern == pattern).first()
    if r:
        r.unit_type = code
        r.source = "human"
    else:
        r = models.UnitRule(pattern=pattern, scope=body.scope, unit_type=code,
                            source="human")
        db.add(r)
    db.commit()
    return _rule_out(r)


@router.delete("/unit-rules/{rule_id}")
def delete_unit_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(models.UnitRule, rule_id)
    if not r:
        raise HTTPException(404, "no such rule")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.get("/unit-preview")
def unit_preview(qty: float = 1, uom: str = "DOZ", unit_type: str = "",
                 description: str = "", db: Session = Depends(get_db)):
    """What a billed quantity becomes — for the GRN screen, and for checking a
    rule before saving it. "1 DOZ → 12 pcs → 6 PAIR · 6 QR label(s)"."""
    code = (unit_type or "").strip().upper()
    if not code:
        code, _, _ = ut.resolve(db, description=description, uom=uom)
    return ut.convert(db, qty, uom, code)


# ---- keyed dropdown lists (companies, cities, racks, modes, …) ---------------

class OptionIn(BaseModel):
    kind: str
    value: str


@router.get("/options")
def list_options(kind: str = "", db: Session = Depends(get_db)):
    """Every dropdown list at once, or one when `kind` is given. The LR Entry
    form loads this once and fills all its selects from it."""
    if kind:
        if kind not in svc.OPTION_KINDS:
            raise HTTPException(400, f"unknown list '{kind}'")
        return {kind: svc.options(db, kind)}
    return svc.all_options(db)


@router.post("/options")
def add_option(body: OptionIn, db: Session = Depends(get_db)):
    """Add a value to an open list. Seeded vocabularies (lr_mode,
    attachment_type, auto_transfer_location) are fixed and reject additions."""
    if body.kind not in svc.OPTION_KINDS:
        raise HTTPException(400, f"unknown list '{body.kind}'")
    if body.kind not in svc.OPEN_OPTIONS:
        raise HTTPException(400, f"'{body.kind}' is a fixed list — it can't be added to")
    o = svc.get_or_create_option(db, body.kind, body.value)
    db.commit()
    return {"ok": bool(o), "id": o.id if o else None}


@router.delete("/options")
def delete_option(kind: str, value: str, db: Session = Depends(get_db)):
    """Remove a value from an open list. Entries already carrying it keep it —
    these are free-text columns, not foreign keys, so history stays readable."""
    if kind not in svc.OPEN_OPTIONS:
        raise HTTPException(400, f"'{kind}' is a fixed list — it can't be edited")
    o = db.query(models.MasterOption).filter(
        models.MasterOption.kind == kind, models.MasterOption.value == value).first()
    if not o:
        raise HTTPException(404, "not in the list")
    db.delete(o)
    db.commit()
    return {"ok": True}
