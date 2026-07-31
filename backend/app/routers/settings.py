from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from .. import runtime
from ..extraction.engine import provider_status

router = APIRouter(prefix="/api/settings", tags=["settings"])


class VisionKeyIn(BaseModel):
    api_key: str
    model: Optional[str] = None


class ModelIn(BaseModel):
    model: str


def _status():
    prov = provider_status()
    return {
        "vision_enabled": prov.get("claude_vision", False),
        "active_live_provider": prov.get("active_live_provider"),
        "has_key": bool(runtime.get("anthropic_api_key")),
        "key_masked": runtime.masked_key(),
        "model": runtime.get("vision_model"),
        "provider_preference": runtime.get("provider_preference"),
        "providers": prov,
    }


MODELS_URL = "https://api.anthropic.com/v1/models"


class KeyRejected(Exception):
    pass


def _list_models(api_key: str):
    """Return the models available to this key (newest first) by calling the
    Anthropic Models API directly over HTTP — works on any SDK version and
    doubles as a key-validity check independent of any model name.
    Raises KeyRejected on 401/403 (bad key)."""
    import httpx
    r = httpx.get(MODELS_URL, params={"limit": 100},
                  headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                  timeout=15)
    if r.status_code in (401, 403):
        raise KeyRejected()
    r.raise_for_status()
    data = r.json().get("data", [])
    return [{"id": m["id"], "display_name": m.get("display_name", m["id"])} for m in data]


def _pick_model(models, preferred):
    ids = [m["id"] for m in models]
    if preferred and preferred in ids:
        return preferred
    # prefer a sonnet (good vision/quality balance), newest first
    for m in models:
        if "sonnet" in m["id"]:
            return m["id"]
    return ids[0] if ids else preferred


@router.get("")
def get_settings():
    return _status()


@router.get("/models")
def get_models():
    """List the models this key can use — powers the model dropdown."""
    key = runtime.get("anthropic_api_key")
    if not key:
        return {"ok": False, "models": [], "message": "No key set"}
    try:
        return {"ok": True, "models": _list_models(key)}
    except Exception as e:
        return {"ok": False, "models": [], "message": f"{type(e).__name__}: {e}"}


@router.post("/vision")
def set_vision_key(body: VisionKeyIn):
    key = (body.api_key or "").strip()
    if len(key) < 10:
        return {"ok": False, "message": "That doesn't look like a valid key."}
    # Validate the key by listing models — this both checks the key AND avoids
    # the "model not found" trap of pinging a hard-coded model name.
    try:
        models = _list_models(key)
    except KeyRejected:
        return {"ok": False, "message": "Invalid API key (authentication failed)."}
    except Exception as e:
        # network/other: accept the key but we couldn't fetch models
        runtime.set_many(anthropic_api_key=key,
                         vision_model=(body.model or runtime.get("vision_model")),
                         provider_preference="auto")
        return {"ok": True, "verified": False, "models": [],
                "message": f"Saved, but couldn't reach Anthropic to verify ({type(e).__name__}).",
                **_status()}
    if not models:
        return {"ok": False, "message": "Key valid but no models available on this account."}
    model = _pick_model(models, body.model or runtime.get("vision_model"))
    runtime.set_many(anthropic_api_key=key, vision_model=model, provider_preference="auto")
    return {"ok": True, "verified": True, "models": models, "chosen_model": model,
            "message": f"Verified · using {model}", **_status()}


@router.post("/model")
def set_model(body: ModelIn):
    """Change just the vision model (key already stored)."""
    runtime.set_many(vision_model=body.model.strip())
    return {"ok": True, **_status()}


@router.post("/vision/off")
def turn_off_vision():
    runtime.clear_key()
    return {"ok": True, **_status()}
