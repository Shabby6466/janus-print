"""The HTTP contract the CUPS backend and the console depend on."""

from __future__ import annotations

from janusprint.db import session_scope
from janusprint.models import Job, JobState


def _inspect(client, data: bytes, queue: str = "office-laser", cups_job_id: str = "7"):
    return client.post(
        "/api/v1/inspect",
        data={
            "cups_job_id": cups_job_id,
            "queue": queue,
            "username": "jdoe",
            "hostname": "WS-4471",
            "title": "report.pdf",
            "copies": "1",
            "options": "",
        },
        files={"document": ("report.pdf", data, "application/pdf")},
    )


class TestInspectEndpoint:
    def test_clean_job_is_released(self, client, pdf_factory):
        response = _inspect(client, pdf_factory(["Quarterly facilities report"]))
        assert response.status_code == 200
        body = response.json()
        assert body["release"] is True
        assert body["action"] == "allow"

    def test_matching_job_is_held(self, client, pdf_factory):
        response = _inspect(client, pdf_factory(["Card 4111 1111 1111 1111 cardholder"]))
        body = response.json()
        assert body["release"] is False
        assert body["action"] == "hold"
        assert body["rule_ids"]

    def test_inspect_needs_no_authentication(self, client, pdf_factory):
        """The backend runs as `lp` on the print server and holds no credentials. The
        endpoint is protected by network placement, not by a session."""
        response = _inspect(client, pdf_factory(["hello"]))
        assert response.status_code == 200

    def test_garbage_payload_still_returns_a_verdict(self, client):
        response = _inspect(client, b"\x00\x01\x02\x03")
        assert response.status_code == 200
        assert "action" in response.json()


class TestPreflight:
    def test_unknown_job_is_not_pre_cleared(self, client):
        response = client.get("/api/v1/preflight", params={"queue": "office-laser", "cups_job_id": "1"})
        assert response.json()["pass_through"] is False

    def test_analyst_released_job_passes_through_on_reprint(self, authed_client, pdf_factory):
        """Resuming a held CUPS job re-runs the backend. Without this the job would be
        re-inspected, re-held, and could never actually print."""
        held = _inspect(authed_client, pdf_factory(["Card 4111 1111 1111 1111 cvv"]), cups_job_id="99")
        job_id = held.json()["job_id"]
        assert held.json()["action"] == "hold"

        release = authed_client.post(
            f"/api/v1/jobs/{job_id}/release", json={"reason": "false positive, test data"}
        )
        assert release.status_code == 200

        response = authed_client.get(
            "/api/v1/preflight", params={"queue": "office-laser", "cups_job_id": "99"}
        )
        assert response.json()["pass_through"] is True


class TestDecisions:
    def _held_job(self, client, pdf_factory) -> str:
        response = _inspect(client, pdf_factory(["Card 4111 1111 1111 1111 cardholder"]))
        return response.json()["job_id"]

    def test_release_requires_a_reason(self, authed_client, pdf_factory):
        job_id = self._held_job(authed_client, pdf_factory)
        response = authed_client.post(f"/api/v1/jobs/{job_id}/release", json={"reason": "x"})
        assert response.status_code == 422

    def test_release_records_actor_and_reason(self, authed_client, pdf_factory):
        job_id = self._held_job(authed_client, pdf_factory)
        response = authed_client.post(
            f"/api/v1/jobs/{job_id}/release", json={"reason": "test data, cleared with finance"}
        )
        assert response.status_code == 200

        with session_scope() as session:
            job = session.get(Job, job_id)
            assert job.state == JobState.released_by_analyst
            kinds = {event.kind: event for event in job.events}
            assert kinds["released"].actor == "admin"
            assert "finance" in kinds["released"].detail

    def test_deny_cancels_the_job(self, authed_client, pdf_factory):
        job_id = self._held_job(authed_client, pdf_factory)
        response = authed_client.post(
            f"/api/v1/jobs/{job_id}/deny", json={"reason": "genuine card data, not permitted"}
        )
        assert response.status_code == 200
        assert response.json()["state"] == "denied_by_analyst"

    def test_cannot_decide_a_job_twice(self, authed_client, pdf_factory):
        job_id = self._held_job(authed_client, pdf_factory)
        authed_client.post(f"/api/v1/jobs/{job_id}/release", json={"reason": "first decision"})
        second = authed_client.post(
            f"/api/v1/jobs/{job_id}/release", json={"reason": "second decision"}
        )
        assert second.status_code == 409

    def test_decisions_require_authentication(self, client, pdf_factory):
        job_id = self._held_job(client, pdf_factory)
        response = client.post(f"/api/v1/jobs/{job_id}/release", json={"reason": "no session"})
        assert response.status_code == 401


class TestContentGate:
    """PLAN.md §6 — the archive is the highest-value target in the system."""

    def _job(self, client, pdf_factory) -> str:
        return _inspect(client, pdf_factory(["Card 4111 1111 1111 1111 cardholder"])).json()["job_id"]

    def test_content_is_refused_without_an_approved_request(self, authed_client, pdf_factory):
        job_id = self._job(authed_client, pdf_factory)
        response = authed_client.get(f"/api/v1/jobs/{job_id}/content")
        assert response.status_code == 403

    def test_extracted_text_is_gated_too(self, authed_client, pdf_factory):
        """Extracted text is the same information as the document. Same gate."""
        job_id = self._job(authed_client, pdf_factory)
        assert authed_client.get(f"/api/v1/jobs/{job_id}/text").status_code == 403

    def test_cannot_approve_your_own_request(self, authed_client, pdf_factory):
        job_id = self._job(authed_client, pdf_factory)
        created = authed_client.post(
            f"/api/v1/jobs/{job_id}/content-requests",
            json={"reason": "investigating incident 4471"},
        )
        request_id = created.json()["id"]
        response = authed_client.post(f"/api/v1/jobs/content-requests/{request_id}/approve")
        assert response.status_code == 403

    def test_second_approver_unlocks_a_single_download(self, authed_client, pdf_factory):
        from janusprint.api.auth import hash_password
        from janusprint.models import User

        with session_scope() as session:
            session.add(
                User(
                    username="approver",
                    password_hash=hash_password("approver-pw"),
                    role="approver",
                )
            )

        job_id = self._job(authed_client, pdf_factory)
        request_id = authed_client.post(
            f"/api/v1/jobs/{job_id}/content-requests",
            json={"reason": "investigating incident 4471"},
        ).json()["id"]

        # Approve as somebody else.
        authed_client.get("/logout")
        authed_client.post(
            "/login", data={"username": "approver", "password": "approver-pw", "next": "/"}
        )
        assert (
            authed_client.post(f"/api/v1/jobs/content-requests/{request_id}/approve").status_code
            == 200
        )

        # Back as the requester: one download, then the grant is spent.
        authed_client.get("/logout")
        authed_client.post(
            "/login", data={"username": "admin", "password": "test-password", "next": "/"}
        )
        first = authed_client.get(f"/api/v1/jobs/{job_id}/content")
        assert first.status_code == 200
        assert first.content.startswith(b"%PDF")
        assert authed_client.get(f"/api/v1/jobs/{job_id}/content").status_code == 403

    def test_every_content_read_is_audited(self, authed_client, pdf_factory, monkeypatch):
        monkeypatch.setenv("JANUS_PRINT_REQUIRE_DUAL_APPROVAL_FOR_CONTENT", "false")
        from janusprint import config

        config.reset_caches()

        job_id = self._job(authed_client, pdf_factory)
        assert authed_client.get(f"/api/v1/jobs/{job_id}/content").status_code == 200

        log = authed_client.get("/api/v1/archive-access").json()
        assert any(row["job_id"] == job_id and row["kind"] == "content" for row in log)


class TestOps:
    def test_health_reports_capabilities(self, client):
        body = client.get("/api/v1/health").json()
        assert body["status"] == "ok"
        assert body["rules_loaded"] > 0
        assert "ocr_available" in body

    def test_rule_fixtures_pass_via_the_api(self, authed_client):
        body = authed_client.get("/api/v1/rules-test").json()
        assert body["failures"] == []


class TestConsole:
    def test_console_requires_a_session(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert "/login" in response.headers["location"]

    def test_dashboard_renders(self, authed_client, pdf_factory):
        _inspect(authed_client, pdf_factory(["Card 4111 1111 1111 1111 cardholder"]))
        response = authed_client.get("/")
        assert response.status_code == 200
        assert "Awaiting review" in response.text

    def test_job_page_renders_masked_samples(self, authed_client, pdf_factory):
        job_id = _inspect(
            authed_client, pdf_factory(["Card 4111 1111 1111 1111 cardholder"])
        ).json()["job_id"]
        response = authed_client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        assert "4111***********1111" in response.text
        assert "4111 1111 1111 1111" not in response.text
        assert "4111111111111111" not in response.text

    def test_all_console_pages_render(self, authed_client):
        for path in ["/", "/queue", "/rules", "/documents", "/policies", "/audit"]:
            assert authed_client.get(path).status_code == 200, path


class TestAdminCLI:
    """With dev_mode off nothing seeds an account, so the CLI is the only way in."""

    def test_create_and_authenticate(self, monkeypatch):
        from janusprint.admin_cli import cmd_create, cmd_list
        from janusprint.api.auth import authenticate

        monkeypatch.setenv("JANUS_PRINT_NEW_PASSWORD", "a-real-long-password")
        assert cmd_create("soc1", "approver") == 0
        assert cmd_list() == 0

        with session_scope() as session:
            user = authenticate(session, "soc1", "a-real-long-password")
            assert user is not None and user.role == "approver"
            assert authenticate(session, "soc1", "wrong") is None

    def test_duplicate_create_is_refused(self, monkeypatch):
        from janusprint.admin_cli import cmd_create

        monkeypatch.setenv("JANUS_PRINT_NEW_PASSWORD", "a-real-long-password")
        cmd_create("soc1", "analyst")
        with __import__("pytest").raises(SystemExit):
            cmd_create("soc1", "analyst")

    def test_passwd_resets_and_reactivates(self, monkeypatch):
        from janusprint.admin_cli import cmd_create, cmd_disable, cmd_passwd
        from janusprint.api.auth import authenticate

        monkeypatch.setenv("JANUS_PRINT_NEW_PASSWORD", "first-long-password")
        cmd_create("soc2", "analyst")
        cmd_disable("soc2")

        with session_scope() as session:
            assert authenticate(session, "soc2", "first-long-password") is None

        monkeypatch.setenv("JANUS_PRINT_NEW_PASSWORD", "second-long-password")
        cmd_passwd("soc2")
        with session_scope() as session:
            assert authenticate(session, "soc2", "second-long-password") is not None

    def test_unknown_user_is_an_error(self, monkeypatch):
        from janusprint.admin_cli import cmd_passwd

        monkeypatch.setenv("JANUS_PRINT_NEW_PASSWORD", "a-real-long-password")
        with __import__("pytest").raises(SystemExit):
            cmd_passwd("nobody")


class TestSchemaBootstrap:
    def test_concurrent_init_db_does_not_raise(self):
        """API and worker both run init_db on boot. Racing them must not kill either.

        On SQLite this exercises the plain path; the Postgres path takes an advisory lock,
        which is what stops the duplicate-key crash seen on a real deployment.
        """
        import threading

        from janusprint.db import init_db

        errors: list[Exception] = []

        def boot():
            try:
                init_db()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=boot) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors, errors

    def test_init_db_is_idempotent(self):
        from janusprint.db import init_db

        init_db()
        init_db()
