from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models
from ..services import masters as svc

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
