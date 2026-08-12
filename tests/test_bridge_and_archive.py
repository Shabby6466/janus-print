"""CEF output and archive/retention behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from janusprint.archive.retention import purge_expired
from janusprint.archive.store import ArchiveStore, FilesystemBlobs
from janusprint.bridge.cef import CEFEvent, event_for_job
from janusprint.config import get_settings
from janusprint.db import session_scope
from janusprint.inspector.engine import JobMetadata, inspect_job
from janusprint.models import Job, JobState


class TestCEF:
    def test_header_shape_matches_the_spec(self):
        event = CEFEvent("PAN_DETECTED", "Payment card in print job", 8, {"suser": "jdoe"})
        line = event.render(get_settings())
        assert line.startswith("CEF:0|Janus|PrintDLP|1.0|PAN_DETECTED|")
        assert "|8|" in line
        assert "suser=jdoe" in line

    def test_pipes_in_the_header_are_escaped(self):
        line = CEFEvent("A|B", "Name|With|Pipes", 5, {}).render(get_settings())
        assert "A\\|B" in line
        assert "Name\\|With\\|Pipes" in line

    def test_equals_in_extensions_is_escaped(self):
        line = CEFEvent("X", "X", 5, {"msg": "a=b"}).render(get_settings())
        assert "msg=a\\=b" in line

    def test_newlines_never_break_the_syslog_frame(self):
        line = CEFEvent("X", "X", 5, {"msg": "line one\nline two"}).render(get_settings())
        assert "\n" not in line

    def test_job_event_carries_no_raw_sensitive_data(self, session, pdf_factory):
        verdict = inspect_job(
            session,
            JobMetadata(cups_job_id="1", queue="office-laser", username="jdoe", title="x.pdf"),
            pdf_factory(["Card 4111 1111 1111 1111 cardholder J Smith"]),
        )
        session.flush()
        job = session.get(Job, verdict.job_id)
        line = event_for_job(job).render(get_settings())

        # The whole point of PLAN.md §7: content stays in the archive.
        assert "4111111111111111" not in line
        assert "4111 1111 1111 1111" not in line
        assert "J Smith" not in line
        # A masked sample does travel, so an analyst can triage without opening the doc.
        assert "cs5Label=maskedSample" in line
        assert "****" in line
        assert "cs2Label=jobId" in line

    def test_failed_open_gets_its_own_signature(self, session, pdf_factory, monkeypatch):
        from janusprint.inspector import engine

        monkeypatch.setattr(
            engine, "extract", lambda *_a: (_ for _ in ()).throw(RuntimeError("down"))
        )
        verdict = inspect_job(
            session,
            JobMetadata(cups_job_id="2", queue="office-laser", username="jdoe"),
            pdf_factory(["anything"]),
        )
        session.flush()
        job = session.get(Job, verdict.job_id)
        line = event_for_job(job).render(get_settings())
        assert "INSPECTION_FAILED_OPEN" in line

    def test_siem_outage_does_not_raise(self, monkeypatch):
        """A SIEM that is down must never affect printing."""
        from janusprint.bridge.cef import SIEMBridge

        monkeypatch.setenv("JANUS_PRINT_SIEM_ENABLED", "true")
        monkeypatch.setenv("JANUS_PRINT_SIEM_HOST", "203.0.113.255")
        monkeypatch.setenv("JANUS_PRINT_SIEM_PROTOCOL", "tcp")
        monkeypatch.setenv("JANUS_PRINT_SIEM_PORT", "1")
        from janusprint import config

        config.reset_caches()

        bridge = SIEMBridge()
        assert bridge.send(CEFEvent("X", "X", 5, {})) is False
        assert bridge.failed == 1


class TestArchive:
    def test_round_trip(self, tmp_path):
        store = ArchiveStore(blobs=FilesystemBlobs(tmp_path / "blobs"))
        key, wrapped, digest, purge_after = store.store("job1", b"secret document")
        assert store.load(key, wrapped) == b"secret document"
        assert len(digest) == 64
        assert purge_after > datetime.now(UTC)

    def test_objects_are_encrypted_at_rest(self, tmp_path):
        store = ArchiveStore(blobs=FilesystemBlobs(tmp_path / "blobs"))
        key, _wrapped, _digest, _purge = store.store("job1", b"the quick brown fox")
        assert b"quick brown fox" not in store.blobs.get(key)

    def test_each_object_gets_its_own_key(self, tmp_path):
        store = ArchiveStore(blobs=FilesystemBlobs(tmp_path / "blobs"))
        _k1, wrapped1, _d, _p = store.store("job1", b"same content")
        _k2, wrapped2, _d, _p = store.store("job2", b"same content")
        assert wrapped1 != wrapped2

    def test_traversal_keys_are_rejected(self, tmp_path):
        blobs = FilesystemBlobs(tmp_path / "blobs")
        import pytest

        with pytest.raises(ValueError):
            blobs.put("../../etc/passwd", b"x")

    def test_default_master_key_is_refused_outside_dev_mode(self, monkeypatch):
        monkeypatch.setenv("JANUS_PRINT_DEV_MODE", "false")
        monkeypatch.delenv("JANUS_PRINT_ARCHIVE_MASTER_KEY", raising=False)
        from janusprint import config

        config.reset_caches()

        import pytest

        with pytest.raises(RuntimeError, match="ARCHIVE_MASTER_KEY"):
            ArchiveStore()


class TestRetention:
    def test_expired_jobs_are_purged_and_keys_destroyed(self, pdf_factory):
        with session_scope() as session:
            verdict = inspect_job(
                session,
                JobMetadata(cups_job_id="1", queue="office-laser", username="jdoe"),
                pdf_factory(["Quarterly facilities report"]),
            )
            job_id = verdict.job_id

        with session_scope() as session:
            job = session.get(Job, job_id)
            job.purge_after = datetime.now(UTC) - timedelta(days=1)

        assert purge_expired() == 1

        with session_scope() as session:
            job = session.get(Job, job_id)
            assert job.purged_at is not None
            # Destroying the wrapped key is what makes this irreversible.
            assert job.wrapped_key is None
            assert job.archive_key == ""
            # Metadata survives — the audit trail outlives the content.
            assert job.username == "jdoe"
            assert job.content_sha256

    def test_live_jobs_are_untouched(self, pdf_factory):
        with session_scope() as session:
            inspect_job(
                session,
                JobMetadata(cups_job_id="1", queue="office-laser", username="jdoe"),
                pdf_factory(["Quarterly facilities report"]),
            )
        assert purge_expired() == 0

    def test_purged_content_cannot_be_downloaded(self, authed_client, pdf_factory):
        response = authed_client.post(
            "/api/v1/inspect",
            data={
                "cups_job_id": "3",
                "queue": "office-laser",
                "username": "jdoe",
                "title": "x.pdf",
                "copies": "1",
            },
            files={"document": ("x.pdf", pdf_factory(["hello"]), "application/pdf")},
        )
        job_id = response.json()["job_id"]

        with session_scope() as session:
            job = session.get(Job, job_id)
            job.purge_after = datetime.now(UTC) - timedelta(days=1)
        purge_expired()

        assert authed_client.get(f"/api/v1/jobs/{job_id}/content").status_code == 410


def test_job_states_are_distinguishable():
    """failed_open must never be confused with a clean release."""
    assert JobState.failed_open != JobState.released
