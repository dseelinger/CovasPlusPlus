"""Unit tests for the wake-word gate (issue #64) — pure, offline, free.

These exercise the ``WakeWordGate`` rules class with plain strings: no mic, no audio device,
no Whisper, no network. They cover the whole contract — disabled pass-through, match /
no-match, phrase stripping (leading, trailing, embedded), fuzzy STT-slip tolerance, and the
config plumbing that turns ``[listen].wake_word`` into a gate.
"""
from __future__ import annotations

from covas.wake import WakeWordConfig, WakeWordGate


def _gate(phrase: str, *, fuzzy: bool = True) -> WakeWordGate:
    return WakeWordGate(WakeWordConfig(phrase=phrase, fuzzy=fuzzy))


# --- disabled (empty phrase = OFF, the default) ----------------------------

def test_empty_phrase_is_disabled_and_passes_through():
    gate = _gate("")
    assert gate.enabled is False
    res = gate.check("what's my fuel, Commander?")
    assert res.armed is True
    assert res.text == "what's my fuel, Commander?"


def test_whitespace_or_punctuation_only_phrase_is_disabled():
    """A phrase with no word characters yields no tokens = OFF (can't gate on nothing)."""
    assert _gate("   ").enabled is False
    assert _gate("!!!").enabled is False


def test_from_cfg_defaults_to_disabled():
    gate = WakeWordGate.from_cfg({})
    assert gate.enabled is False
    assert gate.check("hello").armed is True


def test_from_cfg_reads_phrase_and_fuzzy_flag():
    cfg = {"listen": {"wake_word": "COVAS", "wake_word_fuzzy": False}}
    c = WakeWordConfig.from_cfg(cfg)
    assert c.phrase == "COVAS" and c.fuzzy is False


# --- match / no-match ------------------------------------------------------

def test_leading_wake_word_arms_and_strips():
    res = _gate("COVAS").check("COVAS, what's my fuel?")
    assert res.armed is True
    assert res.text == "what's my fuel?"


def test_case_insensitive_match():
    res = _gate("COVAS").check("covas what is the time")
    assert res.armed is True
    assert res.text == "what is the time"


def test_no_wake_word_is_not_armed():
    res = _gate("COVAS").check("what's my fuel, Commander?")
    assert res.armed is False
    assert res.text == ""


def test_wake_word_only_arms_but_cleans_to_empty():
    """A capture that is JUST the wake word has nothing to answer — armed, empty command.
    (app.py treats an empty command as 'nothing to say' and returns to Idle.)"""
    res = _gate("COVAS").check("COVAS.")
    assert res.armed is True
    assert res.text == ""


def test_wake_word_is_matched_as_a_whole_word_not_a_substring():
    """A wake word 'cove' must not arm on 'discover' — matching is on word tokens."""
    res = _gate("cove", fuzzy=False).check("discover the nearest station")
    assert res.armed is False


# --- stripping variants (only the phrase is removed) -----------------------

def test_trailing_wake_word_is_stripped_both_sides_preserved():
    res = _gate("COVAS").check("what's the time, COVAS")
    assert res.armed is True
    assert res.text == "what's the time"


def test_embedded_wake_word_keeps_surrounding_words():
    res = _gate("COVAS").check("hey COVAS what's my cargo")
    assert res.armed is True
    assert res.text == "hey what's my cargo"


def test_multi_word_wake_phrase():
    res = _gate("hey covas").check("hey COVAS, plot a route to Sol")
    assert res.armed is True
    assert res.text == "plot a route to Sol"


# --- fuzzy tolerance for STT slips -----------------------------------------

def test_fuzzy_tolerates_common_stt_slip():
    """Whisper often hears a short call sign a letter off — 'Kovas'/'Covis' must still arm."""
    for slip in ("Kovas", "Covis", "Covass"):
        res = _gate("COVAS").check(f"{slip}, what's my fuel?")
        assert res.armed is True, slip
        assert res.text == "what's my fuel?"


def test_fuzzy_still_rejects_an_unrelated_word():
    res = _gate("COVAS").check("gas up the ship")
    assert res.armed is False


def test_fuzzy_off_requires_exact_word():
    """With fuzzy disabled a slip no longer arms, but the exact word (any case) still does."""
    assert _gate("COVAS", fuzzy=False).check("Kovas what's my fuel").armed is False
    assert _gate("COVAS", fuzzy=False).check("covas what's my fuel").armed is True
