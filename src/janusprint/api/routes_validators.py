"""Validator management.

A validator is code that runs against every document printed in the building, so this
never accepts arbitrary logic from the console — only the two safe declarative shapes in
`inspector.validators.GENERIC_KINDS` (weighted-sum-mod-N checksums, and entropy). See
`validator_store` for the reasoning and the fixtures gate that proves a submission actually
checks something before it can save.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import validator_store
from ..db import get_session
from ..inspector import validators as validator_engine
from ..models import User, ValidatorRow
from .auth import current_user, require_role

router = APIRouter(prefix="/validators", tags=["validators"])


class ValidatorFixturesIn(BaseModel):
    passing: list[str] = Field(default_factory=list, alias="pass")
    failing: list[str] = Field(default_factory=list, alias="fail")

    model_config = {"populate_by_name": True}

    def to_dict(self) -> dict:
        return {"pass": self.passing, "fail": self.failing}


class ValidatorIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = ""
    description: str = ""
    kind: str
    params: dict = Field(default_factory=dict)
    fixtures: ValidatorFixturesIn = Field(default_factory=ValidatorFixturesIn)
    note: str = ""


class ValidatorPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    kind: str | None = None
    params: dict | None = None
    fixtures: ValidatorFixturesIn | None = None
    enabled: bool | None = None
    note: str = ""


class ValidatorTryIn(BaseModel):
    kind: str
    params: dict = Field(default_factory=dict)
    sample: str = Field(min_length=1, max_length=1000)


def _out(row: ValidatorRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "kind": row.kind,
        "params": row.params,
        "fixtures": row.fixtures,
        "builtin": row.builtin,
        "enabled": row.enabled,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.get("")
def list_validators(
    session: Session = Depends(get_session), _user: User = Depends(current_user)
) -> list[dict]:
    rows = session.scalars(
        select(ValidatorRow).order_by(ValidatorRow.builtin.desc(), ValidatorRow.id)
    ).all()
    return [_out(row) for row in rows]


@router.get("/kinds")
def list_kinds(_user: User = Depends(current_user)) -> dict:
    """What a new validator can be built from, and what each param means."""
    return validator_engine.KIND_DOCS


@router.post("", status_code=status.HTTP_201_CREATED)
def create_validator(
    payload: ValidatorIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    body = payload.model_dump(exclude_unset=True)
    note = body.pop("note", "")
    body["fixtures"] = payload.fixtures.to_dict()
    try:
        row = validator_store.create(session, body, actor=user.username, note=note)
    except (validator_store.ValidatorError, validator_engine.InvalidValidatorParams) as exc:
        raise _error(exc) from exc
    return _out(row)


@router.patch("/{validator_id}")
def update_validator(
    validator_id: str,
    payload: ValidatorPatch,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    body = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    note = body.pop("note", "")
    if payload.fixtures is not None:
        body["fixtures"] = payload.fixtures.to_dict()
    try:
        row = validator_store.update(session, validator_id, body, actor=user.username, note=note)
    except (validator_store.ValidatorError, validator_engine.InvalidValidatorParams) as exc:
        raise _error(exc) from exc
    return _out(row)


@router.delete("/{validator_id}")
def delete_validator(
    validator_id: str,
    note: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    try:
        validator_store.delete(session, validator_id, actor=user.username, note=note)
    except validator_store.ValidatorError as exc:
        raise _error(exc) from exc
    return {"deleted": validator_id}


@router.post("/try")
def try_validator(payload: ValidatorTryIn, _user: User = Depends(current_user)) -> dict:
    """Check one sample without saving — the same role /rules/try plays for patterns."""
    try:
        return validator_store.try_validator(payload.kind, payload.params, payload.sample)
    except (validator_store.ValidatorError, validator_engine.InvalidValidatorParams) as exc:
        raise _error(exc) from exc


@router.get("/revisions")
def validator_revisions(
    validator_id: str | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[dict]:
    return [
        {
            "at": rev.at,
            "validator_id": rev.validator_id,
            "actor": rev.actor,
            "change": rev.change,
            "note": rev.note,
        }
        for rev in validator_store.revisions(session, validator_id, min(limit, 500))
    ]
