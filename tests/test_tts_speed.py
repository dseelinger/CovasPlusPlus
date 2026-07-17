"""Unit tests for the normalized, provider-agnostic voice speed (issue #99) — offline, free.

Covers the ONE normalized `[tts].speed` -> each provider's native param mapping + clamp: the
ElevenLabs 0.7–1.2 widen, OpenAI 0.25–4.0, Edge/Azure signed-percent rate, Cartesia's [-1,1] axis,
and ESPECIALLY Piper's INVERSE `length_scale`. Also: an out-of-range stored value CLAMPS (never
errors), a garbage value falls back to 1.0, and a provider switch can't carry an invalid value
across (the normalized value is the only thing stored; each provider caps at synth time).
"""
from __future__ import annotations

import math

import pytest

from covas import tts_speed as ts


# --- normalized_speed: read, fallback, clamp, garbage --------------------------------------

def test_reads_tts_speed():
    assert ts.normalized_speed({"tts": {"speed": 1.5}}) == 1.5


def test_falls_back_to_legacy_elevenlabs_speed_when_tts_absent():
    assert ts.normalized_speed({"elevenlabs": {"speed": 1.1}}) == pytest.approx(1.1)


def test_tts_speed_wins_over_legacy():
    assert ts.normalized_speed({"tts": {"speed": 0.8}, "elevenlabs": {"speed": 1.2}}) == 0.8


def test_out_of_range_stored_value_is_clamped_not_errored():
    # A value beyond the normalized band is capped, never raised — so a bad config can't crash.
    assert ts.normalized_speed({"tts": {"speed": 9.0}}) == ts.SPEED_MAX
    assert ts.normalized_speed({"tts": {"speed": 0.01}}) == ts.SPEED_MIN


def test_garbage_and_missing_default_to_1():
    assert ts.normalized_speed({"tts": {"speed": "fast"}}) == 1.0
    assert ts.normalized_speed({}) == 1.0
    assert ts.normalized_speed({"tts": {}}) == 1.0


def test_is_default():
    assert ts.is_default(1.0)
    assert ts.is_default(1.0 + 1e-9)
    assert not ts.is_default(1.2)


# --- ElevenLabs: clamp to the quality-safe 0.7–1.2 (widened from the old 1.0–1.2) ----------

@pytest.mark.parametrize("n,expected", [
    (1.0, 1.0), (1.1, 1.1), (1.2, 1.2),
    (0.8, 0.8),              # slow-down now allowed (was clamped up to 1.0 before)
    (2.0, 1.2),             # above native max -> capped
    (0.5, 0.7),             # below native min -> capped at the 0.7 floor
])
def test_elevenlabs_speed_clamps(n, expected):
    assert ts.elevenlabs_speed(n) == pytest.approx(expected)


# --- OpenAI: 0.25–4.0 multiplier -----------------------------------------------------------

@pytest.mark.parametrize("n,expected", [
    (1.0, 1.0), (2.0, 2.0), (0.5, 0.5),
    (0.1, 0.25),            # below native min -> capped (normalized won't reach here, but be safe)
    (9.0, 4.0),             # above native max -> capped
])
def test_openai_speed_clamps(n, expected):
    assert ts.openai_speed(n) == pytest.approx(expected)


# --- Edge / Azure: signed-percent rate string ----------------------------------------------

@pytest.mark.parametrize("n,expected", [
    (1.0, "+0%"), (1.5, "+50%"), (2.0, "+100%"),
    (0.8, "-20%"), (0.5, "-50%"),
    (3.0, "+100%"),         # beyond the rate cap -> capped at +100%
])
def test_rate_string(n, expected):
    assert ts.rate_string(n) == expected
    assert ts.edge_rate(n) == expected
    assert ts.azure_rate(n) == expected


# --- Cartesia: [-1, 1] axis, 0 = normal, piecewise ----------------------------------------

@pytest.mark.parametrize("n,expected", [
    (1.0, 0.0),
    (2.0, 1.0),             # fastest
    (0.5, -1.0),            # slowest
    (1.5, 0.5),             # halfway up the fast half
    (0.75, -0.5),           # halfway down the slow half
])
def test_cartesia_speed(n, expected):
    assert ts.cartesia_speed(n) == pytest.approx(expected)


def test_cartesia_speed_clamped_to_axis():
    assert ts.cartesia_speed(9.0) == 1.0
    assert ts.cartesia_speed(0.01) == -1.0


# --- Piper: INVERSE length_scale (larger = slower) — the sign-error trap -------------------

def test_piper_length_scale_is_inverse():
    # normal speed leaves the voice's base length_scale untouched
    assert ts.piper_length_scale(1.0) == pytest.approx(1.0)
    # FASTER -> SMALLER length_scale (this is the direction that's easy to get backwards)
    assert ts.piper_length_scale(2.0) == pytest.approx(0.5)
    assert ts.piper_length_scale(2.0) < ts.piper_length_scale(1.0)
    # SLOWER -> LARGER length_scale
    assert ts.piper_length_scale(0.5) == pytest.approx(2.0)
    assert ts.piper_length_scale(0.5) > ts.piper_length_scale(1.0)


def test_piper_length_scale_respects_voice_base():
    # A voice whose own default length_scale is 1.2 is scaled by 1/speed, not reset to 1/speed.
    assert ts.piper_length_scale(2.0, base=1.2) == pytest.approx(0.6)
    # A garbage / non-positive base falls back to 1.0.
    assert ts.piper_length_scale(2.0, base=0) == pytest.approx(0.5)
    assert ts.piper_length_scale(2.0, base="nope") == pytest.approx(0.5)


def test_piper_length_scale_clamps_out_of_range_speed():
    # An out-of-range normalized speed is capped BEFORE inverting, so length_scale stays sane.
    assert ts.piper_length_scale(9.0) == pytest.approx(1.0 / ts.SPEED_MAX)
    assert ts.piper_length_scale(0.01) == pytest.approx(1.0 / ts.SPEED_MIN)


# --- provider switch can't carry an out-of-range value across ------------------------------

def test_provider_switch_caps_per_provider():
    # One stored normalized value (say a very slow 0.5); each provider caps it to ITS own range,
    # so switching providers never sends a value the new backend would reject.
    cfg = {"tts": {"speed": 0.5}}
    n = ts.normalized_speed(cfg)
    assert ts.elevenlabs_speed(n) == pytest.approx(0.7)     # EL floor
    assert ts.openai_speed(n) == pytest.approx(0.5)         # within OpenAI range
    assert ts.cartesia_speed(n) == pytest.approx(-1.0)      # Cartesia axis floor
    assert ts.rate_string(n) == "-50%"                       # Edge/Azure
    assert ts.piper_length_scale(n) == pytest.approx(2.0)    # Piper slower
    assert math.isclose(n, 0.5)
