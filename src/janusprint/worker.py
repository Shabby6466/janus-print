"""Deferred work: OCR deep scans and retention purges.

Redis/RQ when available. When it is not — the lab, tests, a single-box install — jobs run
inline on a thread so the feature degrades rather than disappearing. A missing Redis must
never mean silently skipped OCR.
"""

from __future__ import annotations

import logging
import threading
import time

from .config import get_settings

log = logging.getLogger(__name__)

QUEUE_NAME = "janus-print"
_fallback_notice = False


def _queue():
    """Return an RQ queue, or None if Redis is unreachable."""
    try:
        from redis import Redis
        from rq import Queue

        connection = Redis.from_url(get_settings().redis_url, socket_connect_timeout=1)
        connection.ping()
        return Queue(QUEUE_NAME, connection=connection)
    except Exception as exc:  # noqa: BLE001 - any Redis problem falls back
        global _fallback_notice
        if not _fallback_notice:
            log.warning("Redis unavailable (%s); deep scans will run in-process", exc)
            _fallback_notice = True
        return None


def run_deep_scan(job_id: str) -> None:
    """Entry point for the worker process."""
    from .bridge.cef import get_bridge
    from .db import session_scope
    from .inspector.engine import deep_scan
    from .models import Job

    started = time.monotonic()
    try:
        with session_scope() as session:
            verdict = deep_scan(session, job_id)
            if verdict is None:
                return
            session.flush()
            job = session.get(Job, job_id)
            if job is not None:
                # A retrospective hit on an already-printed job is still an incident —
                # you cannot unprint, but you can investigate.
                get_bridge().send_job(job, f"deep scan: {verdict.reason}")
    except Exception:  # noqa: BLE001
        log.exception("deep scan failed for job %s", job_id)
    else:
        log.info("deep scan for %s finished in %.1fs", job_id, time.monotonic() - started)


def enqueue_deep_scan(job_id: str) -> None:
    queue = _queue()
    if queue is not None:
        queue.enqueue(run_deep_scan, job_id, job_timeout=1800)
        return
    threading.Thread(target=run_deep_scan, args=(job_id,), daemon=True).start()


def run_retention_loop(interval_seconds: int = 3600) -> None:
    from .archive.retention import purge_expired

    while True:
        try:
            purge_expired()
        except Exception:  # noqa: BLE001
            log.exception("retention purge failed")
        time.sleep(interval_seconds)


def main() -> int:
    """`janus-print-worker` — RQ worker plus the retention timer."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from .db import init_db

    init_db()

    threading.Thread(target=run_retention_loop, daemon=True).start()

    queue = _queue()
    if queue is None:
        log.error("no Redis; worker has nothing to consume. Retention timer still running.")
        while True:
            time.sleep(60)

    from rq import Worker

    log.info("worker listening on %s", QUEUE_NAME)
    Worker([queue], connection=queue.connection).work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
