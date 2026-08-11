"""Aggregated series behind the graphical dashboard."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..services import charts as svc

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/charts")
def charts(months: int = svc.MONTHS_BACK, db: Session = Depends(get_db)):
    """Every series the graphical dashboard draws, in one call.

    One call rather than five: the charts are read together and a dashboard that
    fills in piecemeal reads as five things loading badly rather than one thing
    loading. `months` is clamped — the axis stops being readable long before the
    query stops being answerable."""
    return svc.dashboard_charts(db, months=max(3, min(months, 24)))
