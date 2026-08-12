"""Rules, fingerprint corpus, and the archive access log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..archive.retention import purge_expired
from ..db import get_session
from ..inspector.engine import get_ruleset, register_document, reload_rules
from ..inspector.rules import test_fixtures
from ..models import ArchiveAccess, RegisteredDocument, User
from ..schemas import RegisterDocumentResponse
from .auth import current_user, require_role

router = APIRouter(tags=["admin"])


@router.get("/rules")
def list_rules(_user: User = Depends(current_user)) -> list[dict]:
    return [
        {
            "id": rule.id,
            "name": rule.name,
            "action": rule.action,
            "severity": rule.severity,
            "validator": rule.validator,
            "threshold": rule.threshold,
            "min_count": rule.min_count,
            "tags": rule.tags,
            "description": rule.description,
            "fixtures": {
                "positive": len(rule.fixtures.positive),
                "negative": len(rule.fixtures.negative),
            },
        }
        for rule in get_ruleset().rules
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


@router.get("/rules/test")
def run_fixture_tests(_user: User = Depends(current_user)) -> dict:
    failures = test_fixtures(get_ruleset())
    return {
        "rules": len(get_ruleset()),
        "failures": [
            {"rule_id": f.rule_id, "kind": f.kind, "text": f.text[:120]} for f in failures
        ],
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
