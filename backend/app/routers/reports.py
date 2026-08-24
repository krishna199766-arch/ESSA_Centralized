import io
import csv
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..database import get_db
from ..services import reports as svc
from ..services import nlq as nlq_svc
from ..services import dates as date_svc

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


class Question(BaseModel):
    q: str


#: Registered above "/{key}", or a GET to /api/reports/ask-examples is read as a
#: report named "ask-examples". `/ask` itself is a POST — the question is free
#: text in any script and belongs in a body, not a URL.
@router.get("/ask-examples")
def ask_examples():
    """Questions that work, and whether a model is answering them.

    `engine` is here so the screen can say which of the two is running rather
    than presenting a keyword match as if it had read the sentence."""
    return {"engine": "model" if nlq_svc.available() else "keywords",
            "examples": nlq_svc.examples()}


@router.post("/ask")
def ask(body: Question, db: Session = Depends(get_db)):
    """Answer a natural-language question with one of the catalogue's reports.

    Returns the report in the same shape as `/{key}` plus an `interpretation`
    describing how the question was read, which filters the report actually ran
    with, and anything it could not honour. The screen shows that reading — a
    misroute the person can see and correct is a different thing from a wrong
    table presented as their answer."""
    return nlq_svc.ask(db, body.q)


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
    # Dates leave in the house format, DD-MM-YYYY, exactly as the screen showed
    # them. A download that disagrees with the table it came from is the one
    # nobody trusts — and the browser's own export does the same.
    for r in rep["rows"]:
        w.writerow([date_svc.display_cell(r.get(c, "")) for c in rep["columns"]])
    if rep.get("totals"):
        w.writerow([])
        w.writerow(["TOTALS"] + [f"{k}={v}" for k, v in rep["totals"].items()])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")
