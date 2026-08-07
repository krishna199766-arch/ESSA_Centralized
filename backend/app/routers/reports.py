import io
import csv
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from ..database import get_db
from ..services import reports as svc

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
def catalogue():
    """List available reports, grouped. Each carries the filters it accepts, so
    the screen can offer those and only those."""
    return svc.catalogue()


@router.get("/groups")
def groups():
    """Group keys and their headings, in the order they are meant to be shown."""
    return svc.group_headings()


#: Every filter any report takes. `svc.run` drops the ones a given report doesn't
#: accept, so this list can grow without a per-report branch here.
def _filters(date_from, date_to, as_on, kind, product_id, supplier_id):
    return {"date_from": date_from, "date_to": date_to, "as_on": as_on,
            "kind": kind, "product_id": product_id, "supplier_id": supplier_id}


@router.get("/{key}")
def run_report(key: str, date_from: str = None, date_to: str = None,
               as_on: str = None, kind: str = None, product_id: int = None,
               supplier_id: int = None, db: Session = Depends(get_db)):
    rep = svc.run(db, key, **_filters(date_from, date_to, as_on, kind,
                                      product_id, supplier_id))
    if rep is None:
        raise HTTPException(404, "unknown report")
    return rep


@router.get("/{key}/csv")
def report_csv(key: str, date_from: str = None, date_to: str = None,
               as_on: str = None, kind: str = None, product_id: int = None,
               supplier_id: int = None, db: Session = Depends(get_db)):
    """The same rows the screen is showing — so the export honours the filters
    rather than quietly exporting everything."""
    rep = svc.run(db, key, **_filters(date_from, date_to, as_on, kind,
                                      product_id, supplier_id))
    if rep is None:
        raise HTTPException(404, "unknown report")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(rep["columns"])
    for r in rep["rows"]:
        w.writerow([r.get(c, "") for c in rep["columns"]])
    if rep.get("totals"):
        w.writerow([])
        w.writerow(["TOTALS"] + [f"{k}={v}" for k, v in rep["totals"].items()])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")
