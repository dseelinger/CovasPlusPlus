"""Unit tests for the ED context detector (DESIGN §5, §9).

Pure phrase-rule classification, offline: status vs. log references, the 'context' wake
word forcing both, wake-word stripping, and config overrides via [elite].
"""
from __future__ import annotations

from covas.ed import ContextDetector
from covas.ed.detector import ContextDetectorConfig


def _det(cfg: dict | None = None) -> ContextDetector:
    return ContextDetector.from_cfg(cfg or {})


# --- decide ----------------------------------------------------------------

def test_status_phrase_matches_without_log():
    ref = _det().decide("where am I right now?")
    assert ref.matched and not ref.wants_log
    assert "status reference" in ref.reason


def test_fuel_question_matches():
    assert _det().decide("how's my fuel looking?").matched


def test_log_phrase_wants_log():
    ref = _det().decide("what just happened out there?")
    assert ref.matched and ref.wants_log
    assert "log reference" in ref.reason


def test_wake_word_forces_both():
    ref = _det().decide("context: tell me about the Thargoids")
    assert ref.matched and ref.wants_log
    assert "wake word" in ref.reason


def test_plain_turn_does_not_match():
    ref = _det().decide("tell me a joke, COVAS")
    assert not ref.matched and not ref.wants_log


def test_case_and_spacing_insensitive():
    assert _det().decide("WHERE   AM  I").matched


# --- strip -----------------------------------------------------------------

def test_strip_removes_wake_word():
    assert _det().strip("context where am I") == "where am I"


def test_strip_leaves_ordinary_text_untouched():
    # 'where am I' is a status phrase, NOT the wake word -> never stripped.
    assert _det().strip("where am I") == "where am I"


def test_strip_only_wake_word_falls_back_to_original():
    assert _det().strip("context") == "context"


# --- config overrides ------------------------------------------------------

def test_from_cfg_overrides_phrases():
    cfg = {"elite": {"status_phrases": ["how goes it"], "log_phrases": [],
                     "context_wake": ["sitrep"]}}
    det = _det(cfg)
    assert det.decide("how goes it").matched
    assert not det.decide("where am I").matched          # default phrase overridden away
    assert det.decide("sitrep please").wants_log


def test_from_cfg_defaults_when_absent():
    cfg = ContextDetectorConfig.from_cfg({})
    assert "where am i" in cfg.status_phrases
    assert cfg.wake_phrases == ["context"]
