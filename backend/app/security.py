"""
Who is allowed to call what.

The rule is one table, not a decorator on each of the ~130 routes. A decorator
per route is the usual shape, but here it would mean touching twenty router
files and, worse, it would mean that the answer to "what can a floor user
actually do?" is only obtainable by reading all twenty. POLICY below is that
answer, on one screen, and a route added tomorrow inherits the rule for its
prefix instead of being silently unprotected because someone forgot the
decorator.

The split, per module:

    user        LR, invoice entry, GRN, inventory, bundles, outward/inward,
                label printing, notifications, voice, dashboard
    admin       the above plus masters, suppliers, label design, reports,
                payments, returns, dead stock
    superadmin  the above plus accounts and server settings

Reads and writes are ranked separately because the two do not follow each
other. Masters are an admin screen, but the floor cannot record a receipt
without reading the category and unit lists behind it — so masters read at
`user` and write at `admin`. Payments are admin at both ends: nothing on the
floor asks what a supplier is owed.
"""
import re

from fastapi import Request
from fastapi.responses import JSONResponse

from .database import SessionLocal
from .services.users import ROLE_RANK, resolve_token

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# (regex on the path, role needed to read, role needed to write). First match
# wins, so the specific entries come before the prefix they sit inside.
POLICY = [
    # --- accounts and the server's own configuration ---
    (r"^/api/users", "superadmin", "superadmin"),
    (r"^/api/settings", "superadmin", "superadmin"),

    # --- setup the floor reads and only admin edits ---
    (r"^/api/masters", "user", "admin"),
    (r"^/api/master-data", "user", "admin"),
    (r"^/api/suppliers", "user", "admin"),
    # Label templates: the floor prints from them all day, admin lays them out.
    # /print and /templates/{id}/preview are GETs, so they fall to the read rank.
    (r"^/api/labels", "user", "admin"),

    # --- admin at both ends ---
    (r"^/api/reports", "admin", "admin"),
    (r"^/api/payments", "admin", "admin"),
    (r"^/api/returns", "admin", "admin"),
    (r"^/api/dead-stock", "admin", "admin"),

    # --- the floor ---
    # "Clear all" empties every transaction table and can take the masters with
    # it. It sits inside a user-level prefix but is not user-level work: it is
    # irreversible, global, and one mis-click from a screen the floor uses all
    # day. Listed above /api/documents so it is matched first.
    (r"^/api/documents/clear-all", "superadmin", "superadmin"),
    (r"^/api/documents", "user", "user"),
    (r"^/api/purchases", "user", "user"),
    (r"^/api/inventory", "user", "user"),
    (r"^/api/lr", "user", "user"),
    (r"^/api/bundles", "user", "user"),
    (r"^/api/outward", "user", "user"),
    (r"^/api/notifications", "user", "user"),
    (r"^/api/voice", "user", "user"),
    (r"^/api/dashboard", "user", "user"),
]

POLICY_RE = [(re.compile(p), r, w) for p, r, w in POLICY]

# Reachable without a token. /api/auth is how you get one; /api/status is what
# the login screen probes before anyone has one; the mobile PWA shell and the
# built desktop bundle are static files whose own first call is the login.
PUBLIC = [
    re.compile(r"^/api/auth/"),
    re.compile(r"^/api/status$"),
    re.compile(r"^/docs"), re.compile(r"^/redoc"), re.compile(r"^/openapi.json$"),
]


def required_role(method: str, path: str):
    """The role this request needs, or None if the path is not policed here.

    Anything under /api that no entry matches gets the strictest answer rather
    than a free pass — a new router mounted without a POLICY line should be
    visibly locked, not quietly open.
    """
    for rx, read_role, write_role in POLICY_RE:
        if rx.match(path):
            return read_role if method.upper() in READ_METHODS else write_role
    if path.startswith("/api/"):
        return "superadmin"
    return None


def token_from(request: Request) -> str:
    """A token can arrive four ways, and all four are in use.

    The header is what the two apps send on their own calls. The cookie is for
    the requests the app does not make by hand — an <img> pointing at an invoice
    scan, or a report opened in a new tab — which cannot carry a header. The
    query parameter is the last resort for the same case, and for `curl`.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-essa-token")
            or request.cookies.get("essa_token")
            or request.query_params.get("token")
            or "")


def is_public(path: str) -> bool:
    return any(rx.match(path) for rx in PUBLIC)


async def auth_middleware(request: Request, call_next):
    """Rejects the call before it reaches the route.

    The two failures are kept distinct on purpose: 401 means "you are not signed
    in", which the apps answer by returning to the login screen, and 403 means
    "you are, but not as someone who may do this", which they answer by saying
    so and staying where they are. Collapsing them makes a permission error look
    like an expired session and sends the floor round a login loop.
    """
    path = request.url.path
    need = required_role(request.method, path)
    if need is None or is_public(path):
        return await call_next(request)

    db = SessionLocal()
    try:
        user = resolve_token(db, token_from(request))
        if user is None:
            return JSONResponse({"detail": "Sign in to continue"}, status_code=401)
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[need]:
            return JSONResponse(
                {"detail": f"This needs {need} access — you are signed in as {user.role}."},
                status_code=403)
        # Carried on the request so a route can attribute what it writes to the
        # person who did it without resolving the token a second time.
        request.state.user = {"username": user.username, "role": user.role,
                              "full_name": user.full_name or "", "id": user.id}
    finally:
        db.close()

    return await call_next(request)
