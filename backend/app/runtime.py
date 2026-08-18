"""
Mutable runtime settings — lets the vision API key / model / provider preference
be changed from the UI at runtime (no restart), and persisted across restarts.

Source of truth precedence: what the user saves in the UI (settings.json) wins;
otherwise fall back to the environment defaults in config.py. The key is stored
locally in data/settings.json (git-ignored) and never logged.
"""
import os
import json
from . import config

SETTINGS_PATH = os.path.join(config.DATA_DIR, "settings.json")

_state = {
    "anthropic_api_key": config.ANTHROPIC_API_KEY,
    "vision_model": config.VISION_MODEL,
    "provider_preference": config.EXTRACTION_PROVIDER,   # auto | claude_vision | tesseract
    # The dead-stock discount ladder and the assumptions the cash impact is
    # worked out on — a commercial policy, not a constant, so it is set from the
    # screen and kept here rather than compiled into the code that applies it.
    # Empty until someone changes it: services/dead_stock.py holds the defaults,
    # and a stored blank must not shadow a default that is later improved.
    "dead_stock_rules": {},
}


def _load():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                data = json.load(f)
            for k in _state:
                if data.get(k):
                    _state[k] = data[k]
        except Exception:
            pass


def _persist():
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(_state, f)
    except Exception:
        pass


def get(key):
    return _state.get(key)


def set_many(**kw):
    for k, v in kw.items():
        if k in _state and v is not None:
            _state[k] = v
    _persist()


def clear_key():
    _state["anthropic_api_key"] = ""
    _persist()


def masked_key():
    k = _state.get("anthropic_api_key") or ""
    if len(k) < 8:
        return ""
    return f"{k[:6]}…{k[-4:]}"


_load()
