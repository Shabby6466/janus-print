"""Database-backed rule storage.

Rules used to live only in YAML, which meant every policy change was a file edit and a
redeploy. They now live in the database and are editable from the console; the YAML packs
seed the table once, on first start.

Two things this file is careful about:

  * **Validation happens before persistence.** A rule is round-tripped through the Pydantic
    `Rule` model and its fixtures are run before it can be saved. A rule that cannot compile
    or that fails its own fixtures never reaches the table, so the inspector cannot be
    broken from the UI.

  * **Cache coherence across processes.** The API and the worker are separate processes, so
    an in-process cache would let one of them keep evaluating deleted rules indefinitely.
    The cache is keyed on a cheap (count, max(updated_at)) probe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import RuleRevision, RuleRow
from .rules import ContextSpec, Rule, RuleFixtures, RuleSet, load_rules, test_fixtures
from .validators import UnknownValidator, resolve

log = logging.getLogger(__name__)


class RuleValidationError(ValueError):
    """Raised when a submitted rule is malformed or fails its own fixtures."""

    def __init__(self, message: str, failures: list[dict] | None = None) -> None:
        super().__init__(message)
        self.failures = failures or []


def row_to_rule(row: RuleRow) -> Rule:
    return Rule(
        id=row.id,
        name=row.name,
        description=row.description or "",
        pattern=row.pattern,
        action=row.action,
        severity=row.severity,
        validator=row.validator,
        validator_weight=row.validator_weight,
        base_confidence=row.base_confidence,
        threshold=row.threshold,
        min_count=row.min_count,
        ignore_case=row.ignore_case,
        sample_prefix=row.sample_prefix,
        sample_suffix=row.sample_suffix,
        tags=list(row.tags or []),
        enabled=row.enabled,
        context=ContextSpec(**(row.context or {})),
        fixtures=RuleFixtures(**(row.fixtures or {})),
    )


def rule_to_fields(rule: Rule) -> dict:
    return {
        "name": rule.name,
        "description": rule.description,
        "pattern": rule.pattern,
        "action": rule.action,
        "severity": rule.severity,
        "validator": rule.validator,
        "validator_weight": rule.validator_weight,
        "base_confidence": rule.base_confidence,
        "threshold": rule.threshold,
        "min_count": rule.min_count,
        "ignore_case": rule.ignore_case,
        "sample_prefix": rule.sample_prefix,
        "sample_suffix": rule.sample_suffix,
        "tags": rule.tags,
        "context": rule.context.model_dump(),
        "fixtures": rule.fixtures.model_dump(),
        "enabled": rule.enabled,
    }


def validate(payload: dict, *, require_fixtures: bool = True) -> Rule:
    """Build and prove a rule before it is allowed anywhere near the table."""
    try:
        rule = Rule(**payload)
    except Exception as exc:
        raise RuleValidationError(str(exc)) from exc

    # Resolve the validator here rather than letting it raise later during evaluation —
    # an unknown validator name must be a save-time rejection, not a runtime surprise on
    # the print path.
    try:
        resolve(rule.validator)
    except UnknownValidator as exc:
        raise RuleValidationError(str(exc)) from exc

    try:
        failures = test_fixtures(RuleSet([rule])) if rule.enabled else []
    except Exception as exc:
        raise RuleValidationError(f"rule could not be evaluated: {exc}") from exc
    if failures and require_fixtures:
        raise RuleValidationError(
            "rule does not satisfy its own fixtures",
            [{"kind": f.kind, "text": f.text[:200]} for f in failures],
        )
    return rule


# --- versioning --------------------------------------------------------------


@dataclass(frozen=True)
class RuleVersion:
    count: int
    latest: datetime | None

    @classmethod
    def probe(cls, session: Session) -> RuleVersion:
        count, latest = session.execute(
            select(func.count(RuleRow.id), func.max(RuleRow.updated_at))
        ).one()
        return cls(count=count or 0, latest=latest)


_cached: tuple[RuleVersion, RuleSet] | None = None


def load_ruleset(session: Session) -> RuleSet:
    """Return the active ruleset, rebuilt only when the table has actually changed."""
    global _cached
    version = RuleVersion.probe(session)
    if _cached is not None and _cached[0] == version:
        return _cached[1]

    rows = session.scalars(select(RuleRow).where(RuleRow.enabled.is_(True))).all()
    ruleset = RuleSet([row_to_rule(row) for row in rows])
    _cached = (version, ruleset)
    log.info("rule cache rebuilt: %d enabled rules", len(ruleset))
    return ruleset


def invalidate_cache() -> None:
    global _cached
    _cached = None


# --- seeding -----------------------------------------------------------------


def seed_from_yaml(session: Session, directory: Path | None = None) -> int:
    """Import the shipped YAML packs. Only ever adds rules that do not already exist, so
    an operator's edits are never silently reverted by a restart."""
    try:
        packs = load_rules(directory)
    except FileNotFoundError:
        log.warning("no rules directory to seed from")
        return 0

    existing = set(session.scalars(select(RuleRow.id)))
    added = 0
    for rule in packs.rules:
        if rule.id in existing:
            continue
        session.add(
            RuleRow(id=rule.id, source="yaml", updated_by="seed", **rule_to_fields(rule))
        )
        session.add(
            RuleRevision(
                rule_id=rule.id,
                actor="seed",
                change="created",
                note="imported from the shipped YAML rule pack",
                snapshot=rule_to_fields(rule),
            )
        )
        added += 1

    if added:
        session.flush()
        invalidate_cache()
        log.info("seeded %d rules from YAML", added)
    return added


# --- CRUD --------------------------------------------------------------------


def create_rule(session: Session, payload: dict, actor: str, note: str = "") -> RuleRow:
    rule_id = (payload.get("id") or "").strip()
    if not rule_id:
        raise RuleValidationError("id is required")
    if session.get(RuleRow, rule_id) is not None:
        raise RuleValidationError(f"rule id {rule_id!r} already exists")

    rule = validate(payload)
    row = RuleRow(id=rule.id, source="console", updated_by=actor, **rule_to_fields(rule))
    session.add(row)
    session.add(
        RuleRevision(
            rule_id=rule.id,
            actor=actor,
            change="created",
            note=note,
            snapshot=rule_to_fields(rule),
        )
    )
    session.flush()
    invalidate_cache()
    return row


def update_rule(session: Session, rule_id: str, payload: dict, actor: str, note: str = "") -> RuleRow:
    row = session.get(RuleRow, rule_id)
    if row is None:
        raise RuleValidationError(f"no such rule: {rule_id}")

    merged = rule_to_fields(row_to_rule(row)) | payload
    merged["id"] = rule_id
    rule = validate(merged)

    for key, value in rule_to_fields(rule).items():
        setattr(row, key, value)
    row.updated_by = actor

    session.add(
        RuleRevision(
            rule_id=rule_id,
            actor=actor,
            change="updated",
            note=note,
            snapshot=rule_to_fields(rule),
        )
    )
    session.flush()
    invalidate_cache()
    return row


def set_enabled(session: Session, rule_id: str, enabled: bool, actor: str, note: str = "") -> RuleRow:
    row = session.get(RuleRow, rule_id)
    if row is None:
        raise RuleValidationError(f"no such rule: {rule_id}")
    row.enabled = enabled
    row.updated_by = actor
    session.add(
        RuleRevision(
            rule_id=rule_id,
            actor=actor,
            change="enabled" if enabled else "disabled",
            note=note,
            snapshot=rule_to_fields(row_to_rule(row)),
        )
    )
    session.flush()
    invalidate_cache()
    return row


def delete_rule(session: Session, rule_id: str, actor: str, note: str = "") -> None:
    row = session.get(RuleRow, rule_id)
    if row is None:
        raise RuleValidationError(f"no such rule: {rule_id}")
    snapshot = rule_to_fields(row_to_rule(row))
    session.delete(row)
    # The revision outlives the rule — deleting a detection must stay answerable.
    session.add(
        RuleRevision(
            rule_id=rule_id, actor=actor, change="deleted", note=note, snapshot=snapshot
        )
    )
    session.flush()
    invalidate_cache()


def revisions(session: Session, rule_id: str | None = None, limit: int = 100) -> list[RuleRevision]:
    query = select(RuleRevision).order_by(RuleRevision.at.desc()).limit(limit)
    if rule_id:
        query = query.where(RuleRevision.rule_id == rule_id)
    return list(session.scalars(query))


def try_rule(payload: dict, sample_text: str) -> dict:
    """Evaluate a candidate rule against pasted text without saving it.

    The feature that makes rule authoring safe: an operator sees exactly what their regex
    matches, and what it masks, before it is ever applied to real print traffic.
    """
    rule = validate(payload, require_fixtures=False)
    hits = RuleSet([rule]).evaluate_page(sample_text, page=1)
    fixture_failures = test_fixtures(RuleSet([rule]))
    return {
        "fires": bool(hits),
        "matches": [
            {"count": hit.count, "score": round(hit.score, 3), "sample": hit.sample}
            for hit in hits
        ],
        "action": rule.action if hits else "allow",
        "fixture_failures": [
            {"kind": f.kind, "text": f.text[:200]} for f in fixture_failures
        ],
    }
