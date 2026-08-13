"""Database schema.

Job is the spine: one row per print job, carrying the verdict and the archive pointer.
Everything else hangs off it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class JobState(str, enum.Enum):
    inspecting = "inspecting"
    released = "released"  # passed inspection, sent to the device
    held = "held"  # sitting in the CUPS queue awaiting a decision
    blocked = "blocked"  # cancelled outright
    released_by_analyst = "released_by_analyst"
    denied_by_analyst = "denied_by_analyst"
    # Released without a full verdict because the inspector failed and the queue is
    # fail-open. Its own state so the coverage gap is auditable (PLAN.md §4).
    failed_open = "failed_open"
    error = "error"


class ScanTier(str, enum.Enum):
    text = "text"  # inline text-layer scan only
    ocr_pending = "ocr_pending"  # released or held while OCR runs
    ocr_complete = "ocr_complete"
    unreadable = "unreadable"  # encrypted / undecodable


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    # --- as reported by the CUPS backend ------------------------------------
    cups_job_id: Mapped[str] = mapped_column(String(64), index=True)
    queue: Mapped[str] = mapped_column(String(128), index=True)
    username: Mapped[str] = mapped_column(String(128), index=True)
    hostname: Mapped[str] = mapped_column(String(256), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    copies: Mapped[int] = mapped_column(Integer, default=1)
    options: Mapped[str] = mapped_column(Text, default="")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True, default="")

    # --- verdict -------------------------------------------------------------
    state: Mapped[JobState] = mapped_column(
        Enum(JobState, native_enum=False, length=32), default=JobState.inspecting, index=True
    )
    action: Mapped[str] = mapped_column(String(16), default="allow")
    scan_tier: Mapped[ScanTier] = mapped_column(
        Enum(ScanTier, native_enum=False, length=32), default=ScanTier.text
    )
    score: Mapped[float] = mapped_column(Float, default=0.0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    pages_without_text: Mapped[int] = mapped_column(Integer, default=0)
    inline_ms: Mapped[int] = mapped_column(Integer, default=0)
    verdict_reason: Mapped[str] = mapped_column(Text, default="")

    # --- archive pointer -----------------------------------------------------
    archive_key: Mapped[str] = mapped_column(String(256), default="")
    # Per-object content key, itself encrypted under the master key (PLAN.md §6).
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    matches: Mapped[list[Match]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_jobs_state_created", "state", "created_at"),)

    @property
    def is_open(self) -> bool:
        return self.state == JobState.held


class Match(Base):
    """One rule that fired on one job. Excerpts are redacted before storage."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_name: Mapped[str] = mapped_column(String(256), default="")
    severity: Mapped[int] = mapped_column(Integer, default=5)
    action: Mapped[str] = mapped_column(String(16), default="log")
    count: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(16), default="text")
    # Masked sample (e.g. "4111********1111") — never the raw value.
    sample: Mapped[str] = mapped_column(String(256), default="")
    page: Mapped[int] = mapped_column(Integer, default=0)

    job: Mapped[Job] = relationship(back_populates="matches")


class JobEvent(Base):
    """Append-only audit trail for a job: decisions, releases, failures."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    kind: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128), default="system")
    detail: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[Job] = relationship(back_populates="events")


class RegisteredDocument(Base):
    """A document whose content is registered as sensitive — the fingerprint corpus."""

    __tablename__ = "registered_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    name: Mapped[str] = mapped_column(String(512))
    owner: Mapped[str] = mapped_column(String(128), default="")
    severity: Mapped[int] = mapped_column(Integer, default=7)
    action: Mapped[str] = mapped_column(String(16), default="hold")
    exact_sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    shingle_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    fingerprints: Mapped[list[Fingerprint]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Fingerprint(Base):
    """One winnowed shingle hash. Queried by hash, grouped by document."""

    __tablename__ = "fingerprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("registered_documents.id", ondelete="CASCADE"), index=True
    )
    # Signed 64-bit — hashes are folded into that range by the fingerprinter.
    hash: Mapped[int] = mapped_column(BigInteger)

    document: Mapped[RegisteredDocument] = relationship(back_populates="fingerprints")

    __table_args__ = (Index("ix_fingerprints_hash", "hash"),)


class RuleRow(Base):
    """A detection rule, editable from the console.

    The YAML packs in rules/ seed this table on first start; after that the database is
    authoritative. Storing rules as data rather than files is what lets an operator add a
    rule at 2am without a deploy — which is the difference between a policy that keeps up
    with the business and one that ossifies.
    """

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    pattern: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(16), default="log")
    severity: Mapped[int] = mapped_column(Integer, default=5)
    validator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    validator_weight: Mapped[float] = mapped_column(Float, default=0.3)
    base_confidence: Mapped[float] = mapped_column(Float, default=0.6)
    threshold: Mapped[float] = mapped_column(Float, default=0.75)
    min_count: Mapped[int] = mapped_column(Integer, default=1)
    ignore_case: Mapped[bool] = mapped_column(Boolean, default=True)
    sample_prefix: Mapped[int] = mapped_column(Integer, default=4)
    sample_suffix: Mapped[int] = mapped_column(Integer, default=4)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    fixtures: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    source: Mapped[str] = mapped_column(String(16), default="yaml")  # yaml|console
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, index=True
    )
    updated_by: Mapped[str] = mapped_column(String(128), default="system")


class RuleRevision(Base):
    """Every change to a rule, kept forever.

    A weakened detection rule is indistinguishable from a working one until something is
    missed, so who changed what, when, and why has to be answerable after the fact.
    """

    __tablename__ = "rule_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str] = mapped_column(String(128))
    change: Mapped[str] = mapped_column(String(16))  # created|updated|deleted|enabled|disabled
    note: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class PrinterQueue(Base):
    """A managed CUPS queue and its inspection policy.

    One row per physical printer. `device_uri` is the real device (ipp://, socket://);
    the janus:// form handed to CUPS is derived from it, so the interception wrapper can
    never be forgotten when a queue is created from the UI.
    """

    __tablename__ = "printer_queues"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(256), default="")

    # Real device, e.g. ipp://172.18.104.60/ipp/print or socket://172.18.104.9:9100
    device_uri: Mapped[str] = mapped_column(String(512))
    ppd_model: Mapped[str] = mapped_column(String(128), default="everywhere")

    # --- inspection policy (mirrors config/printers.yaml) --------------------
    deep_scan_required: Mapped[bool] = mapped_column(Boolean, default=False)
    fail_mode: Mapped[str] = mapped_column(String(8), default="open")  # open|closed
    on_unreadable: Mapped[str] = mapped_column(String(16), default="log")
    rule_tags: Mapped[list] = mapped_column(JSON, default=lambda: ["*"])

    # --- lifecycle ----------------------------------------------------------
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=True)  # advertised over DNS-SD
    # Whether the CUPS queue itself was created successfully. A row that exists here but
    # not in CUPS is worse than useless — it looks configured while nothing is inspected.
    cups_state: Mapped[str] = mapped_column(String(16), default="pending")
    cups_error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    updated_by: Mapped[str] = mapped_column(String(128), default="system")

    @property
    def janus_uri(self) -> str:
        """Wrap the real device URI for the interception backend."""
        scheme, _, rest = self.device_uri.partition("://")
        return f"janus://{scheme}/{rest}"


class PrinterRevision(Base):
    """Every change to a queue or its policy. Loosening a printer's policy is a security
    event, so it stays answerable after the fact."""

    __tablename__ = "printer_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queue: Mapped[str] = mapped_column(String(128), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor: Mapped[str] = mapped_column(String(128))
    change: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    # scrypt, salt stored alongside — see console.auth
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="analyst")  # analyst|approver|admin
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContentRequest(Base):
    """Dual-approval gate for reading an archived document's content (PLAN.md §6)."""

    __tablename__ = "content_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    requester: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|denied
    approver: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Approval is single-use and time-boxed.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArchiveAccess(Base):
    """Every read of archived content. The archive watches its watchers."""

    __tablename__ = "archive_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="content")  # content|text|metadata
    source_ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


class ExtractedText(Base):
    """Extracted text, kept separately from the job row so it can be purged early."""

    __tablename__ = "extracted_text"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    tier: Mapped[str] = mapped_column(String(16), default="text")
    pages: Mapped[list] = mapped_column(JSON, default=list)
    chars: Mapped[int] = mapped_column(Integer, default=0)
