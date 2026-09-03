"""JSON authentication endpoints for the React frontend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import User
from .auth import (
    SESSION_COOKIE,
    authenticate,
    create_session,
    current_user,
    current_user_optional,
    destroy_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    active: bool


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> UserOut:
    user = authenticate(session, payload.username.strip(), payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_session(session, user)
    # Set cookie with lax samesite so it works on browser SPA requests
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # works over http in LAN/lab
        path="/",
    )
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
        active=user.active,
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        destroy_session(session, token)
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
        active=user.active,
    )
