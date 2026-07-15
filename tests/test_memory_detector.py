"""Unit tests for the memory recall detector (issue #61, DESIGN §9).

Pure phrase-rule classification, offline: does a turn reach into stored memory, the 'recall'
wake word forcing a look-up, wake-word stripping, and config overrides via [memory]. The exact
twin of the ED detector tests — recall mirrors that cache-safe pattern.
"""
from __future__ import annotations

from covas.memory import MemoryDetector
from covas.memory.detector import MemoryDetectorConfig


def _det(cfg: dict | None = None) -> MemoryDetector:
    return MemoryDetector.from_cfg(cfg or {})


# --- decide ----------------------------------------------------------------

def test_do_you_remember_matches():
    ref = _det().decide("do you remember my main ship?")
    assert ref.matched
    assert "recall reference" in ref.reason


def test_whats_my_matches():
    assert _det().decide("what's my favourite mining spot?").matched


def test_have_i_been_matches():
    ref = _det().decide("have I been to Colonia before?")
    assert ref.matched and "have i been" in ref.reason


def test_preference_phrase_matches():
    assert _det().decide("remind me what I prefer to be called").matched


def test_wake_word_forces_recall():
    ref = _det().decide("recall: tell me about the Thargoids")
    assert ref.matched and "wake word" in ref.reason


def test_plain_turn_does_not_match():
    ref = _det().decide("tell me a joke, COVAS")
    assert not ref.matched


def test_case_and_spacing_insensitive():
    assert _det().decide("DO   YOU  REMEMBER my callsign").matched


# --- strip -----------------------------------------------------------------

def test_strip_removes_wake_word():
    assert _det().strip("recall what's my main ship") == "what's my main ship"


def test_strip_leaves_natural_recall_phrase_untouched():
    # 'do you remember' is a recall phrase, NOT the wake word -> never stripped.
    assert _det().strip("do you remember my ship") == "do you remember my ship"


def test_strip_only_wake_word_falls_back_to_original():
    assert _det().strip("recall") == "recall"


# --- config overrides ------------------------------------------------------

def test_from_cfg_overrides_phrases():
    cfg = {"memory": {"recall_phrases": ["dredge up"], "recall_wake": ["memory"]}}
    det = _det(cfg)
    assert det.decide("dredge up my ship").matched
    assert not det.decide("do you remember").matched      # default phrase overridden away
    assert det.decide("memory please").matched


def test_from_cfg_defaults_when_absent():
    cfg = MemoryDetectorConfig.from_cfg({})
    assert "do you remember" in cfg.recall_phrases
    assert cfg.wake_phrases == ["recall"]
