"""A sentence spoken in Tamil, turned into an English master record.

The browser's recogniser transcribes Tamil as Tamil — "ஜிடிசி இன் ப்ராடக்ட்
குரூப் வந்து" — and everything downstream of a master record is English: the
labels, the dropdown vocabularies, the searches that will later look for the row,
and the shop's catalogue that syncs from it. Filling "பில்லோ" into Name produces
a product nobody can find by typing "pillow", which is the same problem
services/translate.py exists to solve for register pages, arriving through a
different door.

So the transcript is not translated and then matched — it is matched and
translated in ONE pass, because the two decisions are the same decision. The
model is given the form's own fields, with their real dropdown options, and asked
which of them was spoken and what the English value is. That is strictly better
than translate-then-parse:

  * "கோடு 001" is the label "Code" plus the value "001", not a value reading
    "code 001" — only something that knows the field list can tell those apart.
  * A dropdown gets an option that EXISTS. Translating "டெக்ஸ்டைல்" gives
    "textile"; the master needs "Textile", which is in the list it was handed.
  * Digits said as words in either language ("ஜீரோ ஜீரோ ஒன்") become "001"
    rather than "zero zero one".

English is not sent here at all. The local parser in the browser
(frontend/src/voicefill.js) handles it instantly and free; this is the path for a
transcript the browser could only hand over in the language it heard.

With no API key configured there is no translator, and this says so rather than
filling the boxes with Tamil and leaving somebody to discover it later.
"""
import json

from .. import runtime
from . import master_defs, translate

#: What a field contributes to the prompt. Deliberately not the whole definition:
#: help text and layout hints are noise to the matcher, and the options list is
#: the only large part worth its tokens.
_KEEP = ("key", "label", "type", "options")

SYSTEM = """You map a spoken sentence onto the fields of a data-entry form.

The speaker may use Tamil, English, or Tamil and English mixed in one sentence
(Indian warehouse staff routinely do — "product group" said in English inside a
Tamil sentence, or an English word transcribed into Tamil script). The form is
English and every value you return MUST be English.

You are given the form's fields as JSON: key, label, type, and for a dropdown the
exact list of allowed options.

Rules, in order of importance:
1. A field is only in your answer if the speaker actually named it or clearly
   meant it. Never invent a value, and never guess at a field that was not
   mentioned. Fewer, right, beats more.
2. The spoken words include FIELD NAMES as well as values. "கோடு 001" is the
   field Code with value "001" — the label is not part of the value. Strip it.
3. type "select": the value must be copied EXACTLY from that field's options
   list. If nothing in the list is what was meant, omit the field entirely
   rather than inventing a new option.
4. type "num" or "money": return digits only, as a JSON number. Words for digits
   in any language become digits — "ஜீரோ ஜீரோ ஒன்" and "zero zero one" are both
   "001" (return "001" as a string if leading zeros matter, else the number).
   Never change, reorder, add or drop a digit that was said.
5. type "check": true or false.
6. Everything else: the English text, in Title Case for names of things
   ("Pillow", not "pillow"). Transliterate a proper noun rather than translating
   it — a supplier called "ஸ்ட்ராபெரி" is "Strawberry".
7. Never translate a code, an HSN number, a GSTIN or an invoice number. They are
   identifiers, not words.

Return ONLY this JSON object, no prose and no code fence:
{"fills": {"<field key>": <value>, ...},
 "english": "<the whole sentence in English, for the person to check>",
 "unused": "<any words you could not place, English>"}"""


def available() -> bool:
    """Whether a non-English transcript can be understood at all."""
    return bool(runtime.get("anthropic_api_key"))


def _field_spec(master, only=None):
    out = []
    for f in master_defs.fields(master):
        if only and f["key"] not in only:
            continue
        if f.get("type") == "date":
            continue                      # picked, never dictated — see the UI
        out.append({k: f[k] for k in _KEEP if f.get(k) not in (None, [])})
    return out


def _coerce(field, value):
    """Trust the model's mapping, but not its typing.

    A select that comes back with something outside its own list is dropped: a
    master record holding a value its dropdown does not contain is a row that
    cannot be edited without changing it, and this feature is not allowed to
    create those."""
    ftype = field.get("type")
    if value is None or value == "":
        return None
    if ftype == "check":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "yes", "1", "on", "tick", "ticked")
    if ftype in ("num", "money"):
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    opts = field.get("options") or []
    if opts and field.get("type") in ("select", "multiselect"):
        exact = next((o for o in opts if str(o).strip().lower() == text.lower()), None)
        if exact:
            return exact
        near = next((o for o in opts if text.lower() in str(o).lower()
                     or str(o).lower() in text.lower()), None)
        return near              # None → the field is left alone, per the docstring
    # A value that came back still in Tamil means the model treated it as a
    # proper noun and left it. The sweep that exists for register pages knows how
    # to transliterate exactly this case.
    if translate.needs_translation(text):
        got = translate.translate_texts([text]).get(text)
        if got:
            return got
    return text


def fill(master_key, transcript, only=None):
    """{fills, english, unused, reason} for one spoken sentence.

    `only` narrows it to a single field — the mic on one box, where the whole
    utterance is that box's value and the answer is one translated string.
    """
    master = master_defs.get(master_key)
    if not master:
        return {"fills": {}, "reason": f"no master called {master_key}"}
    said = (transcript or "").strip()
    if not said:
        return {"fills": {}, "reason": "nothing was said"}
    if not available():
        return {"fills": {}, "reason": "no-key",
                "english": None, "unused": said}

    spec = _field_spec(master, only=set(only) if only else None)
    if not spec:
        return {"fills": {}, "reason": "no dictatable fields"}

    client, model = translate._client()
    if client is None:
        return {"fills": {}, "reason": "no-key", "unused": said}

    ask = (f"Form: {master.get('label', master_key)}\n"
           f"Fields:\n{json.dumps(spec, ensure_ascii=False)}\n\n"
           f"Spoken sentence:\n{said}")
    if only:
        ask += ("\n\nThe speaker was filling ONLY this one field, so the whole "
                "sentence is its value unless it begins with the field's own name.")
    try:
        msg = client.messages.create(model=model, max_tokens=2000, system=SYSTEM,
                                     messages=[{"role": "user", "content": ask}])
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", "") == "text").strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as exc:
        # the transcript is not lost: the caller shows it, so nothing said has to
        # be said twice
        return {"fills": {}, "reason": f"could not be understood ({exc.__class__.__name__})",
                "unused": said}

    by_key = {f["key"]: f for f in master_defs.fields(master)}
    fills, dropped = {}, []
    for key, value in (data.get("fills") or {}).items():
        field = by_key.get(key)
        if not field:
            continue
        clean = _coerce(field, value)
        if clean is None:
            dropped.append(field.get("label") or key)
            continue
        fills[key] = clean
    return {"fills": fills,
            "english": (data.get("english") or "").strip() or None,
            "unused": (data.get("unused") or "").strip() or None,
            "dropped": dropped,
            "reason": None}
