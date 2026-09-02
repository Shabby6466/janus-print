"""Console-managed validators.

The property under test everywhere here: a validator is code that runs against every
document printed in the building, so nothing the console accepts may become arbitrary
logic — only the two safe declarative shapes (weighted_mod, entropy) — and nothing may
save without proving itself against real pass/fail examples first, the same way rule
fixtures work.
"""

from __future__ import annotations

import pytest

from janusprint import validator_store
from janusprint.inspector import validators as validator_engine
from janusprint.inspector.rules import Rule, RuleSet
from janusprint.models import ValidatorRow


def weighted_mod_payload(**overrides) -> dict:
    """The generic engine reimplementing nhs_number's own algorithm — mod-11, weights
    10..2, one check digit — as proof the DSL genuinely covers a real shipped scheme."""
    base = {
        "id": "nhs-clone",
        "name": "NHS number clone",
        "description": "reimplements nhs_number via the generic engine",
        "kind": "weighted_mod",
        "params": {
            "weights": [10, 9, 8, 7, 6, 5, 4, 3, 2],
            "modulus": 11,
            "check_digits": 1,
            "complement": True,
            "length": 10,
            "reject_remainders": [1],  # check digit 10 is never issued
        },
        "fixtures": {"pass": ["9434765919"], "fail": ["9434765910"]},
    }
    return base | overrides


class TestGenericEngine:
    """The pure functions, independent of the database."""

    def test_weighted_mod_reproduces_nhs_number(self):
        # Same digits, same algorithm as inspector.validators.nhs_number — proves the
        # generic DSL is not a toy, it is the real shape.
        params = weighted_mod_payload()["params"]
        assert validator_engine.weighted_mod_check("943 476 5919", params)
        assert not validator_engine.weighted_mod_check("943 476 5910", params)

    def test_weighted_mod_luhn_style(self):
        params = {
            "weights": [2, 1],
            "modulus": 10,
            "check_digits": 1,
            "complement": True,
            "double_and_sum": True,
        }
        assert validator_engine.weighted_mod_check("4111111111111111", params)
        assert not validator_engine.weighted_mod_check("4111111111111112", params)

    def test_weighted_mod_rejects_wrong_length(self):
        params = weighted_mod_payload()["params"]
        assert not validator_engine.weighted_mod_check("123", params)

    def test_entropy_check_configurable_threshold(self):
        low = {"min_length": 8, "min_bits": 1.0}
        high = {"min_length": 8, "min_bits": 4.5}
        random_ish = "aB3$kX9!qZ7@mN2#"
        assert validator_engine.entropy_check(random_ish, low)
        assert not validator_engine.entropy_check("aaaaaaaaaaaaaaaa", low)
        assert not validator_engine.entropy_check(random_ish, high)

    def test_validate_params_rejects_unknown_kind(self):
        with pytest.raises(validator_engine.InvalidValidatorParams):
            validator_engine.validate_params("exec_python", {})

    def test_validate_params_rejects_bad_weights(self):
        with pytest.raises(validator_engine.InvalidValidatorParams):
            validator_engine.validate_params("weighted_mod", {"weights": "not a list"})

    def test_make_checker_never_raises_on_garbage_input(self):
        """A malformed match must not crash inspection — same contract as builtins."""
        checker = validator_engine.make_checker("weighted_mod", weighted_mod_payload()["params"])
        assert checker("") is False
        assert checker("not digits at all !!!") is False


class TestBuiltinsAreProtected:
    def test_builtins_are_seeded_and_listed(self, session):
        rows = {r.id for r in session.query(ValidatorRow).all()}
        assert {"luhn", "iban", "us_ssn", "nhs_number", "mod11", "entropy", "none"} <= rows

    def test_builtin_still_resolves_directly(self):
        # Never routed through the DB/custom registry — always the tested Python.
        assert validator_engine.resolve("luhn") is validator_engine.luhn

    def test_cannot_update_a_builtin(self, session):
        with pytest.raises(validator_store.ValidatorError, match="protected"):
            validator_store.update(session, "luhn", {"description": "changed"}, actor="admin")

    def test_cannot_delete_a_builtin(self, session):
        with pytest.raises(validator_store.ValidatorError, match="protected"):
            validator_store.delete(session, "luhn", actor="admin")

    def test_id_collision_with_a_builtin_is_refused(self, session):
        with pytest.raises(validator_store.ValidatorError, match="already exists"):
            validator_store.create(session, weighted_mod_payload(id="luhn"), actor="admin")


class TestFixturesGate:
    """The safety mechanism: nothing saves without proving itself first."""

    def test_missing_fixtures_is_refused(self, session):
        payload = weighted_mod_payload(fixtures={"pass": [], "fail": []})
        with pytest.raises(validator_store.ValidatorError, match="PASS example"):
            validator_store.create(session, payload, actor="admin")

    def test_a_pass_example_that_actually_fails_is_refused(self, session):
        payload = weighted_mod_payload(fixtures={"pass": ["0000000001"], "fail": ["9434765910"]})
        with pytest.raises(validator_store.ValidatorError, match="should PASS"):
            validator_store.create(session, payload, actor="admin")

    def test_a_fail_example_that_actually_passes_is_refused(self, session):
        payload = weighted_mod_payload(fixtures={"pass": ["9434765919"], "fail": ["9434765919"]})
        with pytest.raises(validator_store.ValidatorError, match="should FAIL"):
            validator_store.create(session, payload, actor="admin")

    def test_valid_fixtures_save_successfully(self, session):
        row = validator_store.create(session, weighted_mod_payload(), actor="admin")
        assert row.id == "nhs-clone"
        assert row.builtin is False


class TestCrudAndRegistry:
    def test_created_validator_is_immediately_resolvable(self, session):
        validator_store.create(session, weighted_mod_payload(), actor="admin")
        checker = validator_engine.resolve("nhs-clone")
        assert checker("943 476 5919")
        assert not checker("943 476 5910")

    def test_unknown_id_raises_before_creation(self, session):
        with pytest.raises(validator_engine.UnknownValidator):
            validator_engine.resolve("does-not-exist")

    def test_update_that_breaks_fixtures_is_refused(self, session):
        validator_store.create(session, weighted_mod_payload(), actor="admin")
        # Loosen the modulus to something that no longer matches the same fixtures —
        # must be refused rather than silently accepted.
        with pytest.raises(validator_store.ValidatorError):
            validator_store.update(
                session, "nhs-clone", {"params": {**weighted_mod_payload()["params"], "modulus": 7}},
                actor="admin",
            )

    def test_disabling_removes_it_from_the_registry(self, session):
        validator_store.create(session, weighted_mod_payload(), actor="admin")
        validator_store.update(session, "nhs-clone", {"enabled": False}, actor="admin")
        with pytest.raises(validator_engine.UnknownValidator):
            validator_engine.resolve("nhs-clone")

    def test_delete_removes_it(self, session):
        validator_store.create(session, weighted_mod_payload(), actor="admin")
        validator_store.delete(session, "nhs-clone", actor="admin")
        assert session.get(ValidatorRow, "nhs-clone") is None
        with pytest.raises(validator_engine.UnknownValidator):
            validator_engine.resolve("nhs-clone")

    def test_cannot_delete_a_validator_in_use_by_an_enabled_rule(self, session):
        from janusprint.inspector import store as rule_store

        validator_store.create(session, weighted_mod_payload(), actor="admin")
        rule_store.create_rule(
            session,
            {
                "id": "uses-nhs-clone",
                "name": "test",
                "pattern": r"\d{10}",
                "validator": "nhs-clone",
                "base_confidence": 0.9,
                "fixtures": {"positive": ["9434765919"], "negative": ["0000000001"]},
            },
            actor="admin",
        )
        with pytest.raises(validator_store.ValidatorError, match="still used"):
            validator_store.delete(session, "nhs-clone", actor="admin")

    def test_every_change_is_audited(self, session):
        validator_store.create(session, weighted_mod_payload(), actor="alice", note="initial")
        validator_store.update(session, "nhs-clone", {"enabled": False}, actor="bob", note="too noisy")
        history = validator_store.revisions(session, "nhs-clone")
        assert [r.change for r in history] == ["updated", "created"]
        assert {r.actor for r in history} == {"alice", "bob"}

    def test_deletion_history_outlives_the_validator(self, session):
        validator_store.create(session, weighted_mod_payload(), actor="alice")
        validator_store.delete(session, "nhs-clone", actor="mallory", note="cleanup")
        history = validator_store.revisions(session, "nhs-clone")
        assert history[0].change == "deleted"
        assert history[0].actor == "mallory"


class TestRuleIntegration:
    """A custom validator has to actually change a job's verdict, not just exist."""

    def test_rule_using_a_custom_validator_takes_effect(self, session):
        validator_store.create(session, weighted_mod_payload(), actor="admin")
        rule = Rule(
            id="custom-check",
            name="Custom NHS-shaped id",
            pattern=r"\b\d{10}\b",
            validator="nhs-clone",
            base_confidence=0.9,
            threshold=0.75,
        )
        ruleset = RuleSet([rule])
        assert ruleset.evaluate_page("Record 9434765919 on file", 1)
        assert not ruleset.evaluate_page("Record 9434765910 on file", 1)

    def test_try_endpoint_logic_matches_saved_behaviour(self, session):
        params = weighted_mod_payload()["params"]
        assert validator_store.try_validator("weighted_mod", params, "9434765919")["passes"] is True
        assert validator_store.try_validator("weighted_mod", params, "9434765910")["passes"] is False


class TestApi:
    def test_create_via_api(self, authed_client):
        response = authed_client.post("/api/v1/validators", json=weighted_mod_payload() | {"note": "new"})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["id"] == "nhs-clone"
        assert body["builtin"] is False

    def test_bad_fixtures_return_422(self, authed_client):
        # "1234567890" fails the checksum (verified against weighted_mod_check directly),
        # so claiming it as a PASS example must be refused.
        payload = weighted_mod_payload(fixtures={"pass": ["1234567890"], "fail": ["9434765910"]})
        response = authed_client.post("/api/v1/validators", json=payload | {"note": "x"})
        assert response.status_code == 422

    def test_list_includes_builtins_and_custom(self, authed_client):
        authed_client.post("/api/v1/validators", json=weighted_mod_payload() | {"note": "x"})
        rows = authed_client.get("/api/v1/validators").json()
        ids = {r["id"] for r in rows}
        assert "luhn" in ids and "nhs-clone" in ids

    def test_kinds_endpoint_documents_params(self, authed_client):
        body = authed_client.get("/api/v1/validators/kinds").json()
        assert "weighted_mod" in body and "entropy" in body
        assert "weights" in body["weighted_mod"]["params"]

    def test_try_endpoint(self, authed_client):
        response = authed_client.post(
            "/api/v1/validators/try",
            json={"kind": "weighted_mod", "params": weighted_mod_payload()["params"], "sample": "9434765919"},
        )
        assert response.status_code == 200
        assert response.json()["passes"] is True

    def test_non_admin_cannot_create_or_delete(self, authed_client):
        from janusprint.api.auth import hash_password
        from janusprint.db import session_scope
        from janusprint.models import User

        with session_scope() as session:
            session.add(
                User(username="ana", password_hash=hash_password("a-long-test-password"), role="analyst")
            )
        authed_client.get("/logout")
        authed_client.post("/login", data={"username": "ana", "password": "a-long-test-password", "next": "/"})

        assert authed_client.post("/api/v1/validators", json=weighted_mod_payload()).status_code == 403
        assert authed_client.delete("/api/v1/validators/luhn").status_code == 403
        # Reading is fine.
        assert authed_client.get("/api/v1/validators").status_code == 200

    def test_patch_via_api_disables(self, authed_client):
        authed_client.post("/api/v1/validators", json=weighted_mod_payload() | {"note": "x"})
        response = authed_client.patch(
            "/api/v1/validators/nhs-clone", json={"enabled": False, "note": "pause"}
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_delete_via_api(self, authed_client):
        authed_client.post("/api/v1/validators", json=weighted_mod_payload() | {"note": "x"})
        response = authed_client.delete("/api/v1/validators/nhs-clone?note=cleanup")
        assert response.status_code == 200

    def test_builtin_delete_via_api_is_refused(self, authed_client):
        response = authed_client.delete("/api/v1/validators/luhn?note=x")
        assert response.status_code == 422

    def test_console_pages_render(self, authed_client):
        assert authed_client.get("/validators").status_code == 200
        assert authed_client.get("/validators/history").status_code == 200

    def test_rule_editor_offers_custom_validators(self, authed_client):
        authed_client.post("/api/v1/validators", json=weighted_mod_payload() | {"note": "x"})
        page = authed_client.get("/rules/new")
        assert "nhs-clone" in page.text
