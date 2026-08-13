"""Printer queue management.

The property that matters: a queue's row and the actual CUPS queue must never silently
disagree. A row claiming "fail-closed, deep scan required" with no CUPS queue behind it
reads as configured while inspecting nothing.
"""

from __future__ import annotations

import pytest

from janusprint import printers as printer_store
from janusprint.api import cups_control
from janusprint.inspector.engine import JobMetadata, inspect_job
from janusprint.models import Job, JobState, PrinterQueue


def payload(**overrides) -> dict:
    base = {
        "name": "finance-mfp",
        "device_uri": "ipp://172.18.104.60/ipp/print",
        "location": "2nd floor",
        "fail_mode": "closed",
        "on_unreadable": "hold",
        "rule_tags": ["pci", "financial"],
        "deep_scan_required": True,
        "shared": True,
    }
    return base | overrides


class TestUriHandling:
    def test_janus_wrapper_is_derived_not_typed(self):
        assert cups_control.janus_uri("ipp://10.0.0.5/ipp/print") == "janus://ipp/10.0.0.5/ipp/print"
        assert cups_control.janus_uri("socket://10.0.0.9:9100") == "janus://socket/10.0.0.9:9100"

    @pytest.mark.parametrize(
        "uri", ["", "10.0.0.5", "ipp:/10.0.0.5", "ftp://10.0.0.5/x", "not a uri"]
    )
    def test_bad_device_uris_are_refused(self, uri):
        with pytest.raises(cups_control.CupsControlError):
            cups_control.validate_device_uri(uri)

    @pytest.mark.parametrize("name", ["", "has space", "has/slash", "a" * 200, "semi;colon"])
    def test_bad_queue_names_are_refused(self, name):
        with pytest.raises(cups_control.CupsControlError):
            cups_control.validate_name(name)


class TestCrud:
    def test_create_records_the_queue_and_policy(self, session):
        row = printer_store.create(session, payload(), actor="admin", note="new mfp")
        assert row.name == "finance-mfp"
        assert row.janus_uri == "janus://ipp/172.18.104.60/ipp/print"
        assert row.fail_mode == "closed"
        assert row.deep_scan_required is True

    def test_duplicate_name_is_refused(self, session):
        printer_store.create(session, payload(), actor="admin")
        with pytest.raises(printer_store.PrinterError):
            printer_store.create(session, payload(), actor="admin")

    def test_invalid_policy_is_refused(self, session):
        with pytest.raises(printer_store.PrinterError):
            printer_store.create(session, payload(fail_mode="maybe"), actor="admin")
        with pytest.raises(printer_store.PrinterError):
            printer_store.create(session, payload(on_unreadable="shred"), actor="admin")

    def test_update_changes_policy(self, session):
        printer_store.create(session, payload(), actor="admin")
        row = printer_store.update(
            session, "finance-mfp", {"fail_mode": "open"}, actor="admin", note="too strict"
        )
        assert row.fail_mode == "open"

    def test_delete_removes_the_row(self, session):
        printer_store.create(session, payload(), actor="admin")
        printer_store.delete(session, "finance-mfp", actor="admin", note="decommissioned")
        assert session.get(PrinterQueue, "finance-mfp") is None

    def test_cannot_delete_a_queue_with_held_jobs(self, session, pdf_factory):
        """Removing the queue would strand them — no way to release, no way to deny."""
        printer_store.create(session, payload(name="office-x"), actor="admin")
        session.flush()

        inspect_job(
            session,
            JobMetadata(cups_job_id="1", queue="office-x", username="jdoe"),
            pdf_factory(["Card 4111 1111 1111 1111 cardholder"]),
        )
        session.flush()

        with pytest.raises(printer_store.PrinterError, match="still held"):
            printer_store.delete(session, "office-x", actor="admin")

    def test_every_change_is_audited(self, session):
        printer_store.create(session, payload(), actor="alice", note="initial")
        printer_store.update(session, "finance-mfp", {"fail_mode": "open"}, actor="bob", note="relax")
        history = printer_store.revisions(session, "finance-mfp")
        assert [r.change for r in history] == ["updated", "created"]
        assert history[0].actor == "bob"


class TestPolicyTakesEffect:
    def test_policy_applies_to_the_next_job_on_that_queue(self, session, pdf_factory):
        """A queue created in the UI must actually change how its jobs are handled."""
        printer_store.create(
            session,
            payload(name="secure-mfp", deep_scan_required=True, fail_mode="closed"),
            actor="admin",
        )
        session.flush()

        import io

        from PIL import Image

        image = Image.new("RGB", (1240, 1754), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PDF")

        verdict = inspect_job(
            session,
            JobMetadata(cups_job_id="1", queue="secure-mfp", username="jdoe"),
            buffer.getvalue(),
        )
        # deep_scan_required holds image-only pages instead of releasing them.
        assert verdict.state == JobState.held
        assert "no text layer" in verdict.reason

    def test_rule_tags_scope_detection_per_printer(self, session, pdf_factory):
        printer_store.create(
            session, payload(name="label-printer", rule_tags=["secrets"], fail_mode="open"), actor="admin"
        )
        session.flush()

        # A payment card is not in the "secrets" tag, so this queue ignores it.
        verdict = inspect_job(
            session,
            JobMetadata(cups_job_id="1", queue="label-printer", username="jdoe"),
            pdf_factory(["Card 4111 1111 1111 1111 cardholder"]),
        )
        assert verdict.action == "allow"

        # A private key is tagged "secrets", so it still blocks.
        verdict = inspect_job(
            session,
            JobMetadata(cups_job_id="2", queue="label-printer", username="jdoe"),
            pdf_factory(["-----BEGIN RSA PRIVATE KEY-----", "MIIEowIBAAKC"]),
        )
        assert verdict.action == "block"

    def test_unknown_queue_falls_back_to_a_default_policy(self, session, pdf_factory):
        """A queue nobody configured must still be inspected, not error on the print path."""
        verdict = inspect_job(
            session,
            JobMetadata(cups_job_id="1", queue="never-configured", username="jdoe"),
            pdf_factory(["Card 4111 1111 1111 1111 cardholder"]),
        )
        assert verdict.action == "hold"

    def test_policy_change_is_visible_immediately(self, session, pdf_factory):
        printer_store.create(
            session, payload(name="q1", rule_tags=["*"], deep_scan_required=False), actor="admin"
        )
        session.flush()
        first = inspect_job(
            session,
            JobMetadata(cups_job_id="1", queue="q1", username="jdoe"),
            pdf_factory(["Card 4111 1111 1111 1111 cardholder"]),
        )
        assert first.action == "hold"

        printer_store.update(session, "q1", {"rule_tags": ["secrets"]}, actor="admin")
        session.flush()

        second = inspect_job(
            session,
            JobMetadata(cups_job_id="2", queue="q1", username="jdoe"),
            pdf_factory(["Card 4111 1111 1111 1111 cardholder"]),
        )
        assert second.action == "allow"


class TestReconcile:
    def test_reports_managed_queues(self, session):
        printer_store.create(session, payload(), actor="admin")
        session.flush()
        report = printer_store.reconcile(session)
        assert "finance-mfp" in report["managed"]


class TestApi:
    def test_create_and_list(self, authed_client):
        response = authed_client.post("/api/v1/printers", json=payload() | {"note": "new"})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["janus_uri"] == "janus://ipp/172.18.104.60/ipp/print"

        listed = authed_client.get("/api/v1/printers").json()
        assert any(p["name"] == "finance-mfp" for p in listed)

    def test_bad_uri_returns_422(self, authed_client):
        response = authed_client.post(
            "/api/v1/printers", json=payload(device_uri="ftp://x/y") | {"note": "bad"}
        )
        assert response.status_code == 422

    def test_janus_uri_is_rejected_as_input(self, authed_client):
        """The wrapper is applied for you; typing it would double-wrap."""
        response = authed_client.post(
            "/api/v1/printers",
            json=payload(device_uri="janus://ipp/10.0.0.5/ipp/print") | {"note": "x"},
        )
        assert response.status_code == 422

    def test_patch_policy(self, authed_client):
        authed_client.post("/api/v1/printers", json=payload() | {"note": "new"})
        response = authed_client.patch(
            "/api/v1/printers/finance-mfp", json={"fail_mode": "open", "note": "relaxed"}
        )
        assert response.status_code == 200
        assert response.json()["fail_mode"] == "open"

    def test_non_admin_cannot_manage_printers(self, authed_client):
        from janusprint.api.auth import hash_password
        from janusprint.db import session_scope
        from janusprint.models import User

        with session_scope() as session:
            session.add(
                User(username="ana", password_hash=hash_password("a-long-test-password"), role="analyst")
            )
        authed_client.get("/logout")
        authed_client.post(
            "/login", data={"username": "ana", "password": "a-long-test-password", "next": "/"}
        )
        assert authed_client.post("/api/v1/printers", json=payload()).status_code == 403
        assert authed_client.delete("/api/v1/printers/finance-mfp").status_code == 403
        # Reading the list is fine.
        assert authed_client.get("/api/v1/printers").status_code == 200

    def test_console_page_renders(self, authed_client):
        authed_client.post("/api/v1/printers", json=payload() | {"note": "new"})
        page = authed_client.get("/printers")
        assert page.status_code == 200
        assert "finance-mfp" in page.text

    def test_revisions_endpoint(self, authed_client):
        authed_client.post("/api/v1/printers", json=payload() | {"note": "audit me"})
        history = authed_client.get("/api/v1/printers/revisions?queue=finance-mfp").json()
        assert history[0]["note"] == "audit me"
        assert history[0]["actor"] == "admin"


class TestDiagnostics:
    """Two separate questions: is the device reachable, and does the whole chain work."""

    def test_test_page_pdf_is_valid_and_self_describing(self):
        from janusprint.testpage import build_test_page

        pdf = build_test_page("finance-mfp", "admin", "commissioning check")
        assert pdf.startswith(b"%PDF-1.4")
        assert pdf.rstrip().endswith(b"%%EOF")
        assert b"finance-mfp" in pdf
        assert b"admin" in pdf

    def test_test_page_is_extractable_and_triggers_no_rules(self, session):
        """A test page that gets held teaches the operator the printer is broken when it
        is not — so it must read cleanly through the real inspection path."""
        from janusprint.inspector.extract import extract
        from janusprint.testpage import build_test_page

        pdf = build_test_page("finance-mfp", "admin")
        result = extract(pdf)
        assert not result.unreadable
        assert "janus-print test page" in result.text

        printer_store.create(session, payload(name="diag-q", fail_mode="open"), actor="admin")
        session.flush()
        verdict = inspect_job(
            session, JobMetadata(cups_job_id="1", queue="diag-q", username="admin"), pdf
        )
        assert verdict.action == "allow"

    def test_pdf_escapes_parentheses(self):
        """An unescaped bracket in a queue name would corrupt the PDF content stream."""
        from janusprint.inspector.extract import extract
        from janusprint.testpage import build_test_page

        pdf = build_test_page("weird(name)", "admin")
        assert not extract(pdf).unreadable

    def test_device_endpoint_defaults_by_scheme(self):
        assert cups_control.device_endpoint("ipp://10.0.0.5/ipp/print") == ("10.0.0.5", 631)
        assert cups_control.device_endpoint("socket://10.0.0.9:9100") == ("10.0.0.9", 9100)
        assert cups_control.device_endpoint("socket://10.0.0.9") == ("10.0.0.9", 9100)
        assert cups_control.device_endpoint("lpd://10.0.0.3/queue") == ("10.0.0.3", 515)

    def test_unprobeable_scheme_is_reported(self):
        with pytest.raises(cups_control.CupsControlError):
            cups_control.device_endpoint("usb://HP/LaserJet")

    def test_connection_check_reports_unreachable_without_raising(self, authed_client):
        """An unreachable printer is a diagnostic result, not a server error."""
        # RFC 5737 TEST-NET-1: guaranteed never routed, so this always fails to connect.
        authed_client.post(
            "/api/v1/printers",
            json=payload(name="dead-printer", device_uri="socket://192.0.2.1:9100") | {"note": "x"},
        )
        response = authed_client.post("/api/v1/printers/dead-printer/test-connection")
        assert response.status_code == 200
        body = response.json()
        assert body["device_reachable"] is False
        assert body["ok"] is False
        assert body["device_error"]

    def test_loopback_device_is_not_probed(self, authed_client):
        """A loopback device address is relative to the spooler. Probing it from the API
        host would test the API against itself and report a meaningless refusal."""
        authed_client.post(
            "/api/v1/printers",
            json=payload(name="local-q", device_uri="ipp://localhost/printers/x") | {"note": "x"},
        )
        body = authed_client.post("/api/v1/printers/local-q/test-connection").json()
        assert body["device_reachable"] is None
        assert "loopback" in body["device_error"]

    def test_connection_check_on_unknown_queue_is_404(self, authed_client):
        assert authed_client.post("/api/v1/printers/nope/test-connection").status_code == 404

    def test_test_page_refused_when_cups_control_is_off(self, authed_client):
        """Better an explicit refusal than a silent success that prints nothing."""
        authed_client.post("/api/v1/printers", json=payload() | {"note": "x"})
        response = authed_client.post("/api/v1/printers/finance-mfp/test-page", json={"note": "hi"})
        assert response.status_code == 409
        assert "CUPS control is disabled" in response.json()["detail"]

    def test_diagnostics_are_admin_only(self, authed_client):
        from janusprint.api.auth import hash_password
        from janusprint.db import session_scope
        from janusprint.models import User

        authed_client.post("/api/v1/printers", json=payload() | {"note": "x"})
        with session_scope() as session:
            session.add(
                User(username="ana", password_hash=hash_password("a-long-test-password"), role="analyst")
            )
        authed_client.get("/logout")
        authed_client.post(
            "/login", data={"username": "ana", "password": "a-long-test-password", "next": "/"}
        )
        assert authed_client.post("/api/v1/printers/finance-mfp/test-connection").status_code == 403
        assert authed_client.post(
            "/api/v1/printers/finance-mfp/test-page", json={}
        ).status_code == 403


class TestOrphanedRows:
    def test_row_for_a_missing_cups_queue_can_still_be_deleted(self, session, monkeypatch):
        """A failed creation leaves a row with no CUPS queue behind it. If deletion
        insists CUPS remove something that was never there, the row is stuck forever."""
        printer_store.create(session, payload(name="orphan"), actor="admin")
        session.flush()

        def missing(_name):
            raise cups_control.CupsControlError(
                "lpadmin -x orphan failed: lpadmin: The printer or class does not exist."
            )

        monkeypatch.setattr(cups_control, "delete_queue", missing)
        printer_store.delete(session, "orphan", actor="admin", note="cleaning up")
        assert session.get(PrinterQueue, "orphan") is None

    def test_a_real_cups_failure_still_blocks_deletion(self, session, monkeypatch):
        """Only 'does not exist' is benign. Anything else means CUPS still has the queue,
        and dropping the row would hide a live un-inspected route."""
        printer_store.create(session, payload(name="stubborn"), actor="admin")
        session.flush()

        def refused(_name):
            raise cups_control.CupsControlError("lpadmin: permission denied")

        monkeypatch.setattr(cups_control, "delete_queue", refused)
        with pytest.raises(printer_store.PrinterError):
            printer_store.delete(session, "stubborn", actor="admin")
        assert session.get(PrinterQueue, "stubborn") is not None
