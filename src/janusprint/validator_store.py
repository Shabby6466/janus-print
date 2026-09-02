"""Console-managed validators.

Rules narrow what a regex catches; validators are the part that decides whether a specific
match is *real* — a Luhn check is the difference between "every 16-digit number in the
building" and "actual payment cards". Until now the six shipped validators were the only
ones available, which meant any new national ID or account-number scheme needed a code
change.

That is fixed here the same way rules and printers were: database-backed, admin CRUD, a
mandatory fixtures gate before anything can save. The one constraint that doesn't get
relaxed is that a validator is code that runs against every document printed in the
building — so the console can only ever compose one of two safe, declarative shapes
(`inspector.validators.GENERIC_KINDS`), never arbitrary logic. The six original algorithms
stay protected Python, listed here for transparency but not editable — their real
behaviour (IBAN's letter rearrangement, SSN's never-issued ranges) doesn't reduce to the
generic shape and is worth keeping as tested code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .inspector import validators as validator_engine
from .models import RuleRow, ValidatorRevision, ValidatorRow

log = logging.getLogger(__name__)

# name -> (description, kind, params) — listed for transparency only. resolve() serves
# these directly from inspector.validators.BUILTINS; these rows are never used to compile
# a checker, only to show operators why a given entry in the console is protected.
_BUILTIN_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "none": ("No structural check — for phrases and keyword rules.", "python"),
    "luhn": ("Mod-10 check used by payment cards.", "python"),
    "iban": ("ISO 13616 mod-97 for bank account numbers.", "python"),
    "us_ssn": ("Rejects never-issued SSN ranges and documentation placeholders.", "python"),
    "nhs_number": ("UK NHS number, mod-11 with weights 10..2.", "python"),
    "mod11": ("Generic mod-11 checksum used by several national ID schemes.", "python"),
    "entropy": ("Shannon entropy gate for secrets and API keys.", "python"),
}


class ValidatorError(ValueError):
    pass


@dataclass(frozen=True)
class _Version:
    count: int
    latest: object

    @classmethod
    def probe(cls, session: Session) -> _Version:
        count, latest = session.execute(
            select(func.count(ValidatorRow.id), func.max(ValidatorRow.updated_at))
        ).one()
        return cls(count=count or 0, latest=latest)


_cached_version: _Version | None = None


def refresh_registry(session: Session, force: bool = False) -> int:
    """Rebuild the in-process checker cache from the database.

    resolve() runs on the print path, once per rule per page, so it must stay a plain dict
    lookup rather than a DB query. This is the one place that DB row -> compiled checker,
    called at startup and after every create/update/delete/toggle.
    """
    global _cached_version
    version = _Version.probe(session)
    if not force and _cached_version == version:
        return 0

    rows = session.scalars(
        select(ValidatorRow).where(
            ValidatorRow.builtin.is_(False), ValidatorRow.enabled.is_(True)
        )
    ).all()
    checkers = {
        row.id: validator_engine.make_checker(row.kind, row.params) for row in rows
    }
    validator_engine.set_custom_registry(checkers)
    _cached_version = version
    log.info("validator registry rebuilt: %d custom validators", len(checkers))
    return len(checkers)


def invalidate_cache() -> None:
    global _cached_version
    _cached_version = None


def seed_builtins(session: Session) -> int:
    """Insert transparency rows for the six protected algorithms. Never overwrites an
    existing row, so it is safe to call on every start."""
    existing = set(session.scalars(select(ValidatorRow.id)))
    added = 0
    for name, (description, kind) in _BUILTIN_DESCRIPTIONS.items():
        if name in existing:
            continue
        session.add(
            ValidatorRow(
                id=name,
                name=name.replace("_", " ").title(),
                description=description,
                kind=kind,
                builtin=True,
                updated_by="seed",
            )
        )
        added += 1
    if added:
        session.flush()
        log.info("seeded %d builtin validator entries", added)
    return added


def _prove_fixtures(kind: str, params: dict, fixtures: dict) -> list[str]:
    """Run the checker against its own fixtures. Same role as rule fixtures: a validator
    with a typo'd weight vector either rejects everything or accepts everything, and both
    look identical to correct until a real document exposes it — unless this catches it
    first."""
    checker = validator_engine.make_checker(kind, params)
    failures: list[str] = []
    for sample in fixtures.get("pass", []):
        if not checker(sample):
            failures.append(f"should PASS but did not: {sample!r}")
    for sample in fixtures.get("fail", []):
        if checker(sample):
            failures.append(f"should FAIL but passed: {sample!r}")
    return failures


def validate(payload: dict) -> dict:
    kind = payload.get("kind")
    params = payload.get("params") or {}
    fixtures = payload.get("fixtures") or {}

    validator_engine.validate_params(kind, params)

    if not fixtures.get("pass") or not fixtures.get("fail"):
        raise ValidatorError(
            "at least one PASS example and one FAIL example are required — this is what "
            "proves the checksum is actually checking something before it runs against "
            "real print traffic"
        )

    failures = _prove_fixtures(kind, params, fixtures)
    if failures:
        raise ValidatorError("fixtures failed: " + "; ".join(failures))

    return payload


def _snapshot(row: ValidatorRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "kind": row.kind,
        "params": row.params,
        "fixtures": row.fixtures,
        "enabled": row.enabled,
    }


def _record(session: Session, row: ValidatorRow, actor: str, change: str, note: str) -> None:
    session.add(
        ValidatorRevision(
            validator_id=row.id, actor=actor, change=change, note=note, snapshot=_snapshot(row)
        )
    )


def create(session: Session, payload: dict, actor: str, note: str = "") -> ValidatorRow:
    validator_id = (payload.get("id") or "").strip()
    if not validator_id:
        raise ValidatorError("id is required")
    if not validator_id.replace("-", "").replace("_", "").isalnum():
        raise ValidatorError("id may contain only letters, digits, hyphen and underscore")
    if session.get(ValidatorRow, validator_id) is not None:
        raise ValidatorError(f"a validator named {validator_id!r} already exists")

    validate(payload)
    row = ValidatorRow(
        id=validator_id,
        name=payload.get("name") or validator_id,
        description=payload.get("description", ""),
        kind=payload["kind"],
        params=payload.get("params") or {},
        fixtures=payload.get("fixtures") or {},
        builtin=False,
        updated_by=actor,
    )
    session.add(row)
    session.flush()
    _record(session, row, actor, "created", note)
    refresh_registry(session, force=True)
    return row


def update(session: Session, validator_id: str, payload: dict, actor: str, note: str = "") -> ValidatorRow:
    row = session.get(ValidatorRow, validator_id)
    if row is None:
        raise ValidatorError(f"no such validator: {validator_id}")
    if row.builtin:
        raise ValidatorError(
            f"{validator_id!r} is a protected built-in algorithm and cannot be edited"
        )

    merged = _snapshot(row) | payload
    validate(merged)

    row.name = merged.get("name", row.name)
    row.description = merged.get("description", row.description)
    row.kind = merged["kind"]
    row.params = merged["params"]
    row.fixtures = merged["fixtures"]
    if "enabled" in payload:
        row.enabled = bool(payload["enabled"])
    row.updated_by = actor

    session.flush()
    _record(session, row, actor, "updated", note)
    refresh_registry(session, force=True)
    return row


def delete(session: Session, validator_id: str, actor: str, note: str = "") -> None:
    row = session.get(ValidatorRow, validator_id)
    if row is None:
        raise ValidatorError(f"no such validator: {validator_id}")
    if row.builtin:
        raise ValidatorError(f"{validator_id!r} is a protected built-in algorithm and cannot be removed")

    in_use = session.scalars(
        select(RuleRow.id).where(RuleRow.validator == validator_id, RuleRow.enabled.is_(True))
    ).all()
    if in_use:
        raise ValidatorError(
            f"still used by {len(in_use)} enabled rule(s): {', '.join(in_use[:5])} — "
            f"change or disable those rules first"
        )

    _record(session, row, actor, "deleted", note)
    session.delete(row)
    session.flush()
    refresh_registry(session, force=True)


def revisions(session: Session, validator_id: str | None = None, limit: int = 200) -> list[ValidatorRevision]:
    query = select(ValidatorRevision).order_by(ValidatorRevision.at.desc()).limit(limit)
    if validator_id:
        query = query.where(ValidatorRevision.validator_id == validator_id)
    return list(session.scalars(query))


def try_validator(kind: str, params: dict, sample: str) -> dict:
    """Check one sample without saving anything — the same role /rules/try plays."""
    validator_engine.validate_params(kind, params)
    checker = validator_engine.make_checker(kind, params)
    return {"sample": sample, "passes": checker(sample)}
