"""User and role management.

Two rules encoded here that matter more than they look:

  * **An admin cannot demote or disable themselves** while they are the last admin. Locking
    every administrator out of a DLP console is a genuinely bad afternoon, and it is a
    single misclick away without this check.

  * **Passwords are never returned, logged, or accepted in a URL.** They arrive in a JSON
    body and leave as a scrypt hash.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Session as SessionRow
from ..models import User
from .auth import ROLE_RANK, ROLES, current_user, hash_password, require_role, verify_password

router = APIRouter(prefix="/users", tags=["users"])

MIN_PASSWORD_LENGTH = 12


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=128)
    display_name: str = ""
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=512)
    role: str = "viewer"


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=512)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=512)


def _out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "active": user.active,
        "created_at": user.created_at,
    }


def _check_role(role: str) -> str:
    if role not in ROLE_RANK:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"role must be one of: {', '.join(ROLES)}",
        )
    return role


def _admin_count(session: Session, exclude: str | None = None) -> int:
    query = select(func.count()).select_from(User).where(
        User.role == "admin", User.active.is_(True)
    )
    if exclude:
        query = query.where(User.id != exclude)
    return session.scalar(query) or 0


def _guard_last_admin(session: Session, target: User) -> None:
    if target.role == "admin" and target.active and _admin_count(session, exclude=target.id) == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this is the last active admin — promote someone else first",
        )


def _revoke_sessions(session: Session, user_id: str) -> None:
    """A disabled or demoted user must lose their current session immediately, not at
    expiry. Otherwise revoking access does nothing for up to eight hours."""
    session.execute(SessionRow.__table__.delete().where(SessionRow.user_id == user_id))


@router.get("/me")
def whoami(user: User = Depends(current_user)) -> dict:
    return _out(user) | {"permissions": _permissions(user.role)}


@router.post("/me/password")
def change_own_password(
    payload: PasswordChange,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Anyone may change their own password, given the current one."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    session.flush()
    return {"changed": True}


@router.get("")
def list_users(
    session: Session = Depends(get_session), _user: User = Depends(require_role("admin"))
) -> list[dict]:
    return [_out(u) for u in session.scalars(select(User).order_by(User.username))]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserIn,
    session: Session = Depends(get_session),
    _user: User = Depends(require_role("admin")),
) -> dict:
    _check_role(payload.role)
    if session.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "username already exists")

    user = User(
        username=payload.username,
        display_name=payload.display_name or payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    session.add(user)
    session.flush()
    return _out(user)


@router.patch("/{user_id}")
def update_user(
    user_id: str,
    payload: UserPatch,
    session: Session = Depends(get_session),
    actor: User = Depends(require_role("admin")),
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user")

    demoting = payload.role is not None and payload.role != "admin"
    disabling = payload.active is False
    if demoting or disabling:
        _guard_last_admin(session, user)

    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.role is not None:
        user.role = _check_role(payload.role)
    if payload.active is not None:
        user.active = payload.active

    # Any change to what someone may do takes effect now.
    if demoting or disabling or payload.role is not None:
        _revoke_sessions(session, user.id)

    session.flush()
    return _out(user)


@router.post("/{user_id}/password")
def reset_password(
    user_id: str,
    payload: PasswordReset,
    session: Session = Depends(get_session),
    _actor: User = Depends(require_role("admin")),
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user")
    user.password_hash = hash_password(payload.password)
    _revoke_sessions(session, user.id)
    session.flush()
    return {"username": user.username, "password_reset": True}


@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_role("admin")),
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user")
    if user.id == actor.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "you cannot delete your own account")
    _guard_last_admin(session, user)

    # Decisions and archive reads reference the username as free text, so the audit trail
    # survives the account being removed.
    _revoke_sessions(session, user.id)
    session.delete(user)
    return {"deleted": user_id}


def _permissions(role: str) -> dict[str, bool]:
    rank = ROLE_RANK.get(role, 0)
    return {
        "view_queue": rank >= 0,
        "decide_jobs": rank >= ROLE_RANK["analyst"],
        "request_content": rank >= ROLE_RANK["analyst"],
        "approve_content": rank >= ROLE_RANK["approver"],
        "manage_rules": rank >= ROLE_RANK["admin"],
        "manage_users": rank >= ROLE_RANK["admin"],
        "register_documents": rank >= ROLE_RANK["admin"],
    }
