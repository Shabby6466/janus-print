"""Job queue, decisions, and the gated content-access path."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..archive.store import get_archive
from ..bridge.cef import get_bridge
from ..config import get_settings
from ..db import get_session
from ..models import ArchiveAccess, ContentRequest, ExtractedText, Job, JobEvent, JobState, User
from ..schemas import ContentRequestOut, DecisionRequest, JobDetailOut, JobOut
from . import cups_control
from .auth import current_user, require_role

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])

CONTENT_GRANT_TTL = timedelta(hours=2)


def _get_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


def _audit(
    session: Session,
    job: Job,
    actor: str,
    kind: str,
    request: Request,
    request_id: str | None = None,
    detail: str = "",
) -> None:
    session.add(
        ArchiveAccess(
            job_id=job.id,
            actor=actor,
            kind=kind,
            request_id=request_id,
            source_ip=request.client.host if request.client else "",
            detail=detail,
        )
    )


@router.get("", response_model=list[JobOut])
def list_jobs(
    state: str | None = None,
    queue: str | None = None,
    username: str | None = None,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[Job]:
    query = select(Job).order_by(Job.created_at.desc())
    if state:
        query = query.where(Job.state == JobState(state))
    if queue:
        query = query.where(Job.queue == queue)
    if username:
        query = query.where(Job.username == username)
    return list(session.scalars(query.limit(min(limit, 500)).offset(offset)))


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job(
    job_id: str, session: Session = Depends(get_session), _user: User = Depends(current_user)
) -> Job:
    return _get_job(session, job_id)


@router.post("/{job_id}/release", response_model=JobDetailOut)
def release_job(
    job_id: str,
    decision: DecisionRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Job:
    """Let a held job print. The reason is mandatory and permanent."""
    job = _get_job(session, job_id)
    if job.state != JobState.held:
        raise HTTPException(status.HTTP_409_CONFLICT, f"job is {job.state.value}, not held")

    # Recorded before touching CUPS so the preflight grant exists even if lp is slow.
    job.state = JobState.released_by_analyst
    job.verdict_reason = decision.reason
    session.add(
        JobEvent(job_id=job.id, kind="released", actor=user.username, detail=decision.reason)
    )
    session.flush()

    try:
        cups_control.release(job.queue, job.cups_job_id)
    except cups_control.CupsControlError as exc:
        log.error("release of %s recorded but CUPS refused: %s", job.id, exc)
        session.add(
            JobEvent(job_id=job.id, kind="cups_release_failed", actor="system", detail=str(exc))
        )

    get_bridge().send_operational(
        "PRINT_RELEASED_BY_ANALYST",
        "Held print job released after review",
        4,
        cs1=job.id,
        cs1Label="jobId",
        suser=job.username,
        duser=user.username,
        dproc=job.queue,
        msg=decision.reason[:512],
    )
    return job


@router.post("/{job_id}/deny", response_model=JobDetailOut)
def deny_job(
    job_id: str,
    decision: DecisionRequest,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Job:
    job = _get_job(session, job_id)
    if job.state != JobState.held:
        raise HTTPException(status.HTTP_409_CONFLICT, f"job is {job.state.value}, not held")

    job.state = JobState.denied_by_analyst
    job.verdict_reason = decision.reason
    session.add(
        JobEvent(job_id=job.id, kind="denied", actor=user.username, detail=decision.reason)
    )
    session.flush()

    try:
        cups_control.cancel(job.queue, job.cups_job_id)
    except cups_control.CupsControlError as exc:
        log.error("deny of %s recorded but CUPS refused: %s", job.id, exc)
        session.add(
            JobEvent(job_id=job.id, kind="cups_cancel_failed", actor="system", detail=str(exc))
        )

    get_bridge().send_operational(
        "PRINT_DENIED_BY_ANALYST",
        "Held print job denied after review",
        6,
        cs1=job.id,
        cs1Label="jobId",
        suser=job.username,
        duser=user.username,
        dproc=job.queue,
        msg=decision.reason[:512],
    )
    return job


# --- gated content access ----------------------------------------------------


@router.post("/{job_id}/content-requests", response_model=ContentRequestOut)
def request_content(
    job_id: str,
    decision: DecisionRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ContentRequest:
    """Ask to read the archived document. A second person has to approve (PLAN.md §6)."""
    job = _get_job(session, job_id)
    if job.purged_at is not None:
        raise HTTPException(status.HTTP_410_GONE, "archived content has been purged")

    content_request = ContentRequest(
        job_id=job.id,
        requester=user.username,
        reason=decision.reason,
        expires_at=datetime.now(UTC) + CONTENT_GRANT_TTL,
    )
    session.add(content_request)
    session.flush()

    get_bridge().send_operational(
        "ARCHIVE_ACCESS_REQUESTED",
        "Analyst requested archived print content",
        5,
        cs1=job.id,
        cs1Label="jobId",
        duser=user.username,
        msg=decision.reason[:512],
    )
    return content_request


@router.get("/{job_id}/content-requests", response_model=list[ContentRequestOut])
def list_content_requests(
    job_id: str, session: Session = Depends(get_session), _user: User = Depends(current_user)
) -> list[ContentRequest]:
    return list(
        session.scalars(
            select(ContentRequest)
            .where(ContentRequest.job_id == job_id)
            .order_by(ContentRequest.created_at.desc())
        )
    )


@router.post("/content-requests/{request_id}/approve", response_model=ContentRequestOut)
def approve_content(
    request_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_role("approver")),
) -> ContentRequest:
    content_request = session.get(ContentRequest, request_id)
    if content_request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "request not found")
    if content_request.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"already {content_request.status}")
    if content_request.requester == user.username:
        # The entire point of the gate.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot approve your own request")

    content_request.status = "approved"
    content_request.approver = user.username
    content_request.decided_at = datetime.now(UTC)
    content_request.expires_at = datetime.now(UTC) + CONTENT_GRANT_TTL
    session.flush()

    get_bridge().send_operational(
        "ARCHIVE_ACCESS_APPROVED",
        "Archived print content access approved",
        6,
        cs1=content_request.job_id,
        cs1Label="jobId",
        duser=content_request.requester,
        suser=user.username,
    )
    return content_request


def _valid_grant(session: Session, job_id: str, username: str) -> ContentRequest | None:
    now = datetime.now(UTC)
    grants = session.scalars(
        select(ContentRequest).where(
            ContentRequest.job_id == job_id,
            ContentRequest.requester == username,
            ContentRequest.status == "approved",
            ContentRequest.used_at.is_(None),
        )
    ).all()
    for grant in grants:
        expires = grant.expires_at
        if expires is None:
            continue
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires > now:
            return grant
    return None


@router.get("/{job_id}/text")
def get_extracted_text(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Extracted text — same gate as the original document. It is the same information."""
    job = _get_job(session, job_id)
    settings = get_settings()

    grant = None
    if settings.require_dual_approval_for_content:
        grant = _valid_grant(session, job_id, user.username)
        if grant is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "an approved content request is required to read this document",
            )

    stored = session.get(ExtractedText, job_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no extracted text for this job")

    _audit(session, job, user.username, "text", request, grant.id if grant else None)
    return {"job_id": job_id, "tier": stored.tier, "pages": stored.pages, "chars": stored.chars}


@router.get("/{job_id}/content")
def download_content(
    job_id: str,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    """The original spooled document. Single-use grant, always audited."""
    job = _get_job(session, job_id)
    if job.purged_at is not None or not job.archive_key or job.wrapped_key is None:
        raise HTTPException(status.HTTP_410_GONE, "archived content has been purged")

    settings = get_settings()
    grant = None
    if settings.require_dual_approval_for_content:
        grant = _valid_grant(session, job_id, user.username)
        if grant is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "an approved content request is required to read this document",
            )
        grant.used_at = datetime.now(UTC)

    data = get_archive().load(job.archive_key, job.wrapped_key)
    _audit(
        session,
        job,
        user.username,
        "content",
        request,
        grant.id if grant else None,
        detail=f"{len(data)} bytes",
    )
    get_bridge().send_operational(
        "ARCHIVE_CONTENT_READ",
        "Archived print content downloaded",
        7,
        cs1=job.id,
        cs1Label="jobId",
        duser=user.username,
        suser=job.username,
    )
    session.flush()

    filename = f"{job.id}.pdf"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
