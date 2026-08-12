"""Document fingerprinting via winnowed k-gram shingles.

Plain hashing catches only byte-identical files, which an insider defeats by changing one
character. Shingling survives rewording, reformatting, and partial excerpts.

Method (Schleimer et al., "Winnowing: Local Algorithms for Document Fingerprinting"):

    1. normalise  — lowercase, strip punctuation, collapse whitespace
    2. shingle    — every k consecutive words
    3. hash       — blake2b, folded to signed 64-bit for the DB
    4. winnow     — in each window of w consecutive hashes keep the minimum

Winnowing is what makes this robust: the selected set depends on content, not position, so
inserting a paragraph shifts nothing downstream.

Matching uses *containment*, measured both ways:

    printed doc is an excerpt of a registered doc -> |Q n D| / |Q| is high
    printed doc contains a registered doc         -> |Q n D| / |D| is high

We take the max, so both directions are caught.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..config import get_settings

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")
_MASK = (1 << 63) - 1


def normalize(text: str) -> list[str]:
    lowered = _PUNCT.sub(" ", text.lower())
    return _SPACE.sub(" ", lowered).strip().split()


def _hash(shingle: str) -> int:
    digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & _MASK


def shingle_hashes(words: list[str], k: int) -> list[int]:
    if len(words) < k:
        return [_hash(" ".join(words))] if words else []
    return [_hash(" ".join(words[i : i + k])) for i in range(len(words) - k + 1)]


def winnow(hashes: list[int], window: int) -> set[int]:
    """Keep the minimum hash of each sliding window. Ties resolve to the rightmost
    occurrence, per the paper — this keeps selections stable under insertion."""
    if not hashes:
        return set()
    if len(hashes) <= window:
        return {min(hashes)}

    selected: set[int] = set()
    previous_index = -1
    for start in range(len(hashes) - window + 1):
        chunk = hashes[start : start + window]
        smallest = min(chunk)
        # rightmost occurrence of the minimum in this window
        offset = len(chunk) - 1 - chunk[::-1].index(smallest)
        index = start + offset
        if index != previous_index:
            selected.add(smallest)
            previous_index = index
    return selected


def fingerprint(text: str, k: int | None = None, window: int | None = None) -> set[int]:
    settings = get_settings()
    k = k or settings.fingerprint_k
    window = window or settings.fingerprint_window
    return winnow(shingle_hashes(normalize(text), k), window)


@dataclass
class FingerprintMatch:
    document_id: str
    document_name: str
    severity: int
    action: str
    overlap: int
    ratio: float


def containment(query: set[int], reference: set[int]) -> float:
    """Max containment in either direction — see module docstring."""
    if not query or not reference:
        return 0.0
    shared = len(query & reference)
    return max(shared / len(query), shared / len(reference))


def match_against(
    query: set[int], corpus: dict[str, set[int]], threshold: float | None = None
) -> list[tuple[str, float, int]]:
    """In-memory matcher — used by tests and by small corpora. The DB-backed path in
    engine.py does the same arithmetic with a single indexed query."""
    threshold = threshold if threshold is not None else get_settings().fingerprint_threshold
    results: list[tuple[str, float, int]] = []
    for doc_id, reference in corpus.items():
        ratio = containment(query, reference)
        if ratio >= threshold:
            results.append((doc_id, ratio, len(query & reference)))
    return sorted(results, key=lambda item: item[1], reverse=True)
