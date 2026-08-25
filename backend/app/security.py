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
from .services import permissions
from .services.users import ROLE_RANK, resolve_token

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# (regex on the path, role needed to read, role needed to write, screen). First
# match wins, so the specific entries come before the prefix they sit inside.
#
# The fourth column is which SCREEN the path belongs to, and it is here rather
# than in a table of its own for the same reason the roles are: a second table of
# path→screen kept beside this one is two tables that drift, and a drifted screen
# key is a permission that silently stops applying. `None` means no screen claims
# the path — the notification poll, the voice endpoint — and those are decided by
# the role alone. See services/permissions.
POLICY = [
    # --- accounts and the server's own configuration ---
    (r"^/api/users", "superadmin", "superadmin", "users"),
    (r"^/api/settings", "superadmin", "superadmin", "users"),

    # --- setup the floor reads and only admin edits ---
    (r"^/api/masters", "user", "admin", "masters"),
    (r"^/api/master-data", "user", "admin", "masters"),
    (r"^/api/suppliers", "user", "admin", "suppliers"),
    # Label templates: the floor prints from them all day, admin lays them out.
    # /print and /templates/{id}/preview are GETs, so they fall to the read rank.
    # Printing is its OWN screen, though — the person putting goods away opens it
    # every day and never opens the designer — so it is matched off first.
    (r"^/api/labels/print", "user", "user", "labelprint"),
    (r"^/api/labels", "user", "admin", "labels"),

    # --- admin at both ends ---
    (r"^/api/reports", "admin", "admin", "reports"),
    (r"^/api/payments", "admin", "admin", "payments"),
    (r"^/api/returns", "admin", "admin", "returns"),
    (r"^/api/dead-stock", "admin", "admin", "deadstock"),

    # --- the floor ---
    # "Clear all" empties every transaction table and can take the masters with
    # it. It sits inside a user-level prefix but is not user-level work: it is
    # irreversible, global, and one mis-click from a screen the floor uses all
    # day. Listed above /api/documents so it is matched first.
    (r"^/api/documents/clear-all", "superadmin", "superadmin", "users"),
    (r"^/api/documents", "user", "user", "documents"),
    (r"^/api/purchases", "user", "user", "purchases"),
    # The locator reads the whole account of one item and writes nothing. It is
    # its own screen and sits above /api/inventory, which it lives inside.
    (r"^/api/inventory/locate", "user", "user", "locator"),
    (r"^/api/inventory", "user", "user", "inventory"),
    (r"^/api/lr", "user", "user", "lr"),
    (r"^/api/bundles", "user", "user", "inventory"),
    # One router serves both ends of a transfer: dispatching it and accepting it.
    # They are two screens and two jobs — the warehouse packs, the shop receives —
    # so the receiving paths are matched off first.
    (r"^/api/outward/[0-9]+/receive", "user", "user", "inward"),
    (r"^/api/outward/inbox", "user", "user", "inward"),
    (r"^/api/outward", "user", "user", "outward"),
    (r"^/api/notifications", "user", "user", None),
    (r"^/api/voice", "user", "user", None),
    (r"^/api/dashboard", "user", "user", "dashboard"),
]

POLICY_RE = [(re.compile(p), r, w, s) for p, r, w, s in POLICY]

#: HTTP method -> what it is doing to the screen. PATCH and PUT are both
#: "change what is there"; POST is the only one that can mean either, and it
#: means create far more often than not — a POST that merely acts on an existing
#: record (posting a GRN, receiving a transfer) is covered because `create`
#: without `modify` is not a combination anybody grants.
METHOD_ACTION = {"GET": "view", "HEAD": "view", "OPTIONS": "view",
                 "POST": "create", "PUT": "modify", "PATCH": "modify",
                 "DELETE": "delete"}

#: …except when what it is doing is printing. A label, a QR, a barcode or a CSV
#: leaves the building on paper or on disk, which the reference system this
#: warehouse came from has always treated as its own permission — and rightly:
#: reading a cost on screen and walking out with it are different acts.
PRINT_RE = re.compile(r"(/print|/label|/qr\.(svg|png)|/barcode|\.csv$|[?&]format=csv)")

# Reachable without a token. /api/auth is how you get one; /api/status is what
# the login screen probes before anyone has one; the mobile PWA shell and the
# built desktop bundle are static files whose own first call is the login.
PUBLIC = [
    re.compile(r"^/api/auth/"),
    re.compile(r"^/api/status$"),
    # Applying the schema to a deployment that skips it at start — see
    # main.admin_boot. Public HERE and authorised THERE, by ESSA_AUTH_SECRET in
    # a header, because the state it exists to repair includes a users table
    # with nothing in it: there is no super admin to sign in as yet, so a token
    # check would lock the fix behind the thing it fixes.
    re.compile(r"^/api/admin/boot$"),
    re.compile(r"^/docs"), re.compile(r"^/redoc"), re.compile(r"^/openapi.json$"),
]


def required_role(method: str, path: str):
    """The role this request needs, or None if the path is not policed here."""
    return required_access(method, path)[0]


def required_access(method: str, path: str):
    """(role, screen, action) for a request.

    Anything under /api that no entry matches gets the strictest answer rather
    than a free pass — a new router mounted without a POLICY line should be
    visibly locked, not quietly open.
    """
    up = method.upper()
    action = METHOD_ACTION.get(up, "modify")
    if action == "view" and PRINT_RE.search(path):
        action = "print"
    for rx, read_role, write_role, screen in POLICY_RE:
        if rx.match(path):
            return (read_role if up in READ_METHODS else write_role), screen, action
    if path.startswith("/api/"):
        return "superadmin", None, action
    return None, None, action


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
    need, screen, action = required_access(request.method, path)
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
        # …and then, only for accounts that have actually been restricted, what
        # this one may do to THIS screen. The role above is still the ceiling: a
        # grant cannot reach past it, so this can only ever narrow.
        perms = permissions.normalise(user.permissions)
        if not permissions.allows(perms, screen, action):
            label = dict((k, l) for k, l, _, _ in permissions.SCREENS).get(screen, screen)
            verb = dict((k, l) for k, l, _ in permissions.ACTIONS).get(action, action)
            return JSONResponse(
                {"detail": f"You do not have {verb} access to {label}. "
                           "Ask a super admin to grant it in Users & Access."},
                status_code=403)
        # Carried on the request so a route can attribute what it writes to the
        # person who did it without resolving the token a second time, and so a
        # serialiser can withhold a figure this account is not shown.
        request.state.user = {"username": user.username, "role": user.role,
                              "full_name": user.full_name or "", "id": user.id,
                              "permissions": perms}
    finally:
        db.close()

    return await call_next(request)
