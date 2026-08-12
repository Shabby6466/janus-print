from __future__ import annotations

from janusprint.inspector.fingerprint import (
    containment,
    fingerprint,
    match_against,
    normalize,
    shingle_hashes,
    winnow,
)

BASE = (
    "The target company operates fourteen distribution centres across the region with "
    "combined annual revenue of two hundred and twelve million and an adjusted operating "
    "margin of eleven point four percent before the synergies described in appendix C"
)


def test_normalize_strips_punctuation_and_case():
    assert normalize("Hello, World!  Again") == ["hello", "world", "again"]


def test_winnowing_selects_a_stable_subset():
    hashes = shingle_hashes(normalize(BASE), 5)
    selected = winnow(hashes, 4)
    assert 0 < len(selected) < len(hashes)


def test_insertion_preserves_most_fingerprints():
    """The property that makes this beat plain hashing: local edits stay local."""
    original = fingerprint(BASE)
    edited = fingerprint(BASE.replace("appendix C", "appendix C and the covering note"))
    assert containment(edited, original) > 0.7


def test_excerpt_is_contained_in_the_original():
    original = fingerprint(BASE)
    excerpt = fingerprint(" ".join(BASE.split()[:20]))
    assert containment(excerpt, original) > 0.8


def test_unrelated_text_does_not_match():
    assert containment(fingerprint(BASE), fingerprint("Cafeteria menu for Monday")) < 0.1


def test_reordering_paragraphs_still_matches():
    words = BASE.split()
    half = len(words) // 2
    swapped = " ".join(words[half:] + words[:half])
    assert containment(fingerprint(swapped), fingerprint(BASE)) > 0.6


def test_match_against_respects_the_threshold():
    corpus = {"doc-a": fingerprint(BASE), "doc-b": fingerprint("Completely different content")}
    matches = match_against(fingerprint(BASE), corpus, threshold=0.5)
    assert [doc_id for doc_id, _ratio, _overlap in matches] == ["doc-a"]


def test_empty_input_is_safe():
    assert fingerprint("") == set()
    assert containment(set(), fingerprint(BASE)) == 0.0
