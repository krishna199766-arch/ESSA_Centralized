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
    """List available reports, grouped."""
    return svc.catalogue()


@router.get("/{key}")
def run_report(key: str, kind: str = None, db: Session = Depends(get_db)):
    kw = {}
    if key == "stock_movement" and kind:
        kw["kind"] = kind
    rep = svc.run(db, key, **kw)
    if rep is None:
        raise HTTPException(404, "unknown report")
    return rep


@router.get("/{key}/csv")
def report_csv(key: str, db: Session = Depends(get_db)):
    rep = svc.run(db, key)
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
