"""Rule definition, loading, and evaluation.

Rules are versioned YAML, never code — an operator has to be able to add a rule without a
deploy. Each rule carries its own fixtures so `janus-print-rules test` (and CI) can prove a
rule change did not regress the corpus. See PLAN.md §5.

Scoring, concretely:

    score = base_confidence
          + validator_weight   if a validator is configured and passes
          + context.boost      if any context term appears within context.window chars

A configured validator that *fails* discards the match outright — it is a structural
check, not a hint. Matches scoring below `threshold` are dropped; the rule fires when at
least `min_count` matches survive.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from ..config import ACTION_RANK, Action, get_settings
from .regex_engine import UnsupportedPattern, compile_pattern
from .validators import resolve


class ContextSpec(BaseModel):
    terms: list[str] = Field(default_factory=list)
    window: int = 50
    boost: float = 0.3
    # When true the rule only fires if a context term is nearby, regardless of score.
    required: bool = False


class RuleFixtures(BaseModel):
    """Text that must match, and text that must not. Both are mandatory in practice —
    a rule with no negatives is a rule nobody has pressure-tested."""

    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    id: str
    name: str
    pattern: str
    action: Action = "log"
    severity: int = 5
    validator: str | None = None
    validator_weight: float = 0.3
    base_confidence: float = 0.6
    threshold: float = 0.75
    min_count: int = 1
    ignore_case: bool = True
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    description: str = ""
    context: ContextSpec = Field(default_factory=ContextSpec)
    fixtures: RuleFixtures = Field(default_factory=RuleFixtures)
    # How much of the match to keep in the alert. The rest is masked.
    sample_prefix: int = 4
    sample_suffix: int = 4

    @field_validator("pattern")
    @classmethod
    def _compilable(cls, value: str) -> str:
        # Compiled with the real engine, so an unusable pattern is rejected when the rule
        # is saved rather than discovered on the print path.
        try:
            compile_pattern(value)
        except UnsupportedPattern as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("severity")
    @classmethod
    def _severity_range(cls, value: int) -> int:
        if not 0 <= value <= 10:
            raise ValueError("severity must be 0-10 (CEF scale)")
        return value

    def compiled(self):
        return compile_pattern(self.pattern, self.ignore_case)

    def mask(self, value: str) -> str:
        """Redact a matched value for storage and alerting. Raw values never leave the
        archive — see PLAN.md §7."""
        stripped = value.strip()
        keep_front, keep_back = self.sample_prefix, self.sample_suffix
        if len(stripped) <= keep_front + keep_back:
            return "*" * len(stripped)
        middle = "*" * (len(stripped) - keep_front - keep_back)
        return f"{stripped[:keep_front]}{middle}{stripped[len(stripped) - keep_back:]}"


@dataclass
class RuleHit:
    rule: Rule
    count: int
    score: float
    sample: str
    page: int
    tier: str = "text"
    contexts: list[str] = field(default_factory=list)


class RuleSet:
    def __init__(self, rules: list[Rule]) -> None:
        self.rules = [r for r in rules if r.enabled]

        seen: set[str] = set()
        duplicates: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                duplicates.add(rule.id)
            seen.add(rule.id)
        if duplicates:
            raise ValueError(f"duplicate rule ids: {sorted(duplicates)}")

        self._compiled = {r.id: r.compiled() for r in self.rules}

    def __len__(self) -> int:
        return len(self.rules)

    def select(self, tags: list[str]) -> RuleSet:
        """Filter by tag. `["*"]` keeps everything."""
        if "*" in tags:
            return self
        wanted = set(tags)
        return RuleSet([r for r in self.rules if wanted & set(r.tags)])

    def evaluate_page(self, text: str, page: int, tier: str = "text") -> list[RuleHit]:
        hits: list[RuleHit] = []
        for rule in self.rules:
            hit = self._evaluate_rule(rule, text, page, tier)
            if hit is not None:
                hits.append(hit)
        return hits

    def _evaluate_rule(self, rule: Rule, text: str, page: int, tier: str) -> RuleHit | None:
        check = resolve(rule.validator)
        pattern = self._compiled[rule.id]
        qualifying = 0
        best_score = 0.0
        best_sample = ""
        contexts: list[str] = []

        for found in pattern.finditer(text):
            raw = found.group(0)
            if rule.validator and not check(raw):
                continue  # structural check failed — not the thing we are looking for

            score = rule.base_confidence
            if rule.validator:
                score += rule.validator_weight

            nearby = self._context_terms(rule, text, found.start(), found.end())
            if nearby:
                score += rule.context.boost
                contexts.extend(nearby)
            elif rule.context.required:
                continue

            score = min(score, 1.0)
            if score < rule.threshold:
                continue

            qualifying += 1
            if score > best_score:
                best_score, best_sample = score, rule.mask(raw)

        if qualifying < rule.min_count:
            return None
        return RuleHit(
            rule=rule,
            count=qualifying,
            score=best_score,
            sample=best_sample,
            page=page,
            tier=tier,
            contexts=sorted(set(contexts))[:5],
        )

    @staticmethod
    def _context_terms(rule: Rule, text: str, start: int, end: int) -> list[str]:
        if not rule.context.terms:
            return []
        window = rule.context.window
        haystack = text[max(0, start - window) : end + window].lower()
        return [term for term in rule.context.terms if term.lower() in haystack]


def merge_action(hits: list[RuleHit]) -> tuple[Action, float, str]:
    """Most restrictive action wins. Returns (action, top score, human reason)."""
    if not hits:
        return "allow", 0.0, "no rule matched"

    action: Action = "allow"
    for hit in hits:
        if ACTION_RANK[hit.rule.action] > ACTION_RANK[action]:
            action = hit.rule.action

    deciding = [h for h in hits if h.rule.action == action]
    top = max(deciding, key=lambda h: (h.rule.severity, h.score))
    others = len(hits) - 1
    reason = f"{top.rule.name} ({top.count}x, score {top.score:.2f})"
    if others:
        reason += f" and {others} other rule{'s' if others > 1 else ''}"
    return action, max(h.score for h in hits), reason


def load_rules(directory: Path | None = None) -> RuleSet:
    """Load every *.yaml in the rules directory. A malformed file fails loudly — a
    silently skipped rule file is a silently disabled control."""
    directory = Path(directory or get_settings().rules_dir)
    if not directory.exists():
        raise FileNotFoundError(f"rules directory not found: {directory}")

    rules: list[Rule] = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        if raw is None:
            continue
        entries: list[dict[str, Any]] = raw["rules"] if isinstance(raw, dict) else raw
        for entry in entries:
            try:
                rule = Rule(**entry)
            except Exception as exc:
                raise ValueError(f"{path.name}: rule {entry.get('id', '?')!r}: {exc}") from exc
            resolve(rule.validator)  # fail at load time, not at match time
            rules.append(rule)
    return RuleSet(rules)


# --- rule test harness -------------------------------------------------------


@dataclass
class FixtureFailure:
    rule_id: str
    kind: str  # "false-negative" | "false-positive"
    text: str


def test_fixtures(ruleset: RuleSet) -> list[FixtureFailure]:
    """Run every rule against its own fixtures. Used by pytest and the CLI."""
    failures: list[FixtureFailure] = []
    for rule in ruleset.rules:
        single = RuleSet([rule])
        for text in rule.fixtures.positive:
            if not single.evaluate_page(text, page=1):
                failures.append(FixtureFailure(rule.id, "false-negative", text))
        for text in rule.fixtures.negative:
            if single.evaluate_page(text, page=1):
                failures.append(FixtureFailure(rule.id, "false-positive", text))
    return failures


def main() -> int:
    """`janus-print-rules [test|list]` — also the CI gate."""
    command = sys.argv[1] if len(sys.argv) > 1 else "test"
    ruleset = load_rules()

    if command == "list":
        for rule in ruleset.rules:
            tags = ",".join(rule.tags) or "-"
            print(f"{rule.id:<24} sev={rule.severity:<2} {rule.action:<6} [{tags}] {rule.name}")
        return 0

    failures = test_fixtures(ruleset)
    for failure in failures:
        snippet = failure.text if len(failure.text) < 70 else failure.text[:67] + "..."
        print(f"FAIL {failure.rule_id}: {failure.kind}: {snippet!r}", file=sys.stderr)
    total_fixtures = sum(
        len(r.fixtures.positive) + len(r.fixtures.negative) for r in ruleset.rules
    )
    print(f"{len(ruleset)} rules, {total_fixtures} fixtures, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
