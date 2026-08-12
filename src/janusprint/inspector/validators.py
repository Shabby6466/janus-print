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


VALIDATORS: dict[str, Callable[[str], bool]] = {
    "none": always,
    "always": always,
    "luhn": luhn,
    "iban": iban,
    "us_ssn": us_ssn,
    "nhs_number": nhs_number,
    "mod11": bban_mod11,
    "entropy": entropy,
}


class UnknownValidator(ValueError):
    pass


def resolve(name: str | None) -> Callable[[str], bool]:
    if not name:
        return always
    try:
        return VALIDATORS[name]
    except KeyError:
        raise UnknownValidator(
            f"unknown validator {name!r}; available: {', '.join(sorted(VALIDATORS))}"
        ) from None
