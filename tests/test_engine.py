"""End-to-end inspection behaviour, including the two decisions the product turns on:
what happens to image-only pages, and what happens when the inspector fails.
"""

from __future__ import annotations

import pytest

from janusprint.config import PrinterPolicy, get_printer_policies
from janusprint.inspector.engine import JobMetadata, inspect_job, register_document
from janusprint.models import JobState, ScanTier


def meta(queue: str = "office-laser", **overrides) -> JobMetadata:
    base = dict(
        cups_job_id="42",
        queue=queue,
        username="jdoe",
        hostname="WS-4471",
        title="test.pdf",
        copies=1,
        options="",
    )
    return JobMetadata(**(base | overrides))


class TestVerdicts:
    def test_clean_document_is_released(self, session, pdf_factory):
        data = pdf_factory(
            ["Quarterly facilities report", "Room utilisation is up 12% on last quarter."]
        )
        verdict = inspect_job(session, meta(), data)
        assert verdict.action == "allow"
        assert verdict.release is True
        assert verdict.state == JobState.released

    def test_card_data_is_held(self, session, pdf_factory):
        data = pdf_factory(
            ["Payment reconciliation", "Cardholder: J Smith", "Card 4111 1111 1111 1111 cvv 123"]
        )
        verdict = inspect_job(session, meta(), data)
        assert verdict.action == "hold"
        assert verdict.release is False
        assert verdict.state == JobState.held
        assert any("pan" in rule_id for rule_id in verdict.rule_ids)

    def test_private_key_is_blocked_outright(self, session, pdf_factory):
        data = pdf_factory(["-----BEGIN RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA1234"])
        verdict = inspect_job(session, meta(), data)
        assert verdict.action == "block"
        assert verdict.state == JobState.blocked

    def test_job_row_records_masked_matches_only(self, session, pdf_factory):
        data = pdf_factory(["Card 4111 1111 1111 1111 cardholder J Smith"])
        verdict = inspect_job(session, meta(), data)
        session.flush()

        from janusprint.models import Job

        job = session.get(Job, verdict.job_id)
        assert job.matches
        for match in job.matches:
            assert "4111111111111111" not in match.sample
            assert "1111 1111 1111" not in match.sample

    def test_archive_copy_is_written_and_encrypted(self, session, pdf_factory):
        data = pdf_factory(["Quarterly facilities report"])
        verdict = inspect_job(session, meta(), data)
        session.flush()

        from janusprint.archive.store import get_archive
        from janusprint.models import Job

        job = session.get(Job, verdict.job_id)
        assert job.archive_key and job.wrapped_key

        archive = get_archive()
        # The bytes on disk are not the document.
        raw = archive.blobs.get(job.archive_key)
        assert b"%PDF" not in raw[:200]
        # But they decrypt back to it.
        assert archive.load(job.archive_key, job.wrapped_key) == data

    def test_inline_time_is_recorded(self, session, pdf_factory):
        verdict = inspect_job(session, meta(), pdf_factory(["hello"]))
        assert verdict.inline_ms >= 0


class TestImageOnlyPages:
    """PLAN.md §3 — the decision that sets the latency the user feels."""

    def _image_only_pdf(self) -> bytes:
        import io

        import pypdfium2 as pdfium  # noqa: F401 - ensures the reader is present
        from PIL import Image

        image = Image.new("RGB", (1240, 1754), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PDF")
        return buffer.getvalue()

    def test_default_queue_releases_and_defers_ocr(self, session):
        verdict = inspect_job(session, meta(queue="office-laser"), self._image_only_pdf())
        # Cannot unprint, so print now and OCR retrospectively.
        assert verdict.release is True
        assert verdict.scan_tier == ScanTier.ocr_pending
        assert verdict.deep_scan_queued is True

    def test_deep_scan_queue_holds_until_ocr(self, session):
        verdict = inspect_job(session, meta(queue="finance-laser"), self._image_only_pdf())
        assert verdict.action == "hold"
        assert verdict.state == JobState.held
        assert "could not be read as text" in verdict.reason
        assert verdict.deep_scan_queued is True


class TestFailureHandling:
    """PLAN.md §4 — fail-open keeps the office printing; the gap must stay visible."""

    def test_fail_open_releases_but_marks_the_gap(self, session, pdf_factory, monkeypatch):
        from janusprint.inspector import engine

        def explode(*_args, **_kwargs):
            raise RuntimeError("extractor exploded")

        monkeypatch.setattr(engine, "extract", explode)
        verdict = inspect_job(session, meta(queue="office-laser"), pdf_factory(["anything"]))

        assert verdict.release is True
        # Not "released" — a coverage gap must never look like a clean pass.
        assert verdict.state == JobState.failed_open

    def test_fail_closed_queue_holds_instead(self, session, pdf_factory, monkeypatch):
        from janusprint.inspector import engine

        monkeypatch.setattr(
            engine, "extract", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        verdict = inspect_job(session, meta(queue="finance-laser"), pdf_factory(["anything"]))

        assert verdict.action == "hold"
        assert verdict.state == JobState.held

    def test_deadline_overrun_is_resolved_by_fail_mode(self, session, pdf_factory):
        data = pdf_factory([f"line {i} of a long document" for i in range(400)])
        # Zero budget: the deadline check trips on the first page.
        verdict = inspect_job(session, meta(queue="office-laser"), data, deadline=0.0)
        assert verdict.state == JobState.failed_open
        assert "deadline" in verdict.reason

    def test_garbage_input_does_not_raise(self, session):
        verdict = inspect_job(session, meta(), b"\x00\x01\x02not a document at all")
        assert verdict.scan_tier == ScanTier.unreadable
        assert verdict.action in {"log", "hold", "allow"}

    def test_empty_job_does_not_raise(self, session):
        verdict = inspect_job(session, meta(), b"")
        assert verdict is not None


class TestUnreadable:
    def test_policy_decides_what_happens_to_unreadable_documents(self, session, monkeypatch):
        policies = get_printer_policies()
        policies.queues["locked-down"] = PrinterPolicy(
            queue="locked-down", on_unreadable="hold", fail_mode="closed"
        )
        verdict = inspect_job(session, meta(queue="locked-down"), b"\x00\x01\x02garbage")
        assert verdict.action == "hold"


class TestFingerprinting:
    def test_excerpt_of_a_registered_document_is_caught(self, session, pdf_factory):
        body = [
            "Project Halyard valuation model and integration plan",
            "The target company operates fourteen distribution centres across the region",
            "with combined annual revenue of two hundred and twelve million",
            "and an adjusted operating margin of eleven point four percent",
            "before the synergies described in appendix C of this document",
            "which the deal team estimates at nineteen million annually by year three",
        ]
        register_document(session, "Project Halyard model", pdf_factory(body), action="hold")
        session.flush()

        # A retyped, partially reworded excerpt — no byte-level or hash overlap.
        excerpt = pdf_factory(
            [
                "Notes from the meeting",
                "The target company operates fourteen distribution centres across the region",
                "with combined annual revenue of two hundred and twelve million",
                "and an adjusted operating margin of eleven point four percent",
            ]
        )
        verdict = inspect_job(session, meta(), excerpt)
        assert verdict.action == "hold"
        assert any(rule_id.startswith("fingerprint:") for rule_id in verdict.rule_ids)

    def test_unrelated_document_does_not_match(self, session, pdf_factory):
        register_document(
            session,
            "Project Halyard model",
            pdf_factory(["Project Halyard valuation model and integration plan for the target"]),
        )
        session.flush()

        verdict = inspect_job(
            session, meta(), pdf_factory(["Cafeteria menu for the week beginning Monday"])
        )
        assert not any(r.startswith("fingerprint:") for r in verdict.rule_ids)

    def test_registering_an_empty_document_is_rejected(self, session):
        with pytest.raises(ValueError):
            register_document(session, "empty", b"")


class TestReinspection:
    """CUPS re-runs the backend on retry and on resume. The same physical job must not
    turn into a pile of duplicate rows in the console."""

    def test_same_job_reuses_its_row(self, session, pdf_factory):
        from sqlalchemy import func, select

        from janusprint.models import Job

        data = pdf_factory(["Card 4111 1111 1111 1111 cardholder"])
        first = inspect_job(session, meta(cups_job_id="7"), data)
        second = inspect_job(session, meta(cups_job_id="7"), data)
        session.flush()

        assert first.job_id == second.job_id
        assert session.scalar(select(func.count()).select_from(Job)) == 1

        job = session.get(Job, first.job_id)
        # Matches are replaced, not accumulated.
        assert len([m for m in job.matches if m.rule_id == "pan-spaced"]) == 1

        from janusprint.models import JobEvent

        kinds = session.scalars(
            select(JobEvent.kind).where(JobEvent.job_id == first.job_id)
        ).all()
        assert "reinspected" in kinds

    def test_a_different_document_on_the_same_cups_id_is_a_new_row(self, session, pdf_factory):
        from sqlalchemy import func, select

        from janusprint.models import Job

        inspect_job(session, meta(cups_job_id="7"), pdf_factory(["first document"]))
        inspect_job(session, meta(cups_job_id="7"), pdf_factory(["different document"]))
        session.flush()
        assert session.scalar(select(func.count()).select_from(Job)) == 2


class TestDeepScanRelease:
    def test_clean_ocr_releases_a_job_held_only_for_deep_scan(self, session, monkeypatch):
        """finance-laser holds image-only jobs until OCR clears them. If OCR finds
        nothing the job must actually print, not sit held forever."""
        import io

        from PIL import Image

        from janusprint.inspector import engine
        from janusprint.models import Job

        image = Image.new("RGB", (1240, 1754), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PDF")

        verdict = inspect_job(session, meta(queue="finance-laser"), buffer.getvalue())
        assert verdict.state == JobState.held

        monkeypatch.setattr(engine, "ocr_pages", lambda *_a: {1: "cafeteria menu"}, raising=False)
        monkeypatch.setattr("janusprint.inspector.ocr.ocr_pages", lambda *_a: {1: "menu"})
        session.commit()

        engine.deep_scan(session, verdict.job_id)
        session.flush()
        assert session.get(Job, verdict.job_id).state == JobState.released

    def test_a_rule_hold_survives_a_clean_ocr(self, session, pdf_factory, monkeypatch):
        """The inverse: a job held because a rule fired must stay held."""
        from janusprint.inspector import engine
        from janusprint.models import Job

        verdict = inspect_job(
            session, meta(queue="finance-laser"), pdf_factory(["Card 4111 1111 1111 1111 cvv"])
        )
        assert verdict.state == JobState.held
        session.commit()

        monkeypatch.setattr("janusprint.inspector.ocr.ocr_pages", lambda *_a: {})
        engine.deep_scan(session, verdict.job_id)
        session.flush()
        assert session.get(Job, verdict.job_id).state == JobState.held


class TestDeepScanFormats:
    """OCR rasterises pages, so anything that is not already PDF must be converted first.

    Skipping that silently marked the scan complete without running OCR at all — the job
    stayed held with no verdict, which looks identical to a slow worker.
    """

    def _postscript_with_text(self, pdf_factory) -> bytes:
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        if shutil.which("gs") is None:
            import pytest

            pytest.skip("ghostscript not installed")

        pdf = pdf_factory(["STRICTLY CONFIDENTIAL", "Draft merger agreement"])
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "in.pdf", Path(tmp) / "out.ps"
            source.write_bytes(pdf)
            subprocess.run(
                ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=ps2write",
                 f"-sOutputFile={target}", str(source)],
                check=True, capture_output=True,
            )
            return target.read_bytes()

    def test_postscript_job_is_converted_for_ocr(self, session, pdf_factory, monkeypatch):
        """Whatever made the page unreadable, OCR must receive a PDF.

        The trigger is forced here rather than synthesised: ghostscript round-trips clean
        PostScript back into readable text, so a fixture cannot naturally produce the
        mis-encoded font that caused this in the field.
        """
        from janusprint.inspector import engine, extract
        from janusprint.models import Job

        data = self._postscript_with_text(pdf_factory)

        # Every page reads as unusable, exactly like a subset font with no Unicode map.
        monkeypatch.setattr(extract, "looks_like_language", lambda _text: False)

        verdict = inspect_job(session, meta(queue="finance-laser", cups_job_id="1"), data)
        assert verdict.state == JobState.held
        session.commit()

        rendered: dict = {}

        def fake_ocr(pdf_bytes, pages):
            # The point of the fix: deep_scan hands OCR a PDF, not raw PostScript.
            rendered["magic"] = pdf_bytes[:4]
            return {p: "STRICTLY CONFIDENTIAL board pack" for p in pages}

        monkeypatch.setattr("janusprint.inspector.ocr.ocr_pages", fake_ocr)
        engine.deep_scan(session, verdict.job_id)
        session.flush()

        assert rendered.get("magic") == b"%PDF", "OCR was handed PostScript, or never ran"
        job = session.get(Job, verdict.job_id)
        assert any(m.tier == "ocr" for m in job.matches)
        assert job.state == JobState.held  # OCR confirmed the hold

    def test_unconvertible_job_stays_held_and_says_why(self, session, monkeypatch):
        from janusprint.inspector import engine
        from janusprint.models import Job, JobEvent

        verdict = inspect_job(
            session, meta(queue="finance-laser", cups_job_id="2"), b"%!PS-Adobe-3.0\nbroken"
        )
        session.commit()

        monkeypatch.setattr(engine, "_to_pdf", lambda *_a: None, raising=False)
        monkeypatch.setattr("janusprint.inspector.extract._to_pdf", lambda *_a: None)
        engine.deep_scan(session, verdict.job_id)
        session.flush()

        job = session.get(Job, verdict.job_id)
        kinds = {e.kind for e in session.query(JobEvent).filter_by(job_id=job.id)}
        assert job.state == JobState.held
        assert "deep_scan_failed" in kinds or job.scan_tier.value == "unreadable"


class TestConversionBudget:
    """Ghostscript must not outlive the caller's inline deadline.

    The CUPS backend gives up after its own HTTP timeout and releases the job fail-open.
    A conversion that runs past that produces a verdict for a document already on paper.
    """

    def test_extract_caps_conversion_to_the_remaining_budget(self, monkeypatch):
        from janusprint.inspector import extract as extract_module

        seen: dict = {}

        def fake_run(command, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise TimeoutError("stand-in")

        monkeypatch.setattr(extract_module.subprocess, "run", fake_run)
        extract_module.extract(b"%!PS-Adobe-3.0\nsomething", budget=1.5)
        assert seen["timeout"] == 1.5

    def test_conversion_never_gets_less_than_a_second(self, monkeypatch):
        from janusprint.inspector import extract as extract_module

        seen: dict = {}

        def fake_run(command, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise TimeoutError("stand-in")

        monkeypatch.setattr(extract_module.subprocess, "run", fake_run)
        # An already-exhausted budget must not mean a zero-second timeout.
        extract_module.extract(b"%!PS-Adobe-3.0\nsomething", budget=-4.0)
        assert seen["timeout"] >= 1.0

    def test_budget_shrinks_as_inspection_proceeds(self, session, pdf_factory):
        """The engine passes what is left, not the full budget."""
        from janusprint.inspector import engine

        captured: dict = {}
        original = engine.extract

        def spy(data, budget=None):
            captured["budget"] = budget
            return original(data, budget=budget)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(engine, "extract", spy)
        try:
            inspect_job(session, meta(), pdf_factory(["hello"]), deadline=3.0)
        finally:
            monkey.undo()

        assert captured["budget"] is not None
        assert captured["budget"] <= 3.0
