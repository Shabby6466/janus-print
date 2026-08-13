"""Console authentication and role gates.

scrypt from the stdlib rather than a bcrypt dependency — one fewer native wheel on a box
that sits in the print path.

Roles, least-privileged first:

    viewer    read the queue, jobs, rules and policies. Cannot decide anything.
    analyst   everything above, plus release/deny held jobs and request content access
    approver  everything above, plus approving someone else's content request
    admin     everything, plus user management, rule editing, and document registration

Ranked, so `require_role("analyst")` admits analyst, approver and admin. The ranking is
the whole model — there are no per-endpoint permission grants to drift out of sync.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..models import Session as SessionRow
from ..models import User

SESSION_COOKIE = "janus_print_session"
ROLE_RANK = {"viewer": 0, "analyst": 1, "approver": 2, "admin": 3}
ROLES = tuple(ROLE_RANK)

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(expected.hex(), digest_hex)


def create_session(session: Session, user: User) -> str:
    token = secrets.token_urlsafe(48)
    expires = datetime.now(UTC) + timedelta(seconds=get_settings().session_ttl_seconds)
    session.add(SessionRow(token=token, user_id=user.id, expires_at=expires))
    session.flush()
    return token


def destroy_session(session: Session, token: str) -> None:
    row = session.get(SessionRow, token)
    if row is not None:
        session.delete(row)


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username))
    if user is None or not user.active:
        # Hash anyway so a missing user and a wrong password take the same time.
        verify_password(password, "scrypt$00$00")
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def current_user_optional(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = session.get(SessionRow, token)
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        session.delete(row)
        return None
    user = session.get(User, row.user_id)
    return user if user is not None and user.active else None


def current_user(user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return user


def require_role(minimum: str):
    """Dependency factory — `Depends(require_role("approver"))`."""

    def dependency(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires {minimum} role")
        return user

    return dependency


def ensure_admin_user(session: Session, username: str, password: str) -> User:
    """Idempotent bootstrap for the lab and first install."""
    existing = session.scalar(select(User).where(User.username == username))
    if existing is not None:
        return existing
    user = User(
        username=username,
        display_name=username,
        password_hash=hash_password(password),
        role="admin",
    )
    session.add(user)
    session.flush()
    return user
