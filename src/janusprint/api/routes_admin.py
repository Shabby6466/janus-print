"""Rules, fingerprint corpus, and the archive access log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from pydantic import BaseModel, Field

from ..archive.retention import purge_expired
from ..db import get_session
from ..inspector import store as rule_store
from ..inspector.engine import get_ruleset, register_document, reload_rules
from ..inspector.rules import test_fixtures
from ..models import ArchiveAccess, RegisteredDocument, RuleRow, User
from ..schemas import RegisterDocumentResponse
from .auth import current_user, require_role

router = APIRouter(tags=["admin"])


class RuleContextIn(BaseModel):
    terms: list[str] = Field(default_factory=list)
    window: int = 50
    boost: float = 0.3
    required: bool = False


class RuleFixturesIn(BaseModel):
    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class RuleIn(BaseModel):
    """What the console submits. Deliberately mirrors the YAML shape so a rule authored in
    the UI and one authored in a file are the same object."""

    id: str | None = None
    name: str
    description: str = ""
    pattern: str
    action: str = "log"
    severity: int = 5
    validator: str | None = None
    validator_weight: float = 0.3
    base_confidence: float = 0.6
    threshold: float = 0.75
    min_count: int = 1
    ignore_case: bool = True
    sample_prefix: int = 4
    sample_suffix: int = 4
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    context: RuleContextIn = Field(default_factory=RuleContextIn)
    fixtures: RuleFixturesIn = Field(default_factory=RuleFixturesIn)
    note: str = ""


class RuleTryIn(BaseModel):
    rule: RuleIn
    sample_text: str = Field(min_length=1, max_length=100_000)


def _rule_out(row: RuleRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "pattern": row.pattern,
        "action": row.action,
        "severity": row.severity,
        "validator": row.validator,
        "validator_weight": row.validator_weight,
        "base_confidence": row.base_confidence,
        "threshold": row.threshold,
        "min_count": row.min_count,
        "ignore_case": row.ignore_case,
        "sample_prefix": row.sample_prefix,
        "sample_suffix": row.sample_suffix,
        "tags": row.tags,
        "context": row.context,
        "fixtures": row.fixtures,
        "enabled": row.enabled,
        "source": row.source,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


def _validation_error(exc: rule_store.RuleValidationError) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        {"error": str(exc), "failures": exc.failures},
    )


@router.get("/rules")
def list_rules(
    session: Session = Depends(get_session), _user: User = Depends(current_user)
) -> list[dict]:
    rows = session.scalars(select(RuleRow).order_by(RuleRow.severity.desc(), RuleRow.id)).all()
    return [_rule_out(row) for row in rows]


@router.get("/rules/{rule_id}")
def get_rule(
    rule_id: str, session: Session = Depends(get_session), _user: User = Depends(current_user)
) -> dict:
    row = session.get(RuleRow, rule_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such rule")
    return _rule_out(row)


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: RuleIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Create a rule from the console. It is compiled and run against its own fixtures
    before it is stored — an invalid rule can never reach the inspection path."""
    body = payload.model_dump()
    note = body.pop("note", "")
    try:
        row = rule_store.create_rule(session, body, actor=user.username, note=note)
    except rule_store.RuleValidationError as exc:
        raise _validation_error(exc) from exc
    return _rule_out(row)


@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: str,
    payload: RuleIn,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    body = payload.model_dump(exclude_unset=True)
    body.pop("id", None)
    note = body.pop("note", "")
    try:
        row = rule_store.update_rule(session, rule_id, body, actor=user.username, note=note)
    except rule_store.RuleValidationError as exc:
        raise _validation_error(exc) from exc
    return _rule_out(row)


@router.post("/rules/{rule_id}/enabled")
def set_rule_enabled(
    rule_id: str,
    enabled: bool = True,
    note: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    """Disabling is preferred over deleting — it keeps the rule available to re-enable and
    leaves the history intact."""
    try:
        row = rule_store.set_enabled(session, rule_id, enabled, actor=user.username, note=note)
    except rule_store.RuleValidationError as exc:
        raise _validation_error(exc) from exc
    return _rule_out(row)


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: str,
    note: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> dict:
    try:
        rule_store.delete_rule(session, rule_id, actor=user.username, note=note)
    except rule_store.RuleValidationError as exc:
        raise _validation_error(exc) from exc
    return {"deleted": rule_id}


@router.post("/rules/try")
def try_rule(payload: RuleTryIn, _user: User = Depends(current_user)) -> dict:
    """Evaluate a candidate rule against pasted text without saving it.

    This is what makes rule authoring safe for a non-programmer: you see exactly what the
    pattern matches, and how the sample is masked, before it touches real print traffic.
    """
    body = payload.rule.model_dump()
    body.pop("note", "")
    body.setdefault("id", "preview")
    body["id"] = body["id"] or "preview"
    try:
        return rule_store.try_rule(body, payload.sample_text)
    except rule_store.RuleValidationError as exc:
        raise _validation_error(exc) from exc


@router.get("/rule-revisions")
def rule_revisions(
    rule_id: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[dict]:
    """Who changed detection, when, and why. A weakened rule looks identical to a working
    one until something is missed."""
    return [
        {
            "at": rev.at,
            "rule_id": rev.rule_id,
            "actor": rev.actor,
            "change": rev.change,
            "note": rev.note,
        }
        for rev in rule_store.revisions(session, rule_id, min(limit, 500))
    ]


@router.post("/rules/reload")
def reload(_user: User = Depends(require_role("admin"))) -> dict:
    """Pick up edited YAML without a restart. Fixtures are checked first — a rule change
    that regresses the corpus is rejected rather than deployed."""
    count = reload_rules()
    failures = test_fixtures(get_ruleset())
    if failures:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "error": "rules loaded but fixtures fail",
                "failures": [
                    {"rule_id": f.rule_id, "kind": f.kind, "text": f.text[:120]}
                    for f in failures[:20]
                ],
            },
        )
    return {"rules_loaded": count, "fixture_failures": 0}


@router.get("/rules-test")
def run_fixture_tests(_user: User = Depends(current_user)) -> dict:
    failures = test_fixtures(get_ruleset())
    return {
        "rules": len(get_ruleset()),
        "failures": [
            {"rule_id": f.rule_id, "kind": f.kind, "text": f.text[:120]} for f in failures
        ],
    }


@router.get("/policies")
def list_policies(_user: User = Depends(current_user)) -> dict:
    """Effective per-queue policy, including queues with no explicit entry."""
    from ..config import get_printer_policies

    policies = get_printer_policies()
    return {
        "default": policies.default.model_dump(),
        "queues": {name: policy.model_dump() for name, policy in policies.queues.items()},
    }


@router.post("/policies/reload")
def reload_policies(_user: User = Depends(require_role("admin"))) -> dict:
    """Re-read config/printers.yaml without restarting.

    Policy is cached for the process lifetime, so adding a printer entry otherwise appears
    to do nothing until the next deploy — and the queue silently runs on the default
    policy in the meantime.
    """
    from ..config import get_printer_policies, reset_caches

    reset_caches()
    policies = get_printer_policies()
    return {
        "queues_loaded": len(policies.queues),
        "queues": sorted(policies.queues),
    }


@router.get("/documents")
def list_documents(
    session: Session = Depends(get_session), _user: User = Depends(current_user)
) -> list[dict]:
    documents = session.scalars(
        select(RegisteredDocument).order_by(RegisteredDocument.created_at.desc())
    )
    return [
        {
            "id": d.id,
            "name": d.name,
            "owner": d.owner,
            "severity": d.severity,
            "action": d.action,
            "shingle_count": d.shingle_count,
            "enabled": d.enabled,
            "created_at": d.created_at,
        }
        for d in documents
    ]


@router.post("/documents", response_model=RegisterDocumentResponse)
async def add_document(
    document: UploadFile = File(...),
    name: str = Form(""),
    severity: int = Form(7),
    action: str = Form("hold"),
    session: Session = Depends(get_session),
    user: User = Depends(require_role("admin")),
) -> RegisterDocumentResponse:
    """Register a document as sensitive. Its fingerprints then catch excerpts and
    reworded copies, not just byte-identical files."""
    data = await document.read()
    try:
        registered = register_document(
            session,
            name=name or document.filename or "unnamed",
            data=data,
            owner=user.username,
            severity=severity,
            action=action,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return RegisterDocumentResponse(
        id=registered.id,
        name=registered.name,
        shingle_count=registered.shingle_count,
        exact_sha256=registered.exact_sha256,
    )


@router.delete("/documents/{document_id}")
def remove_document(
    document_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(require_role("admin")),
) -> dict:
    document = session.get(RegisteredDocument, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    session.delete(document)
    return {"deleted": document_id}


@router.get("/archive-access")
def archive_access_log(
    limit: int = 200,
    session: Session = Depends(get_session),
    _user: User = Depends(require_role("approver")),
) -> list[dict]:
    """Who read what, and when. The archive watches its watchers (PLAN.md §6)."""
    rows = session.scalars(
        select(ArchiveAccess).order_by(ArchiveAccess.at.desc()).limit(min(limit, 1000))
    )
    return [
        {
            "at": r.at,
            "job_id": r.job_id,
            "actor": r.actor,
            "kind": r.kind,
            "request_id": r.request_id,
            "source_ip": r.source_ip,
            "detail": r.detail,
        }
        for r in rows
    ]


@router.post("/retention/purge")
def run_purge(_user: User = Depends(require_role("admin"))) -> dict:
    return {"purged": purge_expired()}


@router.get("/dashboard/stats")
def dashboard_stats(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict:
    from sqlalchemy import func
    from ..models import ContentRequest, ContentRequestState, Job, JobState
    from . import cups_control

    held = list(
        session.scalars(
            select(Job).where(Job.state == JobState.held).order_by(Job.created_at.desc()).limit(20)
        )
    )
    recent = list(session.scalars(select(Job).order_by(Job.created_at.desc()).limit(20)))
    counts = {s.value: 0 for s in JobState}
    for state, count in session.execute(
        select(Job.state, func.count(Job.id)).group_by(Job.state)
    ).all():
        if state is not None:
            counts[state.value] = count

    gaps = session.scalar(
        select(func.count(Job.id)).where(Job.state == JobState.failed_open)
    ) or 0
    pending_requests = session.scalar(
        select(func.count(ContentRequest.id)).where(
            ContentRequest.state == ContentRequestState.pending
        )
    ) or 0

    return {
        "counts": counts,
        "gaps": gaps,
        "pending_requests": pending_requests,
        "rules_loaded": len(get_ruleset(session)),
        "cups_mode": cups_control.status()["mode"],
        "held": [
            {
                "id": j.id,
                "title": j.title,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "username": j.username,
                "hostname": j.hostname,
                "queue": j.queue,
                "state": j.state.value if j.state else None,
                "verdict_reason": j.verdict_reason,
                "score": j.score,
                "scan_tier": j.scan_tier.value if j.scan_tier else None,
                "page_count": j.page_count,
                "inline_ms": j.inline_ms,
            }
            for j in held
        ],
        "recent": [
            {
                "id": j.id,
                "title": j.title,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "username": j.username,
                "hostname": j.hostname,
                "queue": j.queue,
                "state": j.state.value if j.state else None,
                "verdict_reason": j.verdict_reason,
                "score": j.score,
                "scan_tier": j.scan_tier.value if j.scan_tier else None,
                "page_count": j.page_count,
                "inline_ms": j.inline_ms,
            }
            for j in recent
        ],
    }

