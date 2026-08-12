"""CEF emitter for Janus SIEM.

Coupling to Janus is one syslog line — no shared database, no shared runtime (Janus is
PHP 8.2/MySQL; this is Python). If Janus is down, printing is unaffected; events queue and
drop rather than block.

Content never travels in the alert. Rule id, count, and a masked sample only; the document
itself stays in the archive behind the dual-approval gate (PLAN.md §7).
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime

from ..config import Settings, get_settings
from ..models import Job, JobState

log = logging.getLogger(__name__)

# Syslog priority: facility 13 (log audit) * 8 + severity 4 (warning)
_PRIORITY = 13 * 8 + 4


def _escape_header(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _escape_extension(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace("\n", " ")
        .replace("\r", " ")
    )


@dataclass
class CEFEvent:
    signature_id: str
    name: str
    severity: int
    extensions: dict[str, str]

    def render(self, settings: Settings) -> str:
        header = "|".join(
            [
                "CEF:0",
                _escape_header(settings.siem_vendor),
                _escape_header(settings.siem_product),
                _escape_header(settings.siem_version),
                _escape_header(self.signature_id),
                _escape_header(self.name),
                str(max(0, min(10, self.severity))),
            ]
        )
        body = " ".join(
            f"{key}={_escape_extension(value)}"
            for key, value in self.extensions.items()
            if value not in (None, "")
        )
        return f"{header}|{body}"


def event_for_job(job: Job, reason: str = "") -> CEFEvent:
    """Map a job verdict onto a CEF event."""
    top = max(job.matches, key=lambda m: (m.severity, m.score), default=None)

    signature, name, severity = _signature_for(job, top)
    extensions: dict[str, str] = {
        "rt": str(int(job.created_at.timestamp() * 1000)),
        "suser": job.username,
        "shost": job.hostname,
        "dproc": job.queue,
        "fname": job.title,
        "fsize": str(job.byte_size),
        "fileHash": job.content_sha256,
        "cs1": job.action,
        "cs1Label": "action",
        "cs2": job.id,
        "cs2Label": "jobId",
        "cs3": ",".join(sorted({m.rule_id for m in job.matches}))[:1000],
        "cs3Label": "ruleIds",
        "cs4": job.scan_tier.value,
        "cs4Label": "scanTier",
        "cn1": str(sum(m.count for m in job.matches)),
        "cn1Label": "matchCount",
        "cn2": str(job.page_count),
        "cn2Label": "pageCount",
        "cn3": str(job.inline_ms),
        "cn3Label": "inspectMs",
        "msg": (reason or job.verdict_reason)[:512],
    }
    if top is not None:
        # Masked sample only — e.g. "4111********1111".
        extensions["cs5"] = top.sample
        extensions["cs5Label"] = "maskedSample"
    return CEFEvent(signature, name, severity, extensions)


def _signature_for(job: Job, top) -> tuple[str, str, int]:
    if job.state == JobState.failed_open:
        return (
            "INSPECTION_FAILED_OPEN",
            "Print job released without inspection",
            7,
        )
    if job.state == JobState.blocked:
        return "PRINT_BLOCKED", "Print job blocked by DLP policy", max(8, _sev(top))
    if job.state == JobState.held:
        return "PRINT_HELD", "Print job held for review", max(6, _sev(top))
    if job.matches:
        return "PRINT_POLICY_MATCH", "Sensitive data printed", _sev(top)
    return "PRINT_CLEAN", "Print job inspected, no match", 1


def _sev(top) -> int:
    return top.severity if top is not None else 5


class SIEMBridge:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.sent = 0
        self.failed = 0

    def send(self, event: CEFEvent) -> bool:
        settings = self.settings
        if not settings.siem_enabled:
            log.debug("SIEM disabled; dropping %s", event.signature_id)
            return False

        stamp = datetime.now(UTC).strftime("%b %d %H:%M:%S")
        host = socket.gethostname()
        line = f"<{_PRIORITY}>{stamp} {host} janus-print: {event.render(settings)}"

        try:
            if settings.siem_protocol == "tcp":
                with socket.create_connection(
                    (settings.siem_host, settings.siem_port), timeout=2.0
                ) as sock:
                    sock.sendall(line.encode("utf-8") + b"\n")
            else:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(2.0)
                    sock.sendto(line.encode("utf-8"), (settings.siem_host, settings.siem_port))
        except OSError as exc:
            # Never let a SIEM outage affect printing — log and move on.
            self.failed += 1
            log.warning("SIEM send failed (%s); event %s dropped", exc, event.signature_id)
            return False

        self.sent += 1
        return True

    def send_job(self, job: Job, reason: str = "") -> bool:
        return self.send(event_for_job(job, reason))

    def send_operational(self, signature: str, name: str, severity: int, **fields: str) -> bool:
        """Health and lifecycle events — service up/down, rule reloads, purge runs."""
        return self.send(CEFEvent(signature, name, severity, dict(fields)))


_bridge: SIEMBridge | None = None


def get_bridge() -> SIEMBridge:
    global _bridge
    if _bridge is None:
        _bridge = SIEMBridge()
    return _bridge


def reset_bridge() -> None:
    """Test hook."""
    global _bridge
    _bridge = None
