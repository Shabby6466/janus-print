"""Inspection orchestration — the decision path for every print job.

Inline budget (PLAN.md §3): archive, extract, rules, fingerprints. Target under
`inspect_deadline_seconds`. OCR is always deferred.

The rules of the road, in priority order:

  1. Never lose the archive copy. It is written before anything can go wrong.
  2. Never exceed the deadline. Past it, the queue's fail mode decides and the job is
     tagged so the coverage gap shows up in the audit trail.
  3. Never raise into the CUPS backend. Every failure resolves to an action.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..archive.store import get_archive
from ..config import ACTION_RANK, Action, PrinterPolicy, get_settings
from ..printers import policy_for
from ..models import ExtractedText, Fingerprint, Job, JobEvent, JobState, Match, RegisteredDocument
from ..models import ScanTier
from . import fingerprint as fp
from .extract import ExtractionResult, extract
from .rules import RuleHit, RuleSet, merge_action

log = logging.getLogger(__name__)

def get_ruleset(session: Session | None = None) -> RuleSet:
    """Active rules, from the database.

    A session is passed on the inspection path so the ruleset is checked against the
    table's current version — an operator disabling a rule in the console must take effect
    on the next job, in every process, not on the next restart.
    """
    from .store import load_ruleset

    if session is not None:
        return load_ruleset(session)

    from ..db import session_scope

    with session_scope() as scoped:
        return load_ruleset(scoped)


def reload_rules() -> int:
    """Drop the cache and rebuild. Rules themselves live in the database now."""
    from ..db import session_scope
    from .store import invalidate_cache, load_ruleset

    invalidate_cache()
    with session_scope() as session:
        return len(load_ruleset(session))


@dataclass
class JobMetadata:
    cups_job_id: str
    queue: str
    username: str
    hostname: str = ""
    title: str = ""
    copies: int = 1
    options: str = ""


@dataclass
class Verdict:
    job_id: str
    action: Action
    state: JobState
    reason: str
    score: float = 0.0
    scan_tier: ScanTier = ScanTier.text
    rule_ids: list[str] = field(default_factory=list)
    page_count: int = 0
    pages_pending_ocr: list[int] = field(default_factory=list)
    inline_ms: int = 0
    deep_scan_queued: bool = False

    @property
    def release(self) -> bool:
        return self.action in {"allow", "log"}


def inspect_job(
    session: Session, meta: JobMetadata, data: bytes, deadline: float | None = None
) -> Verdict:
    """Inline inspection. Returns a verdict for every input, including broken ones."""
    import hashlib

    started = time.monotonic()
    settings = get_settings()
    budget = deadline if deadline is not None else settings.inspect_deadline_seconds
    policy = policy_for(session, meta.queue)
    digest = hashlib.sha256(data).hexdigest()

    # CUPS re-runs the backend whenever a job is retried or resumed, so the same physical
    # job can arrive here several times. Reuse the existing row rather than filling the
    # console with duplicates of one document.
    job = session.scalar(
        select(Job)
        .where(
            Job.queue == meta.queue,
            Job.cups_job_id == meta.cups_job_id,
            Job.content_sha256 == digest,
            Job.purged_at.is_(None),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    reinspection = job is not None

    if job is None:
        job = Job(
            cups_job_id=meta.cups_job_id,
            queue=meta.queue,
            username=meta.username,
            hostname=meta.hostname,
            title=meta.title[:512],
            copies=meta.copies,
            options=meta.options,
            byte_size=len(data),
            content_sha256=digest,
            state=JobState.inspecting,
        )
        session.add(job)
        session.flush()
    else:
        # Superseded verdict — drop the previous matches so they are not double-counted.
        for stale in list(job.matches):
            session.delete(stale)
        session.execute(ExtractedText.__table__.delete().where(ExtractedText.job_id == job.id))
        job.state = JobState.inspecting
        session.flush()
        _event(session, job, "reinspected", detail="CUPS re-ran the backend for this job")

    # 1. Archive first — whatever else fails, the audit trail survives.
    if not reinspection:
        try:
            key, wrapped, _digest, purge_after = get_archive().store(job.id, data)
            job.archive_key, job.wrapped_key = key, wrapped
            job.purge_after = purge_after
        except Exception as exc:  # noqa: BLE001
            log.exception("archive write failed for job %s", job.id)
            _event(session, job, "archive_failed", detail=str(exc))

    try:
        verdict = _run_inspection(session, job, data, policy, started, budget)
    except Exception as exc:  # noqa: BLE001 - a crash here must not stop the office printing
        log.exception("inspection crashed for job %s", job.id)
        verdict = _resolve_failure(session, job, policy, f"inspector error: {exc}", started)

    job.action = verdict.action
    job.state = verdict.state
    job.score = verdict.score
    job.scan_tier = verdict.scan_tier
    job.verdict_reason = verdict.reason
    job.inline_ms = verdict.inline_ms
    session.flush()
    return verdict


def _run_inspection(
    session: Session,
    job: Job,
    data: bytes,
    policy: PrinterPolicy,
    started: float,
    budget: float,
) -> Verdict:
    settings = get_settings()

    remaining = budget - (time.monotonic() - started)
    result = extract(data, budget=remaining)
    job.page_count = result.page_count

    if result.unreadable:
        return _unreadable(session, job, result, policy, started, data)

    thin_pages = result.pages_without_text(settings.text_layer_min_chars)
    job.pages_without_text = len(thin_pages)

    session.add(
        ExtractedText(job_id=job.id, tier="text", pages=result.pages, chars=result.chars)
    )

    ruleset = get_ruleset(session).select(policy.rule_tags)
    hits: list[RuleHit] = []
    for number, page_text in enumerate(result.pages, start=1):
        if not page_text.strip():
            continue
        hits.extend(ruleset.evaluate_page(page_text, page=number))
        if time.monotonic() - started > budget:
            log.warning("job %s hit the deadline during rule evaluation", job.id)
            return _resolve_failure(
                session, job, policy, "deadline exceeded during rule evaluation", started, hits
            )

    fingerprint_hits = _match_fingerprints(session, result.text)
    action, score, reason = merge_action(hits)

    for hit in hits:
        session.add(
            Match(
                job_id=job.id,
                rule_id=hit.rule.id,
                rule_name=hit.rule.name,
                severity=hit.rule.severity,
                action=hit.rule.action,
                count=hit.count,
                score=hit.score,
                tier=hit.tier,
                sample=hit.sample,
                page=hit.page,
            )
        )

    for match in fingerprint_hits:
        session.add(
            Match(
                job_id=job.id,
                rule_id=f"fingerprint:{match.document_id}",
                rule_name=f"Registered document: {match.document_name}",
                severity=match.severity,
                action=match.action,
                count=match.overlap,
                score=match.ratio,
                tier="fingerprint",
                sample=f"{match.ratio:.0%} overlap",
                page=0,
            )
        )
        if ACTION_RANK[match.action] > ACTION_RANK[action]:
            action, reason = match.action, f"matches registered document '{match.document_name}'"
        score = max(score, match.ratio)

    # Image-only pages: policy decides whether the user waits for OCR (PLAN.md §3).
    scan_tier = ScanTier.text
    deep_scan_queued = False
    if thin_pages:
        scan_tier = ScanTier.ocr_pending
        deep_scan_queued = True
        if policy.deep_scan_required and ACTION_RANK[action] < ACTION_RANK["hold"]:
            action = "hold"
            reason = (
                f"{len(thin_pages)} page(s) could not be read as text and this queue "
                f"requires a deep scan before release"
            )

    state = _state_for(action)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _event(
        session,
        job,
        "inspected",
        detail=f"action={action} score={score:.2f} rules={len(hits)} ms={elapsed_ms}",
    )

    return Verdict(
        job_id=job.id,
        action=action,
        state=state,
        reason=reason,
        score=score,
        scan_tier=scan_tier,
        rule_ids=[h.rule.id for h in hits] + [f"fingerprint:{m.document_id}" for m in fingerprint_hits],
        page_count=result.page_count,
        pages_pending_ocr=thin_pages,
        inline_ms=elapsed_ms,
        deep_scan_queued=deep_scan_queued,
    )


def _match_fingerprints(session: Session, text: str) -> list[fp.FingerprintMatch]:
    """Single indexed query against the corpus, then containment arithmetic in Python."""
    settings = get_settings()
    query = fp.fingerprint(text)
    if not query:
        return []

    counts: dict[str, int] = {}
    hashes = list(query)
    # Chunked to stay under the backend's bind-parameter ceiling.
    for start in range(0, len(hashes), 5000):
        chunk = hashes[start : start + 5000]
        rows = session.execute(
            select(Fingerprint.document_id, func.count())
            .where(Fingerprint.hash.in_(chunk))
            .group_by(Fingerprint.document_id)
        ).all()
        for document_id, count in rows:
            counts[document_id] = counts.get(document_id, 0) + count

    if not counts:
        return []

    documents = {
        doc.id: doc
        for doc in session.scalars(
            select(RegisteredDocument).where(
                RegisteredDocument.id.in_(counts), RegisteredDocument.enabled.is_(True)
            )
        )
    }

    matches: list[fp.FingerprintMatch] = []
    for document_id, overlap in counts.items():
        document = documents.get(document_id)
        if document is None or not document.shingle_count:
            continue
        ratio = max(overlap / len(query), overlap / document.shingle_count)
        if ratio >= settings.fingerprint_threshold:
            matches.append(
                fp.FingerprintMatch(
                    document_id=document_id,
                    document_name=document.name,
                    severity=document.severity,
                    action=document.action,
                    overlap=overlap,
                    ratio=min(ratio, 1.0),
                )
            )
    return sorted(matches, key=lambda m: m.ratio, reverse=True)


def _unreadable(
    session: Session,
    job: Job,
    result: ExtractionResult,
    policy: PrinterPolicy,
    started: float,
    data: bytes = b"",
) -> Verdict:
    """Encrypted or undecodable — the printer can render it but we cannot read it.

    The leading bytes are recorded because "unreadable" on its own is an unactionable
    verdict. Knowing that a queue is receiving PWG-Raster rather than PDF is the difference
    between a five-minute driver change and a week of guessing.
    """
    kind = "encrypted" if result.encrypted else f"unreadable ({result.format})"
    action = policy.on_unreadable

    magic = ""
    if data:
        head = data[:16]
        printable = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
        magic = f" first bytes: {head.hex(' ')} |{printable}|"

    log.warning(
        "job %s on %s could not be read (%s).%s", job.id, job.queue, result.format, magic
    )
    _event(session, job, "unreadable", detail=f"{kind}: {result.error}{magic}")
    return Verdict(
        job_id=job.id,
        action=action,
        state=_state_for(action),
        reason=f"document is {kind}; queue policy says {action}",
        scan_tier=ScanTier.unreadable,
        inline_ms=int((time.monotonic() - started) * 1000),
    )


def _resolve_failure(
    session: Session,
    job: Job,
    policy: PrinterPolicy,
    reason: str,
    started: float,
    hits: list[RuleHit] | None = None,
) -> Verdict:
    """Inspection could not complete. The queue's fail mode decides (PLAN.md §4).

    Fail-open is the default because an inspector outage that halts every printer in the
    building is worse than the leak it was deployed to stop. The gap gets its own state
    so it is never mistaken for a clean pass.
    """
    if policy.fail_mode == "closed":
        action: Action = "hold"
        state = JobState.held
    else:
        action = "log"
        state = JobState.failed_open

    # Anything already matched before the failure still counts.
    if hits:
        matched_action, score, matched_reason = merge_action(hits)
        if ACTION_RANK[matched_action] > ACTION_RANK[action]:
            action, state = matched_action, _state_for(matched_action)
            reason = f"{matched_reason} (partial scan: {reason})"

    _event(session, job, f"fail_{policy.fail_mode}", detail=reason)
    log.error("job %s resolved by fail-%s: %s", job.id, policy.fail_mode, reason)
    return Verdict(
        job_id=job.id,
        action=action,
        state=state,
        reason=reason,
        inline_ms=int((time.monotonic() - started) * 1000),
    )


def _state_for(action: str) -> JobState:
    return {
        "allow": JobState.released,
        "log": JobState.released,
        "hold": JobState.held,
        "block": JobState.blocked,
    }[action]


def _event(session: Session, job: Job, kind: str, actor: str = "system", detail: str = "") -> None:
    session.add(JobEvent(job_id=job.id, kind=kind, actor=actor, detail=detail))


# --- deferred tier -----------------------------------------------------------


def deep_scan(session: Session, job_id: str) -> Verdict | None:
    """OCR pass. Runs in a worker, never on the print path.

    A job that already printed cannot be unprinted — the value here is the incident and
    the audit trail. A job still held gets cleared or confirmed.
    """
    job = session.get(Job, job_id)
    if job is None or not job.archive_key or job.wrapped_key is None:
        log.warning("deep scan skipped: job %s has no retrievable body", job_id)
        return None

    from .ocr import ocr_pages

    data = get_archive().load(job.archive_key, job.wrapped_key)
    result = extract(data)
    if result.unreadable:
        return None

    settings = get_settings()
    thin = result.pages_without_text(settings.text_layer_min_chars)
    if not thin:
        job.scan_tier = ScanTier.ocr_complete
        return None

    # OCR rasterises pages, which only works on PDF. PostScript and PCL therefore have to
    # be converted first — the same conversion the inline pass did, repeated here because
    # the converted copy is not retained. Skipping this silently marked the scan complete
    # without ever running OCR, leaving the job held with no verdict and no explanation.
    pdf_bytes = data
    if result.format != "pdf":
        from .extract import _to_pdf

        converted = _to_pdf(data, result.format)
        if converted is None:
            log.error(
                "job %s is %s and could not be converted for OCR; leaving it held",
                job_id,
                result.format,
            )
            _event(
                session,
                job,
                "deep_scan_failed",
                detail=f"cannot rasterise a {result.format} document for OCR",
            )
            job.scan_tier = ScanTier.unreadable
            session.flush()
            return None
        pdf_bytes = converted

    texts = ocr_pages(pdf_bytes, thin)
    policy = policy_for(session, job.queue)
    ruleset = get_ruleset(session).select(policy.rule_tags)

    hits: list[RuleHit] = []
    for page_number, text in texts.items():
        hits.extend(ruleset.evaluate_page(text, page=page_number, tier="ocr"))

    merged_pages = list(result.pages)
    for page_number, text in texts.items():
        if 1 <= page_number <= len(merged_pages):
            merged_pages[page_number - 1] = text

    fingerprint_hits = _match_fingerprints(session, "\n".join(merged_pages))

    stored = session.get(ExtractedText, job_id)
    if stored is not None:
        stored.tier = "ocr"
        stored.pages = merged_pages
        stored.chars = sum(len(p) for p in merged_pages)

    action, score, reason = merge_action(hits)
    for match in fingerprint_hits:
        if ACTION_RANK[match.action] > ACTION_RANK[action]:
            action, reason = match.action, f"matches registered document '{match.document_name}'"
        score = max(score, match.ratio)

    for hit in hits:
        session.add(
            Match(
                job_id=job.id,
                rule_id=hit.rule.id,
                rule_name=hit.rule.name,
                severity=hit.rule.severity,
                action=hit.rule.action,
                count=hit.count,
                score=hit.score,
                tier="ocr",
                sample=hit.sample,
                page=hit.page,
            )
        )

    job.scan_tier = ScanTier.ocr_complete
    job.score = max(job.score, score)

    # A job held *only* because this queue requires a deep scan has no rule of its own
    # demanding a hold. Checking job.action is not enough — the deep-scan rule sets it to
    # "hold" itself, which would leave such a job stuck held forever after a clean OCR.
    blocking_matches = [
        m
        for m in job.matches
        if m.tier != "ocr" and ACTION_RANK[m.action] >= ACTION_RANK["hold"]
    ]
    previously_held_pending_scan = job.state == JobState.held and not blocking_matches

    # Whether the job is still in the queue decides what a hit can honestly do about it.
    already_printed = job.state in {
        JobState.released,
        JobState.released_by_analyst,
        JobState.failed_open,
        JobState.released_then_flagged,
    }

    if ACTION_RANK[action] >= ACTION_RANK["hold"]:
        # OCR found something the text layer could not see.
        job.action = action
        if already_printed:
            # Nothing to hold — the pages are out. Saying "held" here would tell an
            # analyst the document was contained when it was not. This is an incident to
            # investigate, not a decision to make.
            job.state = JobState.released_then_flagged
            job.verdict_reason = (
                f"OCR found {reason} AFTER the job printed — this queue releases while "
                f"the deep scan runs (deep_scan_required is off)"
            )
            _event(
                session, job, "deep_scan_hit_after_release",
                detail=f"action={action} {reason}; document already printed",
            )
        else:
            job.state = _state_for(action)
            job.verdict_reason = f"OCR: {reason}"
            _event(session, job, "deep_scan_hit", detail=f"action={action} {reason}")
    elif previously_held_pending_scan:
        # Held only because we could not read it; OCR cleared it.
        job.state = JobState.released
        job.verdict_reason = "deep scan clear"
        _event(session, job, "deep_scan_clear", detail="OCR found nothing; released")
        from ..api import cups_control

        try:
            cups_control.release(job.queue, job.cups_job_id)
        except Exception as exc:
            log.warning("could not resume CUPS job %s after OCR clear: %s", job.id, exc)
    else:
        _event(session, job, "deep_scan_clear", detail="OCR found nothing")

    session.flush()
    return Verdict(
        job_id=job.id,
        action=job.action,
        state=job.state,
        reason=job.verdict_reason,
        score=job.score,
        scan_tier=ScanTier.ocr_complete,
        rule_ids=[h.rule.id for h in hits],
        page_count=job.page_count,
    )


def register_document(
    session: Session,
    name: str,
    data: bytes,
    owner: str = "",
    severity: int = 7,
    action: str = "hold",
) -> RegisteredDocument:
    """Add a document to the fingerprint corpus."""
    import hashlib

    result = extract(data)
    if result.unreadable:
        raise ValueError(f"cannot extract text to fingerprint: {result.error}")

    hashes = fp.fingerprint(result.text)
    if not hashes:
        raise ValueError("document produced no fingerprints; is it empty?")

    document = RegisteredDocument(
        name=name,
        owner=owner,
        severity=severity,
        action=action,
        exact_sha256=hashlib.sha256(data).hexdigest(),
        shingle_count=len(hashes),
    )
    session.add(document)
    session.flush()
    session.add_all(Fingerprint(document_id=document.id, hash=h) for h in hashes)
    session.flush()
    return document
