"""Managed printer queues and their inspection policy.

Policy used to live only in config/printers.yaml. It now lives in the database and is
editable from the console; the YAML file seeds it on first start and never overwrites an
existing row.

The ordering rule that matters: **CUPS is changed before the database row is committed as
active.** A row that says "fail-closed, deep scan required" while no such CUPS queue exists
is worse than no row at all — it reads as configured while inspecting nothing. When the
CUPS call fails, the row is kept but marked `cups_state="error"` so the console can show
the discrepancy rather than hiding it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import PrinterPolicy, get_printer_policies
from .models import PrinterQueue, PrinterRevision

log = logging.getLogger(__name__)

FAIL_MODES = {"open", "closed"}
UNREADABLE_ACTIONS = {"allow", "log", "hold", "block"}


class PrinterError(ValueError):
    pass


def validate(payload: dict) -> dict:
    fail_mode = payload.get("fail_mode", "open")
    if fail_mode not in FAIL_MODES:
        raise PrinterError(f"fail_mode must be one of: {', '.join(sorted(FAIL_MODES))}")

    on_unreadable = payload.get("on_unreadable", "log")
    if on_unreadable not in UNREADABLE_ACTIONS:
        raise PrinterError(
            f"on_unreadable must be one of: {', '.join(sorted(UNREADABLE_ACTIONS))}"
        )

    tags = payload.get("rule_tags") or ["*"]
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise PrinterError("rule_tags must be a list of strings")

    payload = dict(payload)
    payload["fail_mode"] = fail_mode
    payload["on_unreadable"] = on_unreadable
    payload["rule_tags"] = tags
    return payload


# --- policy lookup with a version-keyed cache --------------------------------


@dataclass(frozen=True)
class _Version:
    count: int
    latest: object

    @classmethod
    def probe(cls, session: Session) -> _Version:
        count, latest = session.execute(
            select(func.count(PrinterQueue.name), func.max(PrinterQueue.updated_at))
        ).one()
        return cls(count=count or 0, latest=latest)


_cached: tuple[_Version, dict[str, PrinterPolicy]] | None = None


def _load(session: Session) -> dict[str, PrinterPolicy]:
    global _cached
    version = _Version.probe(session)
    if _cached is not None and _cached[0] == version:
        return _cached[1]

    policies = {
        row.name: PrinterPolicy(
            queue=row.name,
            deep_scan_required=row.deep_scan_required,
            fail_mode=row.fail_mode,
            on_unreadable=row.on_unreadable,
            rule_tags=list(row.rule_tags or ["*"]),
            description=row.description or "",
        )
        for row in session.scalars(select(PrinterQueue))
    }
    _cached = (version, policies)
    return policies


def invalidate_cache() -> None:
    global _cached
    _cached = None


def policy_for(session: Session, queue: str) -> PrinterPolicy:
    """Effective policy for a queue.

    Database first, then the YAML file, then the built-in default. The fallback chain is
    deliberate: a queue nobody has configured must still be inspected under *some* policy
    rather than erroring on the print path.
    """
    from_db = _load(session).get(queue)
    if from_db is not None:
        return from_db
    return get_printer_policies().for_queue(queue)


# --- seeding -----------------------------------------------------------------


def seed_from_yaml(session: Session) -> int:
    """Import queues declared in config/printers.yaml. Never overwrites an existing row."""
    policies = get_printer_policies()
    existing = set(session.scalars(select(PrinterQueue.name)))
    added = 0

    for name, policy in policies.queues.items():
        if name in existing:
            continue
        session.add(
            PrinterQueue(
                name=name,
                description=policy.description,
                # The YAML file carries policy only, not a device. Such a row is policy
                # for an externally created queue, so it is not "pending" creation.
                device_uri="",
                deep_scan_required=policy.deep_scan_required,
                fail_mode=policy.fail_mode,
                on_unreadable=policy.on_unreadable,
                rule_tags=list(policy.rule_tags),
                cups_state="external",
                updated_by="seed",
            )
        )
        added += 1

    if added:
        session.flush()
        invalidate_cache()
        log.info("seeded %d queue policies from YAML", added)
    return added


def adopt_existing(session: Session) -> int:
    """Record queues that already exist in CUPS but have no row here.

    Without this, a queue created by hand with lpadmin is invisible in the console and
    silently runs the default policy.
    """
    from .api import cups_control

    if not cups_control.available():
        return 0

    try:
        live = cups_control.list_queues()
    except cups_control.CupsControlError as exc:
        log.warning("could not enumerate CUPS queues: %s", exc)
        return 0

    known = set(session.scalars(select(PrinterQueue.name)))
    added = 0
    for name, uri in live.items():
        if name in known:
            continue
        # Only adopt inspected queues. A direct-to-device queue is not ours to manage and
        # showing it as managed would misrepresent what is inspected.
        if not uri.startswith("janus://"):
            continue
        if name.endswith("-device"):
            continue  # internal half of a pair, managed with its parent
        scheme, _, rest = uri[len("janus://") :].partition("/")
        session.add(
            PrinterQueue(
                name=name,
                device_uri=f"{scheme}://{rest}",
                cups_state="ok",
                updated_by="adopted",
                description="adopted from an existing CUPS queue",
            )
        )
        added += 1

    if added:
        session.flush()
        invalidate_cache()
        log.info("adopted %d existing CUPS queues", added)
    return added


# --- CRUD --------------------------------------------------------------------


def _snapshot(row: PrinterQueue) -> dict:
    return {
        "name": row.name,
        "device_uri": row.device_uri,
        "description": row.description,
        "location": row.location,
        "deep_scan_required": row.deep_scan_required,
        "fail_mode": row.fail_mode,
        "on_unreadable": row.on_unreadable,
        "rule_tags": row.rule_tags,
        "enabled": row.enabled,
        "shared": row.shared,
    }


def _record(session: Session, row: PrinterQueue, actor: str, change: str, note: str) -> None:
    session.add(
        PrinterRevision(
            queue=row.name, actor=actor, change=change, note=note, snapshot=_snapshot(row)
        )
    )


def create(session: Session, payload: dict, actor: str, note: str = "") -> PrinterQueue:
    from .api import cups_control

    payload = validate(payload)
    name = cups_control.validate_name((payload.get("name") or "").strip())
    device_uri = cups_control.validate_device_uri((payload.get("device_uri") or "").strip())

    if session.get(PrinterQueue, name) is not None:
        raise PrinterError(f"a queue named {name!r} already exists")

    row = PrinterQueue(
        name=name,
        device_uri=device_uri,
        ppd_model=payload.get("ppd_model") or "everywhere",
        description=payload.get("description", ""),
        location=payload.get("location", ""),
        deep_scan_required=bool(payload.get("deep_scan_required", False)),
        fail_mode=payload["fail_mode"],
        on_unreadable=payload["on_unreadable"],
        rule_tags=payload["rule_tags"],
        shared=bool(payload.get("shared", True)),
        updated_by=actor,
    )

    try:
        warnings = cups_control.create_queue(
            name,
            device_uri,
            model=row.ppd_model,
            description=row.description,
            location=row.location,
            shared=row.shared,
        )
        if not cups_control.available():
            row.cups_state = "unmanaged"
        elif warnings:
            # Usable, but not everything asked for was applied. Distinct from "ok" so the
            # console shows it instead of implying the queue is fully configured.
            row.cups_state = "warning"
            row.cups_error = " ".join(warnings)
        else:
            row.cups_state = "ok"
    except cups_control.CupsControlError as exc:
        # Kept, but visibly broken. A silently missing queue is the dangerous outcome.
        row.cups_state = "error"
        row.cups_error = str(exc)
        log.error("could not create CUPS queue %s: %s", name, exc)

    session.add(row)
    session.flush()
    _record(session, row, actor, "created", note)
    invalidate_cache()
    return row


def update(session: Session, name: str, payload: dict, actor: str, note: str = "") -> PrinterQueue:
    from .api import cups_control

    row = session.get(PrinterQueue, name)
    if row is None:
        raise PrinterError(f"no such queue: {name}")

    merged = validate(_snapshot(row) | payload)

    device_changed = (
        "device_uri" in payload and payload["device_uri"] and payload["device_uri"] != row.device_uri
    )
    if device_changed:
        row.device_uri = cups_control.validate_device_uri(payload["device_uri"])

    row.description = merged.get("description", row.description)
    row.location = merged.get("location", row.location)
    row.deep_scan_required = bool(merged["deep_scan_required"])
    row.fail_mode = merged["fail_mode"]
    row.on_unreadable = merged["on_unreadable"]
    row.rule_tags = merged["rule_tags"]
    row.updated_by = actor

    shared_changed = "shared" in payload and bool(payload["shared"]) != row.shared
    enabled_changed = "enabled" in payload and bool(payload["enabled"]) != row.enabled
    row.shared = bool(merged.get("shared", row.shared))
    row.enabled = bool(merged.get("enabled", row.enabled))

    try:
        if device_changed and row.device_uri:
            cups_control.create_queue(
                name,
                row.device_uri,
                model=row.ppd_model,
                description=row.description,
                location=row.location,
                shared=row.shared,
            )
        else:
            if shared_changed:
                cups_control.set_shared(name, row.shared)
            if enabled_changed:
                cups_control.set_enabled(name, row.enabled)
        if row.cups_state == "error":
            row.cups_state, row.cups_error = "ok", ""
    except cups_control.CupsControlError as exc:
        row.cups_state = "error"
        row.cups_error = str(exc)
        log.error("could not update CUPS queue %s: %s", name, exc)

    session.flush()
    _record(session, row, actor, "updated", note)
    invalidate_cache()
    return row


def delete(session: Session, name: str, actor: str, note: str = "") -> None:
    from .api import cups_control
    from .models import Job, JobState

    row = session.get(PrinterQueue, name)
    if row is None:
        raise PrinterError(f"no such queue: {name}")

    held = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.queue == name, Job.state == JobState.held)
    )
    if held:
        # Deleting the queue would strand them: no way to release, no way to deny.
        raise PrinterError(
            f"{held} job(s) are still held on this queue — decide on them before removing it"
        )

    try:
        cups_control.delete_queue(name)
    except cups_control.CupsControlError as exc:
        # "does not exist" is not a failure to delete — it is the desired end state, and
        # it is exactly what a row left behind by a failed creation looks like. Treating
        # it as fatal made such rows permanently undeletable.
        if "does not exist" in str(exc) or "not exist" in str(exc):
            log.info("no CUPS queue named %s to remove; clearing the record", name)
        else:
            log.error("could not delete CUPS queue %s: %s", name, exc)
            raise PrinterError(f"CUPS refused to remove the queue: {exc}") from exc

    _record(session, row, actor, "deleted", note)
    session.delete(row)
    session.flush()
    invalidate_cache()


def reconcile(session: Session) -> dict[str, list[str]]:
    """Compare what is configured here with what CUPS actually has."""
    from .api import cups_control

    rows = {row.name: row for row in session.scalars(select(PrinterQueue))}
    if not cups_control.available():
        return {"managed": sorted(rows), "missing_in_cups": [], "unmanaged_in_cups": []}

    try:
        live = cups_control.list_queues()
    except cups_control.CupsControlError:
        return {"managed": sorted(rows), "missing_in_cups": [], "unmanaged_in_cups": []}

    return {
        "managed": sorted(rows),
        # Configured here, absent there: looks protected, inspects nothing.
        "missing_in_cups": sorted(set(rows) - set(live)),
        # Present there, unknown here: running on the default policy.
        "unmanaged_in_cups": sorted(set(live) - set(rows)),
    }


def revisions(session: Session, queue: str | None = None, limit: int = 200) -> list[PrinterRevision]:
    query = select(PrinterRevision).order_by(PrinterRevision.at.desc()).limit(limit)
    if queue:
        query = query.where(PrinterRevision.queue == queue)
    return list(session.scalars(query))
