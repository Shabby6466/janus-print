"""Checksum validators.

The single highest-leverage component in the whole detector. A bare 16-digit regex fires
on invoice numbers, part numbers, and order references — enough noise to make the SOC
stop reading the alerts within a week. A Luhn check removes ~90% of that.

Every validator takes the raw matched string and returns True if it is plausibly the real
thing. Unknown validator names raise at rule-load time, not at match time.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_DIGITS = re.compile(r"\d")
_ALNUM = re.compile(r"[^A-Za-z0-9]")


def _digits(value: str) -> str:
    return "".join(_DIGITS.findall(value))


def luhn(value: str) -> bool:
    """Mod-10 check used by payment cards and some national IDs."""
    digits = _digits(value)
    if len(digits) < 12:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def iban(value: str) -> bool:
    """ISO 13616 mod-97."""
    cleaned = _ALNUM.sub("", value).upper()
    if not 15 <= len(cleaned) <= 34:
        return False
    rearranged = cleaned[4:] + cleaned[:4]
    numeric = "".join(str(int(c, 36)) if c.isalpha() else c for c in rearranged)
    if not numeric.isdigit():
        return False
    return int(numeric) % 97 == 1


def us_ssn(value: str) -> bool:
    """Structural validity — SSNs have no checksum, but many shapes are never issued."""
    digits = _digits(value)
    if len(digits) != 9:
        return False
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area in {"000", "666"} or area.startswith("9"):
        return False
    if group == "00" or serial == "0000":
        return False
    # 123-45-6789 and friends are almost always documentation examples.
    if digits in {"123456789", "111111111", "000000000"}:
        return False
    return True


def nhs_number(value: str) -> bool:
    """UK NHS number, mod-11 with weights 10..2."""
    digits = _digits(value)
    if len(digits) != 10:
        return False
    total = sum(int(d) * (10 - i) for i, d in enumerate(digits[:9]))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    if check == 10:
        return False
    return check == int(digits[9])


def bban_mod11(value: str) -> bool:
    """Generic mod-11 over all digits — used by several national ID schemes."""
    digits = _digits(value)
    if len(digits) < 8:
        return False
    total = sum(int(d) * (i % 6 + 2) for i, d in enumerate(reversed(digits[:-1])))
    check = 11 - (total % 11)
    check = 0 if check >= 10 else check
    return check == int(digits[-1])


def entropy(value: str, threshold: float = 3.0) -> bool:
    """Shannon entropy gate — for secrets/API keys where structure is the only signal."""
    import math
    from collections import Counter

    cleaned = value.strip()
    if len(cleaned) < 16:
        return False
    counts = Counter(cleaned)
    total = len(cleaned)
    bits = -sum((n / total) * math.log2(n / total) for n in counts.values())
    return bits >= threshold


def always(value: str) -> bool:  # noqa: ARG001 - uniform signature
    """No structural check. Use for keyword-style rules only."""
    return True


BUILTINS: dict[str, Callable[[str], bool]] = {
    "none": always,
    "always": always,
    "luhn": luhn,
    "iban": iban,
    "us_ssn": us_ssn,
    "nhs_number": nhs_number,
    "mod11": bban_mod11,
    "entropy": entropy,
}

# Kept as VALIDATORS for anything importing the old name directly.
VALIDATORS = BUILTINS


class UnknownValidator(ValueError):
    pass


class InvalidValidatorParams(ValueError):
    pass


# --- the generic engine custom validators are built from ---------------------
#
# A validator runs against every document printed in the building, so the console can
# never hand it arbitrary code. Instead it hands it one of these two declarative shapes,
# which between them cover the realistic long tail: most national ID and account-number
# schemes ARE a weighted-sum-mod-N checksum (this is literally how nhs_number and mod11
# above work internally — weighted_mod is that same logic, made data-driven), and secrets
# detection is an entropy threshold either way.


def weighted_mod_check(value: str, params: dict) -> bool:
    """A weighted-sum-mod-N checksum: the shape behind Luhn, NHS numbers, ISBN-10, and
    most national ID / account-number check digits.

        digits = digits_only(value)
        body, check = digits[:-check_digits], digits[-check_digits:]
        total = sum(weight[i] * int(body[i]) for i in range(len(body)))
        expected = (modulus - (total % modulus)) % modulus
        valid iff expected == int(check), unless expected is in reject_remainders

    `weights` cycles if shorter than the body — a 3-element list on a 12-digit body
    repeats 4 times, which covers both fixed-length national IDs and variable-length
    account numbers with a repeating weight pattern.
    """
    digits = _digits(value)
    length = params.get("length")
    if length and len(digits) != length:
        return False

    check_digits = int(params.get("check_digits", 1))
    if len(digits) <= check_digits:
        return False

    body, check = digits[:-check_digits] if check_digits else digits, digits[-check_digits:] if check_digits else ""
    weights = params.get("weights") or [2, 1]
    if not weights or any(not isinstance(w, int) for w in weights):
        raise InvalidValidatorParams("weights must be a non-empty list of integers")

    total = 0
    for index, char in enumerate(body):
        weight = weights[index % len(weights)]
        digit = int(char)
        if params.get("double_and_sum"):  # Luhn-style: doubled digits over 9 lose 9
            digit *= weight
            if digit > 9:
                digit -= 9
        else:
            digit *= weight
        total += digit

    modulus = int(params.get("modulus", 10))
    reject_remainders = set(params.get("reject_remainders", []))
    remainder = total % modulus
    if remainder in reject_remainders:
        return False

    expected = (modulus - remainder) % modulus if params.get("complement", True) else remainder
    try:
        return expected == int(check)
    except ValueError:
        return False


def entropy_check(value: str, params: dict) -> bool:
    """Shannon entropy gate with a configurable threshold and minimum length."""
    import math
    from collections import Counter

    cleaned = value.strip()
    min_length = int(params.get("min_length", 16))
    if len(cleaned) < min_length:
        return False
    min_bits = float(params.get("min_bits", 3.0))
    counts = Counter(cleaned)
    total = len(cleaned)
    bits = -sum((n / total) * math.log2(n / total) for n in counts.values())
    return bits >= min_bits


GENERIC_KINDS: dict[str, Callable[[str, dict], bool]] = {
    "weighted_mod": weighted_mod_check,
    "entropy": entropy_check,
}

# Shared by the API and the console so the "add validator" form's documentation and the
# engine's accepted kinds can never drift apart.
KIND_DOCS: dict[str, dict] = {
    "weighted_mod": {
        "description": "Weighted-sum-mod-N checksum — the shape behind Luhn, NHS numbers, "
        "ISBN-10, and most national ID or account-number check digits.",
        "params": {
            "weights": "list of integers, cycled across the digits (required)",
            "modulus": "default 10",
            "check_digits": "how many trailing digits are the check value (default 1)",
            "complement": "expected = (modulus - remainder) % modulus if true, else "
            "expected = remainder (default true)",
            "double_and_sum": "Luhn-style: multiply then fold digits over 9 (default false)",
            "length": "optional exact digit-count requirement",
            "reject_remainders": "optional list of remainders that are always invalid",
        },
    },
    "entropy": {
        "description": "Shannon entropy threshold, for secrets and API keys where "
        "randomness itself is the signal.",
        "params": {"min_length": "default 16", "min_bits": "default 3.0"},
    },
}


def validate_params(kind: str, params: dict) -> None:
    """Prove the params are usable before anything is saved — the same role fixtures
    play for rules, one level down."""
    if kind not in GENERIC_KINDS:
        raise InvalidValidatorParams(f"kind must be one of: {', '.join(GENERIC_KINDS)}")
    try:
        GENERIC_KINDS[kind]("0" * 20, params)
    except InvalidValidatorParams:
        raise
    except Exception as exc:
        raise InvalidValidatorParams(f"invalid params for {kind}: {exc}") from exc


def make_checker(kind: str, params: dict) -> Callable[[str], bool]:
    """Bind params into a plain `str -> bool` callable so it slots into `resolve()`
    exactly like a builtin."""
    fn = GENERIC_KINDS[kind]
    frozen = dict(params)

    def checker(value: str) -> bool:
        try:
            return fn(value, frozen)
        except Exception:  # noqa: BLE001 - a bad match must not break inspection
            return False

    return checker


# --- registry ------------------------------------------------------------------
#
# resolve() is called once per rule per page, on the print path — it has to stay a
# synchronous dict lookup, not a DB query. Custom validators are therefore compiled once
# into this module-level cache and refreshed only when something actually changes,
# mirroring how the rule cache in inspector/store.py works.

_custom: dict[str, Callable[[str], bool]] = {}


def set_custom_registry(checkers: dict[str, Callable[[str], bool]]) -> None:
    global _custom
    _custom = dict(checkers)


def resolve(name: str | None) -> Callable[[str], bool]:
    if not name:
        return always
    if name in BUILTINS:
        return BUILTINS[name]
    if name in _custom:
        return _custom[name]
    available = sorted(set(BUILTINS) | set(_custom))
    raise UnknownValidator(f"unknown validator {name!r}; available: {', '.join(available)}")


def known_names() -> list[str]:
    return sorted(set(BUILTINS) | set(_custom))
