"""
Login for the app UI, with two roles: admin (masters + master-entry-by-image)
and user (processing). Credentials validated server-side against configured
accounts (env: ESSA_ADMIN_* / ESSA_USER_*). Returns an opaque token carrying the
role so the frontend can gate admin-only features and the token stays valid
across refreshes.
"""
import hashlib
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..config import AUTH_USERS, AUTH_SECRET

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token(username: str) -> str:
    pw = AUTH_USERS.get(username, {}).get("password", "")
    role = AUTH_USERS.get(username, {}).get("role", "")
    return hashlib.sha256(f"{username}:{pw}:{role}:{AUTH_SECRET}".encode()).hexdigest()


def resolve_token(token: str):
    """Return (username, role) for a valid token, else (None, None)."""
    for u in AUTH_USERS:
        if token and token == _token(u):
            return u, AUTH_USERS[u]["role"]
    return None, None


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn):
    acct = AUTH_USERS.get(body.username)
    if acct and body.password == acct["password"]:
        return {"ok": True, "token": _token(body.username),
                "user": body.username, "role": acct["role"]}
    raise HTTPException(401, "Invalid username or password")


@router.get("/verify")
def verify(token: str = ""):
    user, role = resolve_token(token)
    return {"ok": bool(user), "user": user, "role": role}
