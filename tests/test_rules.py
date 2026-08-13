"""The rule corpus gate.

test_shipped_rule_fixtures is the one that matters: it is the CI check that stops a rule
edit from quietly regressing detection or reopening a false-positive class.
"""

from __future__ import annotations

import pytest

from janusprint.inspector.rules import Rule, RuleSet, load_rules, merge_action
from janusprint.inspector.rules import test_fixtures as run_fixture_checks
from janusprint.inspector.validators import UnknownValidator, iban, luhn, nhs_number, resolve, us_ssn


def test_shipped_rule_fixtures():
    ruleset = load_rules()
    failures = run_fixture_checks(ruleset)
    assert not failures, "\n".join(
        f"{f.rule_id}: {f.kind}: {f.text[:100]}" for f in failures
    )


def test_rules_load_and_are_unique():
    ruleset = load_rules()
    assert len(ruleset) >= 10
    ids = [r.id for r in ruleset.rules]
    assert len(ids) == len(set(ids))


def test_duplicate_rule_ids_rejected():
    rule = Rule(id="dup", name="Dup", pattern="a")
    with pytest.raises(ValueError, match="duplicate"):
        RuleSet([rule, rule.model_copy()])


def test_unknown_validator_fails_at_load_time():
    with pytest.raises(UnknownValidator):
        resolve("no-such-validator")


class TestValidators:
    def test_luhn_accepts_real_cards(self):
        assert luhn("4111111111111111")
        assert luhn("5555 5555 5555 4444")

    def test_luhn_rejects_invoice_shaped_numbers(self):
        # The whole reason the validator exists.
        assert not luhn("4111111111111112")
        assert not luhn("1234567890123456")

    def test_iban_mod97(self):
        assert iban("GB82WEST12345698765432")
        assert not iban("GB99WEST12345698765432")

    def test_ssn_rejects_never_issued_ranges(self):
        assert us_ssn("536-90-4399")
        assert not us_ssn("666-12-3456")
        assert not us_ssn("900-12-3456")
        assert not us_ssn("123-45-6789")  # documentation placeholder

    def test_nhs_mod11(self):
        assert nhs_number("943 476 5919")
        assert not nhs_number("943 476 5910")


class TestScoring:
    def _rule(self, **overrides) -> Rule:
        base = dict(
            id="test-pan",
            name="Test PAN",
            pattern=r"\b\d{16}\b",
            validator="luhn",
            action="hold",
            threshold=0.75,
        )
        return Rule(**(base | overrides))

    def test_validator_failure_discards_the_match(self):
        ruleset = RuleSet([self._rule()])
        assert not ruleset.evaluate_page("Invoice 1234567890123456", 1)

    def test_context_boost_raises_a_borderline_match(self):
        # base 0.6 alone is under the 0.75 threshold; the context term carries it over.
        rule = self._rule(
            validator=None,
            base_confidence=0.6,
            context={"terms": ["card"], "boost": 0.3},
        )
        ruleset = RuleSet([rule])
        assert not ruleset.evaluate_page("Reference 4111111111111111", 1)
        assert ruleset.evaluate_page("Card 4111111111111111", 1)

    def test_required_context_gates_the_rule(self):
        rule = self._rule(context={"terms": ["passport"], "required": True})
        ruleset = RuleSet([rule])
        assert not ruleset.evaluate_page("Number 4111111111111111", 1)
        assert ruleset.evaluate_page("Passport 4111111111111111", 1)

    def test_min_count_needs_repeats(self):
        rule = self._rule(min_count=3)
        ruleset = RuleSet([rule])
        one = "Card 4111111111111111"
        assert not ruleset.evaluate_page(one, 1)
        assert ruleset.evaluate_page(" ".join([one] * 3), 1)

    def test_sample_is_masked(self):
        ruleset = RuleSet([self._rule()])
        hit = ruleset.evaluate_page("Card 4111111111111111", 1)[0]
        assert hit.sample == "4111********1111"
        assert "4111111111111111" not in hit.sample

    def test_tag_selection(self):
        ruleset = RuleSet(
            [self._rule(id="a", tags=["pci"]), self._rule(id="b", tags=["hr"])]
        )
        assert len(ruleset.select(["pci"])) == 1
        assert len(ruleset.select(["*"])) == 2


class TestActionMerge:
    def test_most_restrictive_wins(self):
        ruleset = RuleSet(
            [
                Rule(id="a", name="A", pattern="alpha", action="log", base_confidence=0.9),
                Rule(id="b", name="B", pattern="beta", action="block", base_confidence=0.9),
                Rule(id="c", name="C", pattern="gamma", action="hold", base_confidence=0.9),
            ]
        )
        hits = ruleset.evaluate_page("alpha beta gamma", 1)
        action, _score, reason = merge_action(hits)
        assert action == "block"
        assert "B" in reason

    def test_no_hits_allows(self):
        assert merge_action([])[0] == "allow"


def test_real_pan_in_a_realistic_page_is_held():
    ruleset = load_rules()
    page = (
        "Payment reconciliation - October\n"
        "Cardholder: J Smith\n"
        "Card 4111 1111 1111 1111  exp 04/28  cvv 123\n"
        "Amount settled: 1,204.55\n"
    )
    action, score, _ = merge_action(ruleset.evaluate_page(page, 1))
    assert action == "hold"
    assert score >= 0.75


def test_ordinary_office_document_is_clean():
    """The false-positive canary. If this ever fails, the SOC is about to be flooded."""
    ruleset = load_rules()
    page = (
        "Quarterly facilities report\n"
        "Invoice 4111111111111112 was raised on 3 March for order 1234567890123456.\n"
        "Meeting room utilisation is up 12% on last quarter, ref 9999999999999999.\n"
        "Contact alice@example.com or bob@example.com with questions.\n"
        "Serial 1234 5678 9012 3456 on the chassis plate. Phone 020 7946 0958.\n"
        "Please treat the contents as confidential where possible.\n"
        "Your salary review is scheduled for April.\n"
    )
    hits = ruleset.evaluate_page(page, 1)
    assert not hits, [h.rule.id for h in hits]


class TestTextPlausibility:
    """Guarding against a text layer that exists but is not language.

    This is the failure that let a confidential document print: macOS embedded a subset
    font with no Unicode mapping, extraction produced one symbol per glyph, the page
    counted as having text, OCR never ran, and no rule could match.
    """

    def test_real_glyph_soup_is_rejected(self):
        from janusprint.inspector.extract import looks_like_language

        # Captured from an actual macOS Word print job.
        soup = "!\"#$%&'(#)*$+'+,+-.*!+--/#\"$0*1%)'.\"23&#\"\".&'31#\"-.\r\n-'\"(&'45*&#)6$.)'(+4"
        assert looks_like_language(soup) is False

    def test_ordinary_prose_is_accepted(self):
        from janusprint.inspector.extract import looks_like_language

        assert looks_like_language("production database password: hunter2-correct-horse")
        assert looks_like_language("STRICTLY CONFIDENTIAL")
        assert looks_like_language("Quarterly facilities report for the October meeting")

    def test_mostly_numeric_pages_still_pass(self):
        """An invoice is legitimate text even though most characters are digits."""
        from janusprint.inspector.extract import looks_like_language

        assert looks_like_language("Invoice 4111 total 1,204.55 due 30 days net amount")

    def test_empty_is_not_language(self):
        from janusprint.inspector.extract import looks_like_language

        assert looks_like_language("") is False
        assert looks_like_language("   \n  ") is False

    def test_glyph_soup_page_is_routed_to_ocr(self):
        from janusprint.inspector.extract import ExtractionResult

        soup = "!\"#$%&'(#)*$+'+,+-.*!+--/#\"$0*1%)'.\"23&#\"\".&'31#\"-." * 3
        result = ExtractionResult(pages=[soup], page_count=1, format="pdf")
        # Plenty of characters, so the old length-only check passed it through.
        assert len(soup) > 20
        assert result.pages_without_text(20) == [1]
        assert result.unreadable_text_pages(20) == [1]

    def test_genuine_page_is_not_routed_to_ocr(self):
        from janusprint.inspector.extract import ExtractionResult

        result = ExtractionResult(
            pages=["Quarterly facilities report. Room utilisation is up twelve percent."],
            page_count=1,
            format="pdf",
        )
        assert result.pages_without_text(20) == []
