"""Role-based access control and document preview.

The RBAC tests are written from the attacker's side: for each privileged action, prove the
role below it is refused. A permission model nobody tested is a permission model that
quietly admits everyone.
"""

from __future__ import annotations

from janusprint.api.auth import hash_password
from janusprint.db import session_scope
from janusprint.models import Job, JobState, User


def make_user(username: str, role: str, password: str = "a-long-test-password") -> None:
    with session_scope() as session:
        session.add(
            User(username=username, password_hash=hash_password(password), role=role)
        )


def login(client, username: str, password: str = "a-long-test-password"):
    client.get("/logout")
    response = client.post(
        "/login", data={"username": username, "password": password, "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303, f"{username} could not sign in"
    return client


def held_job(client, pdf_factory, cups_job_id: str = "1") -> str:
    response = client.post(
        "/api/v1/inspect",
        data={
            "cups_job_id": cups_job_id,
            "queue": "office-laser",
            "username": "jdoe",
            "title": "report.pdf",
            "copies": "1",
        },
        files={
            "document": (
                "r.pdf",
                pdf_factory(["Card 4111 1111 1111 1111 cardholder"]),
                "application/pdf",
            )
        },
    )
    assert response.json()["action"] == "hold"
    return response.json()["job_id"]


class TestRoleGates:
    def test_viewer_cannot_release_or_deny(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        make_user("val", "viewer")
        login(authed_client, "val")

        assert authed_client.post(
            f"/api/v1/jobs/{job_id}/release", json={"reason": "let me through"}
        ).status_code == 403
        assert authed_client.post(
            f"/api/v1/jobs/{job_id}/deny", json={"reason": "let me through"}
        ).status_code == 403

    def test_viewer_can_still_read_the_queue(self, authed_client, pdf_factory):
        held_job(authed_client, pdf_factory)
        make_user("val", "viewer")
        login(authed_client, "val")

        assert authed_client.get("/api/v1/jobs").status_code == 200
        assert authed_client.get("/queue").status_code == 200

    def test_viewer_cannot_request_content(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        make_user("val", "viewer")
        login(authed_client, "val")
        assert authed_client.post(
            f"/api/v1/jobs/{job_id}/content-requests", json={"reason": "curious"}
        ).status_code == 403

    def test_analyst_cannot_approve_content(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        make_user("ana", "analyst")
        login(authed_client, "ana")
        request_id = authed_client.post(
            f"/api/v1/jobs/{job_id}/content-requests", json={"reason": "investigating"}
        ).json()["id"]

        make_user("ana2", "analyst")
        login(authed_client, "ana2")
        assert authed_client.post(
            f"/api/v1/jobs/content-requests/{request_id}/approve"
        ).status_code == 403

    def test_analyst_cannot_manage_users_or_rules(self, authed_client):
        make_user("ana", "analyst")
        login(authed_client, "ana")
        assert authed_client.get("/api/v1/users").status_code == 403
        assert authed_client.post(
            "/api/v1/users",
            json={"username": "x", "password": "a-long-test-password", "role": "admin"},
        ).status_code == 403
        assert authed_client.post("/api/v1/rules", json={"name": "x", "pattern": "x"}).status_code == 403

    def test_analyst_can_decide(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        make_user("ana", "analyst")
        login(authed_client, "ana")
        assert authed_client.post(
            f"/api/v1/jobs/{job_id}/release", json={"reason": "cleared with finance"}
        ).status_code == 200


class TestUserManagement:
    def test_admin_creates_and_user_can_sign_in(self, authed_client):
        response = authed_client.post(
            "/api/v1/users",
            json={
                "username": "newbie",
                "password": "a-long-test-password",
                "role": "analyst",
            },
        )
        assert response.status_code == 201
        login(authed_client, "newbie")

    def test_short_passwords_are_refused(self, authed_client):
        response = authed_client.post(
            "/api/v1/users", json={"username": "weak", "password": "short", "role": "viewer"}
        )
        assert response.status_code == 422

    def test_unknown_role_is_refused(self, authed_client):
        response = authed_client.post(
            "/api/v1/users",
            json={"username": "x", "password": "a-long-test-password", "role": "superuser"},
        )
        assert response.status_code == 422

    def test_last_admin_cannot_be_demoted_or_disabled(self, authed_client):
        users = authed_client.get("/api/v1/users").json()
        admin_id = next(u["id"] for u in users if u["role"] == "admin")

        assert authed_client.patch(
            f"/api/v1/users/{admin_id}", json={"role": "viewer"}
        ).status_code == 409
        assert authed_client.patch(
            f"/api/v1/users/{admin_id}", json={"active": False}
        ).status_code == 409

    def test_demotion_is_allowed_once_another_admin_exists(self, authed_client):
        authed_client.post(
            "/api/v1/users",
            json={"username": "admin2", "password": "a-long-test-password", "role": "admin"},
        )
        users = authed_client.get("/api/v1/users").json()
        first = next(u["id"] for u in users if u["username"] == "admin")
        assert authed_client.patch(f"/api/v1/users/{first}", json={"role": "viewer"}).status_code == 200

    def test_changing_a_role_ends_that_users_session(self, authed_client, pdf_factory):
        """Revoking access has to take effect now, not at session expiry."""
        job_id = held_job(authed_client, pdf_factory)
        authed_client.post(
            "/api/v1/users",
            json={"username": "ana", "password": "a-long-test-password", "role": "analyst"},
        )
        users = authed_client.get("/api/v1/users").json()
        ana_id = next(u["id"] for u in users if u["username"] == "ana")

        # Sign in as ana in a second client so the admin session survives.
        from fastapi.testclient import TestClient

        from janusprint.api.app import create_app

        with TestClient(create_app()) as ana_client:
            ana_client.post(
                "/login", data={"username": "ana", "password": "a-long-test-password", "next": "/"}
            )
            assert ana_client.get("/api/v1/jobs").status_code == 200

            authed_client.patch(f"/api/v1/users/{ana_id}", json={"role": "viewer"})

            # Old cookie must no longer work.
            assert ana_client.get("/api/v1/jobs").status_code == 401
            assert ana_client.post(
                f"/api/v1/jobs/{job_id}/release", json={"reason": "still here?"}
            ).status_code == 401

    def test_cannot_delete_your_own_account(self, authed_client):
        users = authed_client.get("/api/v1/users").json()
        me = next(u["id"] for u in users if u["username"] == "admin")
        assert authed_client.delete(f"/api/v1/users/{me}").status_code == 409

    def test_change_own_password_requires_the_current_one(self, authed_client):
        assert authed_client.post(
            "/api/v1/users/me/password",
            json={"current_password": "wrong", "new_password": "another-long-password"},
        ).status_code == 403
        assert authed_client.post(
            "/api/v1/users/me/password",
            json={"current_password": "test-password", "new_password": "another-long-password"},
        ).status_code == 200

    def test_whoami_reports_permissions(self, authed_client):
        body = authed_client.get("/api/v1/users/me").json()
        assert body["role"] == "admin"
        assert body["permissions"]["manage_users"] is True

        make_user("val", "viewer")
        login(authed_client, "val")
        body = authed_client.get("/api/v1/users/me").json()
        assert body["permissions"]["decide_jobs"] is False


class TestPreview:
    def test_held_job_is_viewable_for_triage(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)

        info = authed_client.get(f"/api/v1/jobs/{job_id}/preview").json()
        assert info["allowed"] is True
        assert info["pages"] == 1

        page = authed_client.get(f"/api/v1/jobs/{job_id}/preview/1")
        assert page.status_code == 200
        assert page.headers["content-type"] == "image/png"
        assert page.content.startswith(b"\x89PNG")
        # Must not be cacheable — a grant can expire or a role be revoked.
        assert "no-store" in page.headers["cache-control"]

    def test_preview_is_audited_per_page(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        authed_client.get(f"/api/v1/jobs/{job_id}/preview/1")

        log = authed_client.get("/api/v1/archive-access").json()
        entries = [r for r in log if r["job_id"] == job_id and r["kind"] == "preview"]
        assert entries and "page 1" in entries[0]["detail"]

    def test_decided_job_needs_a_grant(self, authed_client, pdf_factory):
        """Triage access ends when the decision does."""
        job_id = held_job(authed_client, pdf_factory)
        authed_client.post(f"/api/v1/jobs/{job_id}/release", json={"reason": "cleared"})

        assert authed_client.get(f"/api/v1/jobs/{job_id}/preview/1").status_code == 403
        assert authed_client.get(f"/api/v1/jobs/{job_id}/preview").json()["allowed"] is False

    def test_preview_never_serves_the_original_file(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        page = authed_client.get(f"/api/v1/jobs/{job_id}/preview/1")
        # An image, not the PDF — nothing forwardable leaves the server.
        assert not page.content.startswith(b"%PDF")

    def test_purged_job_cannot_be_previewed(self, authed_client, pdf_factory):
        from datetime import UTC, datetime, timedelta

        from janusprint.archive.retention import purge_expired

        job_id = held_job(authed_client, pdf_factory)
        with session_scope() as session:
            session.get(Job, job_id).purge_after = datetime.now(UTC) - timedelta(days=1)
        purge_expired()

        assert authed_client.get(f"/api/v1/jobs/{job_id}/preview/1").status_code == 410

    def test_out_of_range_page_is_rejected(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        assert authed_client.get(f"/api/v1/jobs/{job_id}/preview/99").status_code == 422

    def test_viewer_role_may_preview_but_not_decide(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        make_user("val", "viewer")
        login(authed_client, "val")

        assert authed_client.get(f"/api/v1/jobs/{job_id}/preview/1").status_code == 200
        assert authed_client.post(
            f"/api/v1/jobs/{job_id}/release", json={"reason": "nope"}
        ).status_code == 403

    def test_console_viewer_page_renders(self, authed_client, pdf_factory):
        job_id = held_job(authed_client, pdf_factory)
        assert authed_client.get(f"/jobs/{job_id}/view").status_code == 200

    def test_users_page_is_admin_only(self, authed_client):
        assert authed_client.get("/users").status_code == 200
        make_user("ana", "analyst")
        login(authed_client, "ana")
        page = authed_client.get("/users")
        assert "admin only" in page.text
