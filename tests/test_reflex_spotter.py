"""Unit tests for the local reflex phrase-spotter (issue #38) — pure, offline, free (§9).

The spotter is the FAST PATH's brain: a pure function of *(fixed vocabulary, transcript)* that
maps a combat keyword STRAIGHT to a Tier-2 reflex name, bypassing the LLM. These pin the three
behaviours the dispatch relies on:

  * exact / synonym / multi-word MATCH -> the correct reflex name;
  * whole-word matching (no substring false fires) and leftmost-wins;
  * NO MATCH -> None, so the caller falls through to a normal LLM turn.

No audio, no keys, no executor, no LLM — plain strings only.
"""
from __future__ import annotations

import pytest

from covas.capabilities.reflex_capability import COMBAT_PERMISSIVE
from covas.reflex_spotter import ABORT, REFLEX_VOCABULARY, PhraseSpotter


@pytest.fixture()
def spotter() -> PhraseSpotter:
    return PhraseSpotter.from_cfg({})


# --- exact / synonym matches -> the right reflex ----------------------------

@pytest.mark.parametrize("text,expected", [
    ("chaff", "chaff"),
    ("chaff!", "chaff"),
    ("Chaff now, quick!", "chaff"),
    ("fire the flares", "chaff"),
    ("decoy", "chaff"),
    ("break their lock", "chaff"),          # multi-word synonym
    ("break lock", "chaff"),
    ("heat", "heat_sink"),
    ("heat sink", "heat_sink"),             # multi-word
    ("heatsink please", "heat_sink"),
    ("dump heat", "heat_sink"),
    ("shields", "shields"),
    ("shield cell", "shields"),
    ("pop a cell", "shields"),
    ("boost", "boost"),
    ("punch it", "boost"),
])
def test_keyword_maps_to_reflex(spotter, text, expected):
    assert spotter.match(text) == expected


def test_match_is_case_insensitive(spotter):
    assert spotter.match("CHAFF") == "chaff"
    assert spotter.match("Boost") == "boost"


# --- abort sentinel ---------------------------------------------------------

@pytest.mark.parametrize("text", ["abort", "abort!", "stop", "cancel that", "release", "belay"])
def test_abort_phrases_return_the_abort_sentinel(spotter, text):
    assert spotter.match(text) == ABORT


def test_abort_is_not_a_combat_permissive_reflex():
    # ABORT is a meta action (hard abort), never a member of the fireable reflex set.
    assert ABORT not in COMBAT_PERMISSIVE


# --- whole-word: no substring false fires -----------------------------------

@pytest.mark.parametrize("text", [
    "chaffinch on the scanner",     # 'chaff' is a substring of 'chaffinch' — must NOT fire
    "unshielded hull",              # 'shield' inside 'unshielded'
    "reheating the drive",          # 'heat' inside 'reheating'
    "the booster stage",            # 'boost' inside 'booster'
])
def test_substring_does_not_false_fire(spotter, text):
    assert spotter.match(text) is None


# --- leftmost wins ----------------------------------------------------------

def test_leftmost_keyword_wins(spotter):
    # Two keywords in one utterance -> the earlier one decides (deterministic).
    assert spotter.match("chaff then boost") == "chaff"
    assert spotter.match("boost then chaff") == "boost"


def test_abort_wins_only_when_it_comes_first(spotter):
    assert spotter.match("abort the chaff") == ABORT
    assert spotter.match("chaff, no abort") == "chaff"


# --- no match -> None (fall through to a normal turn) ------------------------

@pytest.mark.parametrize("text", [
    "",
    "   ",
    "what's my fuel level",
    "plot a route to Sol",
    "tell me a joke",
    "how many jumps to Colonia",
])
def test_no_keyword_returns_none(spotter, text):
    assert spotter.match(text) is None


def test_none_transcript_is_safe(spotter):
    assert spotter.match(None) is None  # type: ignore[arg-type]


# --- vocabulary integrity ---------------------------------------------------

def test_vocabulary_names_only_known_reflexes():
    # Every reflex name the spotter can return must be a COMBAT_PERMISSIVE member or ABORT, so it
    # can never hand ReflexCapability a name that isn't recognised.
    allowed = set(COMBAT_PERMISSIVE) | {ABORT}
    assert set(REFLEX_VOCABULARY) <= allowed
    assert set(REFLEX_VOCABULARY) == allowed   # and it covers all of them


def test_bad_vocabulary_is_rejected():
    with pytest.raises(ValueError):
        PhraseSpotter(vocab={"warp_drive": ("warp",)})


def test_every_reflex_returned_is_dispatchable_or_abort(spotter):
    # Sanity: every value the spotter can emit is either the abort sentinel or a fireable reflex.
    for name in REFLEX_VOCABULARY:
        assert name == ABORT or name in COMBAT_PERMISSIVE
