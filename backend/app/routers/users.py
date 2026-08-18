"""
User management — the super admin's screen.

Reachable only by a super admin; that is enforced in security.POLICY, not here,
so this file is about the rules that are specific to accounts rather than about
who may open it.

Those rules exist to stop the two ways an install locks itself out. A super
admin may not demote, deactivate or delete themselves — the account you are
signed in as is the one holding the door open. And the last active super admin
may not be removed by any route, even by another super admin, because the
screen that would fix the mistake is the one that just became unreachable.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..services import users as users_svc

router = APIRouter(prefix="/api/users", tags=["users"])


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "user"
    full_name: str = ""


class UserPatch(BaseModel):
    role: str | None = None
    full_name: str | None = None
    active: bool | None = None


class ResetIn(BaseModel):
    new_password: str


def _me(request: Request) -> dict:
    """The signed-in user, put on the request by the auth middleware."""
    return getattr(request.state, "user", None) or {}


def _get(db: Session, uid: int) -> User:
    user = db.query(User).get(uid)
    if not user:
        raise HTTPException(404, "No such user")
    return user


def _guard_last_superadmin(db: Session, user: User) -> None:
    if user.role != "superadmin" or not user.active:
        return
    others = (db.query(User)
              .filter(User.role == "superadmin", User.active == True,  # noqa: E712
                      User.id != user.id).count())
    if not others:
        raise HTTPException(400, "This is the last super admin — promote someone "
                                 "else first, or nobody can manage users.")


def _guard_self(request: Request, user: User) -> None:
    if user.username == _me(request).get("username"):
        raise HTTPException(400, "You cannot change your own role or access — "
                                 "ask another super admin.")


@router.get("")
def list_users(db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.active.desc(), User.username).all()
    return {"users": [users_svc.out(u) for u in rows],
            "roles": [{"value": r, "label": users_svc.ROLE_LABEL[r]} for r in users_svc.ROLES]}


@router.post("")
def create_user(body: UserIn, request: Request, db: Session = Depends(get_db)):
    username = (body.username or "").strip()
    if not username:
        raise HTTPException(400, "Username is required")
    if body.role not in users_svc.ROLES:
        raise HTTPException(400, f"Role must be one of {', '.join(users_svc.ROLES)}")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, f"'{username}' already exists")
    problem = users_svc.password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    user = users_svc.create_user(db, username, body.password, body.role,
                                 body.full_name, created_by=_me(request).get("username", ""))
    return users_svc.out(user)


@router.patch("/{uid}")
def update_user(uid: int, body: UserPatch, request: Request,
                db: Session = Depends(get_db)):
    user = _get(db, uid)

    if body.full_name is not None:
        user.full_name = body.full_name.strip() or None

    if body.role is not None and body.role != user.role:
        if body.role not in users_svc.ROLES:
            raise HTTPException(400, f"Role must be one of {', '.join(users_svc.ROLES)}")
        _guard_self(request, user)
        _guard_last_superadmin(db, user)
        user.role = body.role

    if body.active is not None and bool(body.active) != bool(user.active):
        if not body.active:
            _guard_self(request, user)
            _guard_last_superadmin(db, user)
        user.active = bool(body.active)
        # Deactivating cuts the phone and desktop off at the next request rather
        # than at the next login — resolve_token re-reads this row every time.

    db.commit()
    return users_svc.out(user)


@router.post("/{uid}/password")
def reset_password(uid: int, body: ResetIn, db: Session = Depends(get_db)):
    """A reset, not a change — the super admin sets a new password without
    knowing the old one, and every device holding a token for that account is
    signed out by the seed rotation inside set_password."""
    user = _get(db, uid)
    problem = users_svc.password_problem(body.new_password)
    if problem:
        raise HTTPException(400, problem)
    users_svc.set_password(db, user, body.new_password)
    return {"ok": True}


@router.delete("/{uid}")
def delete_user(uid: int, request: Request, db: Session = Depends(get_db)):
    user = _get(db, uid)
    _guard_self(request, user)
    _guard_last_superadmin(db, user)
    db.delete(user)
    db.commit()
    return {"ok": True}
