"""Regex compilation for operator-authored rules.

Rules are written by operators through the console and then run against every printed
page. Python's `re` is a backtracking engine, so a pattern with nested quantifiers —
`(a+)+$` is the classic — takes exponential time on adversarial input:

    18 chars  0.017s
    20 chars  0.067s
    22 chars  0.248s
    24 chars  0.982s        (measured, CPython 3.12)

Thirty-odd characters is minutes. Worse, `finditer` runs in C and does not yield, so the
engine's own deadline check — which only runs *between* pattern calls — never fires. The
API thread locks, the HTTP request times out, and the CUPS backend falls open. A user
printing a page of "aaaaaa..." would take the inspector down.

So patterns are compiled with RE2, which guarantees linear time and cannot backtrack. The
cost is that RE2 has no backreferences and no lookaround; those are rejected when a rule is
saved, with an explanation. That is the right trade for this system: a rule that cannot be
expressed is a nuisance, a rule that halts inspection is an outage.

If RE2 is unavailable the module falls back to `re` and says so loudly, because the
difference matters and should never be silent.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


class UnsupportedPattern(ValueError):
    """The pattern cannot be compiled by the active engine."""


try:  # pragma: no cover - depends on the deployment image
    import re2 as _re2

    ENGINE = "re2"
except ImportError:  # pragma: no cover
    _re2 = None
    ENGINE = "re"
    log.error(
        "google-re2 is not installed; falling back to Python's backtracking re engine. "
        "A rule with nested quantifiers can then hang inspection on adversarial input. "
        "Install google-re2."
    )

LINEAR_TIME = ENGINE == "re2"

# Constructs RE2 cannot express. Detected up front so the error names the actual problem
# instead of surfacing a parser message operators cannot act on.
_UNSUPPORTED = (
    (re.compile(r"\(\?="), "lookahead (?=...)"),
    (re.compile(r"\(\?!"), "negative lookahead (?!...)"),
    (re.compile(r"\(\?<="), "lookbehind (?<=...)"),
    (re.compile(r"\(\?<!"), "negative lookbehind (?<!...)"),
    (re.compile(r"\\[1-9]"), "backreference (\\1)"),
)


def compile_pattern(pattern: str, ignore_case: bool = True):
    """Compile a rule pattern, or explain why it cannot be used."""
    if _re2 is None:  # pragma: no cover - fallback path
        return re.compile(pattern, re.IGNORECASE if ignore_case else 0)

    for probe, description in _UNSUPPORTED:
        if probe.search(pattern):
            raise UnsupportedPattern(
                f"{description} is not supported. The inspector uses RE2 so that no rule "
                f"can lock up on a crafted document; RE2 has no backtracking, and these "
                f"constructs require it. Rewrite the pattern, or use context terms to "
                f"express the surrounding condition."
            )

    try:
        options = _re2.Options()
        options.case_sensitive = not ignore_case
        return _re2.compile(pattern, options=options)
    except Exception as exc:
        raise UnsupportedPattern(f"invalid regular expression: {exc}") from exc


def describe() -> dict[str, object]:
    return {"engine": ENGINE, "linear_time": LINEAR_TIME}
