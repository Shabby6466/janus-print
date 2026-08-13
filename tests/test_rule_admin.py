"""Console-managed rules.

The safety property under test: nothing an admin can submit through the UI is allowed to
break the inspection path. A rule that will not compile, or that fails its own fixtures,
must be rejected before it is stored.
"""

from __future__ import annotations

import pytest

from janusprint.db import session_scope
from janusprint.inspector import store as rule_store
from janusprint.inspector.engine import JobMetadata, get_ruleset, inspect_job
from janusprint.models import RuleRow


def valid_rule(**overrides) -> dict:
    base = {
        "id": "test-badge",
        "name": "Badge number",
        "pattern": r"\bBADGE-\d{6}\b",
        "action": "hold",
        "severity": 6,
        "base_confidence": 0.9,
        "threshold": 0.75,
        "tags": ["corporate"],
        "fixtures": {
            "positive": ["Employee BADGE-123456 issued"],
            "negative": ["Order 123456 shipped"],
        },
    }
    return base | overrides


class TestSeeding:
    def test_yaml_packs_seed_the_table(self, session):
        assert session.query(RuleRow).count() >= 10

    def test_seeding_is_idempotent_and_never_reverts_edits(self, session):
        rule_store.update_rule(
            session, "pan-primary", {"severity": 3}, actor="admin", note="lowered"
        )
        session.commit()

        added = rule_store.seed_from_yaml(session)
        assert added == 0
        # A restart must not quietly restore the shipped value over an operator's decision.
        assert session.get(RuleRow, "pan-primary").severity == 3


class TestValidation:
    def test_uncompilable_pattern_is_rejected(self, session):
        with pytest.raises(rule_store.RuleValidationError):
            rule_store.create_rule(session, valid_rule(pattern="([unclosed"), actor="admin")

    def test_rule_failing_its_own_fixtures_is_rejected(self, session):
        payload = valid_rule(
            fixtures={"positive": ["this text contains no badge at all"], "negative": []}
        )
        with pytest.raises(rule_store.RuleValidationError) as caught:
            rule_store.create_rule(session, payload, actor="admin")
        assert caught.value.failures

    def test_rule_matching_its_own_negative_is_rejected(self, session):
        payload = valid_rule(
            fixtures={"positive": ["BADGE-123456"], "negative": ["BADGE-999999 is fine"]}
        )
        with pytest.raises(rule_store.RuleValidationError):
            rule_store.create_rule(session, payload, actor="admin")

    def test_unknown_validator_is_rejected(self, session):
        with pytest.raises(rule_store.RuleValidationError):
            rule_store.create_rule(session, valid_rule(validator="astrology"), actor="admin")

    def test_duplicate_id_is_rejected(self, session):
        rule_store.create_rule(session, valid_rule(), actor="admin")
        with pytest.raises(rule_store.RuleValidationError):
            rule_store.create_rule(session, valid_rule(), actor="admin")


class TestCrudTakesEffect:
    def _meta(self) -> JobMetadata:
        return JobMetadata(cups_job_id="1", queue="office-laser", username="jdoe")

    def test_new_rule_applies_to_the_next_job(self, session, pdf_factory):
        data = pdf_factory(["Access card BADGE-123456 for the contractor"])

        first = inspect_job(session, self._meta(), data)
        assert first.action == "allow"

        rule_store.create_rule(session, valid_rule(), actor="admin", note="new control")
        session.commit()

        second = inspect_job(
            session, JobMetadata(cups_job_id="2", queue="office-laser", username="jdoe"), data
        )
        assert second.action == "hold"
        assert "test-badge" in second.rule_ids

    def test_disabling_a_rule_stops_it_firing(self, session, pdf_factory):
        data = pdf_factory(["Card 4111 1111 1111 1111 cardholder"])
        assert inspect_job(session, self._meta(), data).action == "hold"

        for rule_id in ("pan-primary", "pan-spaced"):
            rule_store.set_enabled(session, rule_id, False, actor="admin", note="testing")
        session.commit()

        verdict = inspect_job(
            session, JobMetadata(cups_job_id="2", queue="office-laser", username="jdoe"), data
        )
        assert verdict.action == "allow"

    def test_deleted_rule_stops_firing(self, session, pdf_factory):
        rule_store.create_rule(session, valid_rule(), actor="admin")
        session.commit()
        data = pdf_factory(["Access card BADGE-123456"])
        assert inspect_job(session, self._meta(), data).action == "hold"

        rule_store.delete_rule(session, "test-badge", actor="admin", note="no longer needed")
        session.commit()

        verdict = inspect_job(
            session, JobMetadata(cups_job_id="2", queue="office-laser", username="jdoe"), data
        )
        assert verdict.action == "allow"


class TestAuditTrail:
    def test_every_change_is_recorded(self, session):
        rule_store.create_rule(session, valid_rule(), actor="alice", note="initial")
        rule_store.update_rule(
            session, "test-badge", {"severity": 9}, actor="bob", note="raised severity"
        )
        rule_store.set_enabled(session, "test-badge", False, actor="carol", note="too noisy")

        history = rule_store.revisions(session, "test-badge")
        assert [r.change for r in history] == ["disabled", "updated", "created"]
        assert {r.actor for r in history} == {"alice", "bob", "carol"}

    def test_deletion_history_outlives_the_rule(self, session):
        rule_store.create_rule(session, valid_rule(), actor="alice")
        rule_store.delete_rule(session, "test-badge", actor="mallory", note="cleanup")

        assert session.get(RuleRow, "test-badge") is None
        history = rule_store.revisions(session, "test-badge")
        # Who removed a detection must stay answerable after the rule is gone.
        assert history[0].change == "deleted"
        assert history[0].actor == "mallory"
        assert history[0].snapshot["pattern"]


class TestTryEndpoint:
    def test_preview_reports_matches_without_saving(self, session):
        result = rule_store.try_rule(valid_rule(), "Employee BADGE-123456 issued today")
        assert result["fires"] is True
        assert result["action"] == "hold"
        assert result["matches"][0]["sample"] == "BADG****3456"
        assert "123456" not in result["matches"][0]["sample"]
        assert session.get(RuleRow, "test-badge") is None

    def test_preview_reports_no_match(self, session):
        assert rule_store.try_rule(valid_rule(), "nothing sensitive here")["fires"] is False

    def test_preview_surfaces_fixture_failures_without_rejecting(self):
        payload = valid_rule(fixtures={"positive": ["no badge here"], "negative": []})
        result = rule_store.try_rule(payload, "Employee BADGE-123456")
        assert result["fires"] is True
        assert result["fixture_failures"]


class TestApi:
    def test_create_via_api_and_list(self, authed_client):
        response = authed_client.post("/api/v1/rules", json=valid_rule() | {"note": "new"})
        assert response.status_code == 201, response.text

        listed = authed_client.get("/api/v1/rules").json()
        assert any(rule["id"] == "test-badge" for rule in listed)

    def test_invalid_rule_returns_422_with_detail(self, authed_client):
        payload = valid_rule(fixtures={"positive": ["nothing matches this"], "negative": []})
        response = authed_client.post("/api/v1/rules", json=payload | {"note": "bad"})
        assert response.status_code == 422
        assert response.json()["detail"]["failures"]

    def test_non_admin_cannot_create_rules(self, authed_client):
        from janusprint.api.auth import hash_password
        from janusprint.models import User

        with session_scope() as session:
            session.add(
                User(username="ana", password_hash=hash_password("analyst-pw"), role="analyst")
            )
        authed_client.get("/logout")
        authed_client.post("/login", data={"username": "ana", "password": "analyst-pw", "next": "/"})

        response = authed_client.post("/api/v1/rules", json=valid_rule() | {"note": "x"})
        assert response.status_code == 403

    def test_try_endpoint(self, authed_client):
        response = authed_client.post(
            "/api/v1/rules/try",
            json={"rule": valid_rule(), "sample_text": "Employee BADGE-123456"},
        )
        assert response.status_code == 200
        assert response.json()["fires"] is True

    def test_console_pages_render(self, authed_client):
        assert authed_client.get("/rules").status_code == 200
        assert authed_client.get("/rules/new").status_code == 200
        assert authed_client.get("/rules/history").status_code == 200
        assert authed_client.get("/rules/pan-primary").status_code == 200

    def test_revisions_endpoint(self, authed_client):
        authed_client.post("/api/v1/rules", json=valid_rule() | {"note": "audit me"})
        history = authed_client.get("/api/v1/rule-revisions?rule_id=test-badge").json()
        assert history[0]["note"] == "audit me"
        assert history[0]["actor"] == "admin"
