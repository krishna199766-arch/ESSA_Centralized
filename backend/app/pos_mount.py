"""The retail shop (POS) module, served from inside this API at /pos.

The shop — "Textile Retail Shop" beside this backend — is a finished Flask app
with its own database, its own login and its own screens. It is not rewritten
into React here. It is mounted, as WSGI, under this same server, and the POS
button in the warehouse UI opens it in a frame.

Mounting rather than running it beside us on a second port is what makes the
button work at all: a Flask session cookie set by http://localhost:8100 is a
third-party cookie inside a frame served from :8000, and browsers drop it — the
POS would ask for a login it could never accept. Same origin, one port, one
process; the cookie is first-party and the whole thing behaves like a page of
this app.

The one wrinkle is that both codebases call their package `app`. Python has
room for exactly one `app` in sys.modules, and ours is already there. So the
shop is imported with ours lifted out of the way and put back afterwards (see
_load below) — which works because every `from app…` in the shop runs while its
package is the one installed. Two of them used to run later, at request time,
long after ours was back; those two imports were hoisted to the top of their
files (app/utils.py, app/routes/pos.py) so nothing reaches for `app` once the
swap is over.
"""

import importlib
import sys
from pathlib import Path

# …/essa-intake/backend/app/pos_mount.py → …/essa-intake/Textile Retail Shop
POS_DIR = Path(__file__).resolve().parents[2] / "Textile Retail Shop"

_OURS = ("app", "config")          # module names the shop would otherwise shadow


def _is_ours(name: str) -> bool:
    return name in _OURS or name.startswith("app.")


def _seed_if_empty(pkg) -> None:
    """A shop with no users has no way in — nobody can log in to create the first
    one. Ship the same starting data `python run.py init` would have made."""
    from app.models import User          # the shop's models, mid-swap
    if User.query.first() is not None:
        return
    from app.seed import seed_all
    seed_all()


def load_pos_app():
    """Import and return the shop's Flask application, or raise.

    Raises FileNotFoundError if the shop folder is not beside this project, and
    ImportError if its dependencies (Flask and friends) are not installed in
    this backend's environment — both are told to the user as a POS screen that
    explains itself rather than as a 500.
    """
    if not POS_DIR.is_dir():
        raise FileNotFoundError(f"POS module not found at {POS_DIR}")

    ours = {k: v for k, v in sys.modules.items() if _is_ours(k)}
    for name in ours:
        del sys.modules[name]
    sys.path.insert(0, str(POS_DIR))
    try:
        pkg = importlib.import_module("app")        # the shop's package
        flask_app = pkg.create_app()
        with flask_app.app_context():
            pkg.db.create_all()                     # first run: build the schema
            # The shop's categories are the warehouse master's, and its catalogue
            # is the warehouse's items — both kept in step on every start, and
            # both before the seed so a fresh shop seeds against them.
            from app.master_categories import sync_master_categories
            from app.warehouse_items import sync_warehouse_items
            sync_master_categories()
            sync_warehouse_items()
            _seed_if_empty(pkg)
        return flask_app
    finally:
        for name in [k for k in list(sys.modules) if _is_ours(k)]:
            del sys.modules[name]
        sys.modules.update(ours)                    # ours is `app` again
        try:
            sys.path.remove(str(POS_DIR))
        except ValueError:
            pass
