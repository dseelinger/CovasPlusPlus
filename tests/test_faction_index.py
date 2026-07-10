"""Unit tests for the canonical faction-name index (offline, DESIGN §9).

The index resolves a spoken (often mistranscribed) minor-faction name to Spansh's EXACT
string, because the faction filter is exact-match — a mishear returns zero systems otherwise,
which is what let the model confabulate a location. The fetch is injected so this is offline.
"""
from __future__ import annotations

from covas.search.faction_index import FactionIndex

_NAMES = ["Formidine Greybeard Guild", "The Formidine Bounty Hunters", "Mother Gaia",
          "Sol Workers' Party", "The Dark Wheel", "Greybeard Delta"]


def _index():
    return FactionIndex(fetch=lambda: list(_NAMES))


def test_exact_name_resolves():
    assert _index().resolve("Formidine Greybeard Guild") == "Formidine Greybeard Guild"


def test_case_and_spacing_insensitive():
    assert _index().resolve("  the DARK wheel ") == "The Dark Wheel"


def test_mistranscription_resolves_to_intended_faction():
    # 'Formadine' (the reported Whisper mishear) -> 'Formidine Greybeard Guild'.
    assert _index().resolve("Formadine Greybeard Guild") == "Formidine Greybeard Guild"


def test_partial_name_resolves():
    assert _index().resolve("Formidine Greybeard") == "Formidine Greybeard Guild"


def test_unrelated_name_does_not_resolve():
    assert _index().resolve("Galactic Empire of Xed") is None


def test_suggestions_are_real_names():
    sugg = _index().suggestions("Formidine Exiles")
    assert "Formidine Greybeard Guild" in sugg
    assert all(s in _NAMES for s in sugg)         # never invents a name


def test_loaded_reflects_a_successful_fetch():
    idx = _index()
    assert idx.loaded is True


def test_fetch_failure_is_fail_soft():
    def _boom():
        raise ConnectionError("offline")
    idx = FactionIndex(fetch=_boom)
    assert idx.loaded is False
    assert idx.resolve("Formidine Greybeard Guild") is None   # no crash, just no resolution
    assert idx.suggestions("anything") == []


def test_empty_list_is_fail_soft():
    idx = FactionIndex(fetch=lambda: [])
    assert idx.loaded is False and idx.resolve("Mother Gaia") is None


def test_fetch_is_lazy_and_cached():
    calls = {"n": 0}
    def _fetch():
        calls["n"] += 1
        return list(_NAMES)
    idx = FactionIndex(fetch=_fetch)
    assert calls["n"] == 0                          # not fetched at construction
    idx.resolve("Mother Gaia")
    idx.resolve("The Dark Wheel")
    idx.suggestions("x")
    assert calls["n"] == 1                          # fetched once, then cached
