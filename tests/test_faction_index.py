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


# ---- issue #164: _ensure must publish _lut BEFORE _names (torn-write race) ---------------------

def test_ensure_publishes_lut_before_names():
    # `_names is not None` is the "loaded" sentinel readers check first; if it is set before _lut,
    # a concurrent reader sees names-loaded but an empty table -> false "faction not found". Record
    # the assignment order and assert the publish sets _lut then _names.
    class _TrackingIndex(FactionIndex):
        def __setattr__(self, name, value):
            if name in ("_names", "_lut"):
                object.__getattribute__(self, "_order").append(name)
            super().__setattr__(name, value)

    idx = _TrackingIndex.__new__(_TrackingIndex)
    object.__setattr__(idx, "_order", [])
    idx.__init__(fetch=lambda: list(_NAMES))
    idx._ensure()
    # The last two tracked assignments are the publish inside _ensure: _lut first, then _names.
    assert idx._order[-2:] == ["_lut", "_names"]
    # ...and the published state is internally consistent (non-empty names imply a populated table).
    assert idx.resolve("Mother Gaia") == "Mother Gaia"


def test_concurrent_readers_never_see_torn_state():
    # Stress the publish under contention: many reader threads resolving while the index loads once.
    # A reader that observes _names populated must always resolve a known name (never a torn empty
    # table). With the ordered publish this holds; without it, readers could intermittently miss.
    import threading

    misses: list[str] = []
    idx = FactionIndex(fetch=lambda: list(_NAMES))

    def _reader() -> None:
        for _ in range(200):
            if idx._names is not None and idx.resolve("Mother Gaia") is None:
                misses.append("torn")

    threads = [threading.Thread(target=_reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert misses == []
