"""Wire contracts between the CUPS backend, the console, and the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .config import Action


class InspectResponse(BaseModel):
    """What the CUPS backend acts on. `release` is the only field it strictly needs;
    the rest is for its error_log line."""

    job_id: str
    action: Action
    release: bool
    reason: str = ""
    score: float = 0.0
    scan_tier: str = "text"
    rule_ids: list[str] = Field(default_factory=list)
    page_count: int = 0
    inline_ms: int = 0
    deep_scan_queued: bool = False


class PreflightResponse(BaseModel):
    """Answered before inspection so an analyst-released job is not re-held.

    Resuming a held CUPS job re-runs the backend from the top. Without this the job would
    be inspected again, match again, and be held again — an unreleasable job.
    """

    pass_through: bool = False
    job_id: str | None = None
    reason: str = ""


class MatchOut(BaseModel):
    rule_id: str
    rule_name: str
    severity: int
    action: str
    count: int
    score: float
    tier: str
    sample: str
    page: int

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    at: datetime
    kind: str
    actor: str
    detail: str

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: str
    created_at: datetime
    cups_job_id: str
    queue: str
    username: str
    hostname: str
    title: str
    copies: int
    byte_size: int
    content_sha256: str
    state: str
    action: str
    scan_tier: str
    score: float
    page_count: int
    pages_without_text: int
    inline_ms: int
    verdict_reason: str
    purged_at: datetime | None = None
    matches: list[MatchOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class JobDetailOut(JobOut):
    events: list[EventOut] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class RegisterDocumentResponse(BaseModel):
    id: str
    name: str
    shingle_count: int
    exact_sha256: str


class ContentRequestOut(BaseModel):
    id: str
    job_id: str
    requester: str
    reason: str
    status: str
    approver: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class HealthOut(BaseModel):
    status: str
    rules_loaded: int
    ocr_available: bool
    ghostscript_available: bool
    archive_backend: str
    siem_enabled: bool
    database: str
    # "re2" is linear-time and safe for operator-authored patterns; "re" backtracks and a
    # crafted document can hang inspection.
    regex_engine: str = "re2"
    regex_linear_time: bool = True
