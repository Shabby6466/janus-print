"""The print path. Everything here is latency-critical and must never 5xx into the
backend — the backend's fail-open logic exists for the cases this file cannot handle,
but it should rarely need it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bridge.cef import get_bridge
from ..db import get_session
from ..inspector.engine import JobMetadata, inspect_job
from ..models import Job, JobState
from ..schemas import InspectResponse, PreflightResponse
from ..worker import enqueue_deep_scan

log = logging.getLogger(__name__)
router = APIRouter(tags=["inspect"])


@router.get("/preflight", response_model=PreflightResponse)
def preflight(
    queue: str, cups_job_id: str, session: Session = Depends(get_session)
) -> PreflightResponse:
    """Has an analyst already cleared this exact CUPS job?

    Resuming a held job re-runs the backend. Without this the job would be re-inspected,
    re-matched, and re-held — permanently unreleasable.
    """
    job = session.scalar(
        select(Job)
        .where(
            Job.queue == queue,
            Job.cups_job_id == cups_job_id,
            Job.state == JobState.released_by_analyst,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        return PreflightResponse(pass_through=False)
    return PreflightResponse(
        pass_through=True,
        job_id=job.id,
        reason=f"released by analyst: {job.verdict_reason}",
    )


@router.post("/inspect", response_model=InspectResponse)
async def inspect(
    document: UploadFile = File(...),
    cups_job_id: str = Form(...),
    queue: str = Form(...),
    username: str = Form(...),
    hostname: str = Form(""),
    title: str = Form(""),
    copies: int = Form(1),
    options: str = Form(""),
    session: Session = Depends(get_session),
) -> InspectResponse:
    data = await document.read()
    meta = JobMetadata(
        cups_job_id=cups_job_id,
        queue=queue,
        username=username,
        hostname=hostname,
        title=title,
        copies=copies,
        options=options,
    )

    verdict = inspect_job(session, meta, data)
    session.flush()

    job = session.get(Job, verdict.job_id)
    if job is not None:
        get_bridge().send_job(job, verdict.reason)

    if verdict.deep_scan_queued:
        session.commit()
        enqueue_deep_scan(verdict.job_id)

    log.info(
        "job %s queue=%s user=%s action=%s tier=%s %dms",
        verdict.job_id,
        queue,
        username,
        verdict.action,
        verdict.scan_tier.value,
        verdict.inline_ms,
    )

    return InspectResponse(
        job_id=verdict.job_id,
        action=verdict.action,
        release=verdict.release,
        reason=verdict.reason,
        score=verdict.score,
        scan_tier=verdict.scan_tier.value,
        rule_ids=verdict.rule_ids,
        page_count=verdict.page_count,
        inline_ms=verdict.inline_ms,
        deep_scan_queued=verdict.deep_scan_queued,
    )
