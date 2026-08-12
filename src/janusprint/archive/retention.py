"""Retention enforcement.

A DLP archive that never forgets is a liability that only grows. This runs on a timer
(see worker.py) and hard-deletes both the ciphertext and the wrapped key — losing the key
makes the object unrecoverable even if a backup of the bucket survives.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from ..db import session_scope
from ..models import ExtractedText, Job
from .store import get_archive

log = logging.getLogger(__name__)


def purge_expired(limit: int = 500) -> int:
    """Delete archived bodies past their retention date. Job metadata is retained."""
    archive = get_archive()
    now = datetime.now(UTC)
    purged = 0

    with session_scope() as session:
        stale = session.scalars(
            select(Job)
            .where(Job.purged_at.is_(None), Job.purge_after.is_not(None), Job.purge_after < now)
            .limit(limit)
        ).all()

        for job in stale:
            if job.archive_key:
                try:
                    archive.delete(job.archive_key)
                except Exception:  # noqa: BLE001 - a missing object still needs the row cleared
                    log.warning("archive object %s already gone", job.archive_key)
            # Dropping the wrapped key is what actually makes this irreversible.
            job.wrapped_key = None
            job.archive_key = ""
            job.purged_at = now
            session.execute(
                ExtractedText.__table__.delete().where(ExtractedText.job_id == job.id)
            )
            purged += 1

    if purged:
        log.info("purged %d expired archive objects", purged)
    return purged
