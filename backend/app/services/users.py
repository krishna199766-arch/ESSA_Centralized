"""
Accounts, passwords and sign-in tokens.

Three ranked roles. Everything the app gates on is a comparison against this
rank, never a string equality test, so adding a tier later is one line here
rather than a hunt through the routers:

    user        the floor — receive, count, scan, print, dispatch
    admin       the floor plus the setup it works against, and the money screens
    superadmin  all of it, plus this table and the server's own settings

Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user salt, using only the
standard library — the deployment is a laptop or a small server and adding a
bcrypt build step to it buys nothing here. Tokens are HMACs rather than rows in
a session table: the phone is offline half the day and a stateless token
survives a server restart, which a session row would not. Revocation still
works because the user's `token_seed` is inside the signature — change the seed
and every token already issued stops verifying.
"""
import datetime as dt
import hashlib
import hmac
import os
import secrets

from sqlalchemy.orm import Session

from ..config import AUTH_SECRET, SEED_ACCOUNTS
from ..models import User

ROLES = ("user", "admin", "superadmin")
ROLE_RANK = {"user": 1, "admin": 2, "superadmin": 3}
ROLE_LABEL = {"user": "User", "admin": "Admin", "superadmin": "Super Admin"}

_PBKDF2_ROUNDS = 120_000


# --------------------------------------------------------------------------
#  passwords
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                                 _PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                   bytes.fromhex(salt), int(rounds)).hex()
    except (ValueError, AttributeError):
        return False
    # constant time: a timing difference here leaks how much of the digest matched
    return hmac.compare_digest(calc, digest)


def password_problem(password: str) -> str:
    """Why this password is not acceptable, or "" if it is. Deliberately mild —
    this is a warehouse floor, and a rule strict enough to force a sticky note
    on the monitor is worse than a short password typed from memory."""
    if len(password or "") < 6:
        return "Password must be at least 6 characters"
    return ""


# --------------------------------------------------------------------------
#  tokens
# --------------------------------------------------------------------------

def _sign(payload: str, seed: str) -> str:
    key = f"{AUTH_SECRET}:{seed or ''}".encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def mint_token(user: User) -> str:
    """`<username>.<issued-at>.<signature>` — readable enough to debug, signed
    so none of the three parts can be edited."""
    issued = str(int(dt.datetime.utcnow().timestamp()))
    payload = f"{user.username}.{issued}"
    return f"{payload}.{_sign(payload, user.token_seed)}"


def resolve_token(db: Session, token: str):
    """The User this token names, or None. Deactivating an account takes effect
    here on the next request, because the row is re-read every time rather than
    trusted from the token's contents."""
    if not token or token.count(".") != 2:
        return None
    username, issued, sig = token.split(".")
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.active:
        return None
    if not hmac.compare_digest(_sign(f"{username}.{issued}", user.token_seed), sig):
        return None
    return user


# --------------------------------------------------------------------------
#  the table itself
# --------------------------------------------------------------------------

def new_seed() -> str:
    return secrets.token_hex(16)


def create_user(db: Session, username: str, password: str, role: str,
                full_name: str = "", created_by: str = "") -> User:
    user = User(username=username.strip(), password_hash=hash_password(password),
                role=role, full_name=(full_name or "").strip() or None,
                active=True, token_seed=new_seed(), created_by=created_by or None)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_password(db: Session, user: User, password: str) -> None:
    """Changing a password also rotates the seed, which signs out every device
    that was holding a token for this account — including, on a reset, whoever
    the super admin is resetting it away from."""
    user.password_hash = hash_password(password)
    user.token_seed = new_seed()
    db.commit()


def out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "role_label": ROLE_LABEL.get(user.role, user.role),
        "full_name": user.full_name or "",
        "active": bool(user.active),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "created_by": user.created_by or "",
    }


def seed(db: Session) -> None:
    """Make sure there is a way in on a fresh database, and on an existing one
    that predates this table.

    The accounts and passwords are the ones this install was already configured
    with (ESSA_ADMIN_* / ESSA_USER_*, defaults unchanged), plus a super admin —
    so upgrading does not lock the warehouse out of its own app on Monday
    morning. Only missing rows are created: a password changed in the app is
    never reverted to the environment's value on the next restart.
    """
    existing = {row[0] for row in db.query(User.username).all()}
    for username, spec in SEED_ACCOUNTS.items():
        if not username or username in existing:
            continue
        # A blank password would hash and store like any other, and then let
        # anyone in as that account with nothing typed. config._env already
        # turns an empty variable back into the default; this is the second
        # lock, because seeding is the one path that does not go through
        # password_problem and an account nobody can lock is worth two.
        if not (spec.get("password") or "").strip():
            raise RuntimeError(
                f"Refusing to seed '{username}' with an empty password — "
                f"unset its ESSA_*_PASSWORD variable to use the default.")
        create_user(db, username, spec["password"], spec["role"],
                    full_name=spec.get("full_name", ""), created_by="system")

    # A database with no super admin has no way to reach user management, which
    # would make the tier unusable on any install that upgraded into it. If the
    # seeded super admin name is taken by an older account, promote it rather
    # than creating a second one.
    if not db.query(User).filter(User.role == "superadmin", User.active == True).first():  # noqa: E712
        name = os.environ.get("ESSA_SUPERADMIN_USER", "superadmin")
        row = db.query(User).filter(User.username == name).first()
        if row:
            row.role = "superadmin"
            row.active = True
            db.commit()
