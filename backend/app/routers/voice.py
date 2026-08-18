"""Voice into a form, for the languages a browser can only hand over as-is.

English dictation never reaches here — the browser matches it against the form's
own labels locally, instantly and for nothing. This is the Tamil path: see
services/voice_form.py for why matching and translating are one step.
"""
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import voice_form

router = APIRouter(prefix="/api/voice", tags=["voice"])


class FillIn(BaseModel):
    master: str
    transcript: str
    #: narrow it to one box — the mic on a single field
    fields: Optional[List[str]] = None
    #: what the recogniser was listening for, e.g. ta-IN. Recorded, not trusted:
    #: staff mix Tamil and English in one sentence and the model is told to expect
    #: that, so the answer does not change with this.
    language: Optional[str] = None


@router.get("/status")
def status():
    """Whether non-English dictation can work at all — the form asks before it
    offers Tamil, so the button can say why instead of failing on a press."""
    return {"available": voice_form.available()}


@router.post("/fill")
def fill(body: FillIn):
    return voice_form.fill(body.master, body.transcript, only=body.fields)
