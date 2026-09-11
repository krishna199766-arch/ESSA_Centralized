"""Every screen the shop has is reachable from the warehouse's POS menu.

    python backend/tools/pos_screens_test.py

The shop keeps its own list of modules (app/modules.py) and the warehouse keeps
its own list of POS tabs (frontend/src/App.jsx, POS_SCREENS). They are two copies
of one fact, and they drifted: four screens — Physical Stock Audit, Stock Check,
Floors & Tills, Promotions — existed, worked, and were reachable only from inside
the frame. Anybody navigating from the warehouse simply could not see them, and
the module looked missing rather than unlisted.

The two lists cannot be merged: one is Python read at request time, the other is
JavaScript compiled into a bundle, and the tab has to be drawn before the frame
is loaded. So they are checked against each other instead, here.

Also checks that every path a tab points at is a real route on the shop, because
a tab that 404s inside the frame is worse than no tab: it looks like the screen
is broken rather than misfiled.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOP = ROOT / "Textile Retail Shop"
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

bad = []


def eq(what, got, want):
    if got == want:
        print(f"  ok    {what}")
        return
    bad.append(what)
    print(f"  FAIL  {what}\n        got  {got!r}\n        want {want!r}")


def ok(what, cond, detail=""):
    if cond:
        print(f"  ok    {what}")
        return
    bad.append(what)
    print(f"  FAIL  {what}" + (f"  {detail}" if detail else ""))


def head(t):
    print(f"\n{t}")


# ---- the two lists ---------------------------------------------------------
head("the shop's modules and the warehouse's POS tabs are the same set")

sys.path.insert(0, str(SHOP))
# Imported as source rather than executed: app/modules.py is a plain list and
# importing the shop's package here would drag in Flask, a database and the
# whole package-name swap for the sake of one constant.
modules_src = (SHOP / "app" / "modules.py").read_text(encoding="utf-8")
shop_keys = re.findall(r'\{"key":\s*"([a-z_]+)"', modules_src)
shop_labels = dict(zip(shop_keys, re.findall(r'"label":\s*"([^"]+)"', modules_src)))
ok("the shop's module list was read", len(shop_keys) >= 10, f"found {shop_keys}")

jsx = APP_JSX.read_text(encoding="utf-8")
block = re.search(r"const POS_SCREENS = \[(.*?)\n\]", jsx, re.S)
ok("the warehouse's POS tab list was read", block is not None)
tabs = re.findall(r"key: 'pos:([a-z_]+)'[^}]*?path: '([^']+)'", block.group(1) if block else "")
tab_keys = [k for k, _ in tabs]

missing = [k for k in shop_keys if k not in tab_keys]
eq("no shop screen is missing a tab",
   [f"{k} ({shop_labels.get(k, '?')})" for k in missing], [])
stale = [k for k in tab_keys if k not in shop_keys]
eq("and no tab points at a screen the shop no longer has", stale, [])
eq("they are in the same order", tab_keys, shop_keys)


# ---- and every tab actually goes somewhere ---------------------------------
head("every tab points at a route the shop really serves")
import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      f"sqlite:///{Path(tempfile.mkdtemp()) / 'tabs.db'}")
os.environ.setdefault("ESSA_WAREHOUSE_DB",
                      str(Path(tempfile.mkdtemp()) / "no-warehouse.db"))

from app import create_app                                          # noqa: E402

shop_app = create_app()
rules = {str(r) for r in shop_app.url_map.iter_rules()}
for key, path in tabs:
    # The frame is served under /pos, and these paths are relative to the shop
    # itself — which is exactly what the mount rewrites them to.
    ok(f"{key} → {path}", path.rstrip("/") in {r.rstrip("/") for r in rules}
       or path in rules, f"no route matches {path}")


print("\n%s" % ("all passing" if not bad else "FAILED: " + ", ".join(bad)))
sys.exit(1 if bad else 0)
