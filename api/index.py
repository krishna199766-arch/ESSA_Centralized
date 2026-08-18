"""
The whole server, as one Vercel function.

Vercel's Python runtime serves whatever ASGI or WSGI application this module
calls `app`, so this file's only job is to make `backend/` and the shop
importable and then hand over the application that already exists. There is no
second copy of the wiring here: the routers, the access-control middleware, the
phone app at /m and the shop mounted at /pos are all assembled in
backend/app/main.py exactly as they are when the same server runs on the
warehouse PC. One code path, two ways of being started.

Everything under /api, /m and /pos is routed here by the rewrites in
vercel.json; the desktop bundle is served by Vercel itself as static files.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `backend` holds the package `app`; the shop is found relative to ROOT by
# pos_mount, which does its own sys.modules juggling because both codebases
# call their package `app`.
sys.path.insert(0, str(ROOT / "backend"))

# The one thing that differs from a laptop run, and it differs because the
# platform differs: outside /tmp the filesystem is read-only. STATE_DIR is only
# reached for scratch work now — the database is Postgres and uploaded invoices
# go to Blob storage — but config.py creates it at import, and creating it under
# the read-only code directory would fail before a single route was registered.
os.environ.setdefault("ESSA_STATE_DIR", "/tmp/essa")

from app.main import app  # noqa: E402  (path must be set first)

__all__ = ["app"]
