"""Voice into a form, for the languages a browser can only hand over as-is.

English dictation never reaches here — the browser matches it against the form's
own labels locally, instantly and for nothing. This is the Tamil path: see
services/voice_form.py for why matching and translating are one step.
"""
from typing import Any, Dict, List, Optional

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


class FillFormIn(BaseModel):
    """Dictation into a form that is NOT a master record.

    The purchase order form has no `MasterDefinition` and should not be given a
    fake one — it is not a list anybody maintains. So it hands over its own
    fields, which is also what keeps the labels in one place: the form renders
    from them and dictates against them, and there is no second copy on the
    server to fall out of step the first time a field is renamed.
    """
    transcript: str
    form_fields: List[Dict[str, Any]]
    label: Optional[str] = "Form"
    #: narrow it to one box — the mic on a single field
    only: Optional[List[str]] = None
    language: Optional[str] = None


@router.get("/status")
def status():
    """Whether non-English dictation can work at all — the form asks before it
    offers Tamil, so the button can say why instead of failing on a press."""
    return {"available": voice_form.available()}


@router.post("/fill")
def fill(body: FillIn):
    return voice_form.fill(body.master, body.transcript, only=body.fields)


@router.post("/fill-form")
def fill_form(body: FillFormIn):
    """The same understanding, over the fields the caller names."""
    return voice_form.fill_fields(body.form_fields, body.transcript,
                                  only=body.only, label=body.label or "Form")
