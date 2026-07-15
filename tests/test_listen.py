"""Unit tests for the hands-free VAD gate (issue #63) — pure, offline, free.

These exercise the ``VadGate`` state machine and the listener's capture/pre-roll logic
with a SYNTHETIC energy/frame sequence: no real mic, no sounddevice, no wall clock, no
real-time threads. Time is measured purely by the number of frames pushed (each worth
``frame_ms``), which is exactly what makes the gate deterministic to test. The on-hardware
``VadListener`` mic thread is covered by MANUAL_TESTS, not here.
"""
from __future__ import annotations

import numpy as np

from covas.listen import (
    VadEvent,
    VadGate,
    VadListener,
    VadTuning,
    frame_rms,
    tuning_from_cfg,
)

# Deterministic tuning: 10 ms frames, onset after 3 voiced frames (30 ms), an utterance
# needs 50 ms of voiced audio, and 50 ms (5 frames) of trailing silence ends it.
TUNING = VadTuning(frame_ms=10.0, energy_threshold=0.1,
                   start_ms=30.0, min_speech_ms=50.0, hangover_ms=50.0)
LOUD = 0.5
QUIET = 0.0


def _events(gate: VadGate, energies) -> list[VadEvent]:
    return [gate.push(e) for e in energies]


# --- onset detection + debounce -------------------------------------------

def test_onset_requires_debounce_run():
    """A voiced run shorter than start_ms must NOT open a capture (debounces clicks)."""
    gate = VadGate(TUNING)
    assert gate.push(LOUD) is VadEvent.NONE   # 10 ms
    assert gate.push(LOUD) is VadEvent.NONE   # 20 ms
    assert gate.push(LOUD) is VadEvent.SPEECH_START  # 30 ms — confirmed
    assert gate.in_speech


def test_sub_onset_blip_never_starts():
    """Two voiced frames then silence resets the onset run — no speech ever starts."""
    gate = VadGate(TUNING)
    events = _events(gate, [LOUD, LOUD, QUIET, QUIET])
    assert all(e is VadEvent.NONE for e in events)
    assert not gate.in_speech


def test_intermittent_noise_below_onset_never_triggers():
    """Alternating loud/quiet frames never accumulate a full onset run."""
    gate = VadGate(TUNING)
    events = _events(gate, [LOUD, QUIET, LOUD, QUIET, LOUD, QUIET])
    assert all(e is VadEvent.NONE for e in events)


# --- full utterance --------------------------------------------------------

def test_full_utterance_starts_and_ends():
    gate = VadGate(TUNING)
    # Onset (3 loud) then 2 more loud -> 50 ms voiced (>= min_speech).
    seq = [LOUD, LOUD, LOUD, LOUD, LOUD]
    assert _events(gate, seq)[2] is VadEvent.SPEECH_START
    # 5 quiet frames = 50 ms of hangover -> utterance ends.
    tail = _events(gate, [QUIET, QUIET, QUIET, QUIET, QUIET])
    assert tail[-1] is VadEvent.SPEECH_END
    assert not gate.in_speech  # reset for the next utterance


def test_mid_utterance_pause_does_not_end_it():
    """Silence shorter than hangover_ms is tolerated (a breath mid-sentence)."""
    gate = VadGate(TUNING)
    _events(gate, [LOUD, LOUD, LOUD, LOUD, LOUD])  # started, 50 ms voiced
    # 4 quiet frames = 40 ms < 50 ms hangover: still in speech.
    assert all(e is VadEvent.NONE for e in _events(gate, [QUIET] * 4))
    assert gate.in_speech
    # more speech resets the silence run...
    assert gate.push(LOUD) is VadEvent.NONE
    # ...so it now takes another full hangover to end.
    tail = _events(gate, [QUIET] * 5)
    assert tail[-1] is VadEvent.SPEECH_END


# --- noise rejection -------------------------------------------------------

def test_short_utterance_rejected_as_noise():
    """Onset fires but the voiced total never reaches min_speech -> NOISE_REJECTED."""
    gate = VadGate(TUNING)
    assert gate.push(LOUD) is VadEvent.NONE
    assert gate.push(LOUD) is VadEvent.NONE
    assert gate.push(LOUD) is VadEvent.SPEECH_START  # voiced = 30 ms only
    # straight into silence: 50 ms hangover ends it, but 30 < 50 min_speech
    tail = _events(gate, [QUIET] * 5)
    assert tail[-1] is VadEvent.NOISE_REJECTED
    assert not gate.in_speech


def test_gate_reusable_across_utterances():
    """After an END the gate is back in SILENCE and detects a fresh onset."""
    gate = VadGate(TUNING)
    _events(gate, [LOUD] * 5 + [QUIET] * 5)  # one full utterance
    assert not gate.in_speech
    assert gate.push(LOUD) is VadEvent.NONE
    assert gate.push(LOUD) is VadEvent.NONE
    assert gate.push(LOUD) is VadEvent.SPEECH_START  # second utterance starts


def test_threshold_boundary_is_inclusive():
    """Energy exactly at the threshold counts as speech (>=)."""
    gate = VadGate(VadTuning(frame_ms=10.0, energy_threshold=0.1,
                             start_ms=10.0, min_speech_ms=10.0, hangover_ms=20.0))
    assert gate.push(0.1) is VadEvent.SPEECH_START


def test_reset_clears_state():
    gate = VadGate(TUNING)
    _events(gate, [LOUD, LOUD, LOUD])
    assert gate.in_speech
    gate.reset()
    assert not gate.in_speech
    assert gate.push(LOUD) is VadEvent.NONE  # onset run starts fresh


# --- config + energy helpers ----------------------------------------------

def test_tuning_from_cfg_reads_values_and_defaults():
    cfg = {"listen": {"mode": "continuous", "energy_threshold": 0.05, "hangover_ms": 900.0}}
    t = tuning_from_cfg(cfg)
    assert t.energy_threshold == 0.05
    assert t.hangover_ms == 900.0
    assert t.frame_ms == VadTuning().frame_ms  # missing key -> dataclass default


def test_tuning_from_cfg_empty_is_all_defaults():
    assert tuning_from_cfg({}) == VadTuning()


def test_frame_rms_basic():
    assert frame_rms(np.zeros(100, dtype=np.float32)) == 0.0
    assert frame_rms(np.array([], dtype=np.float32)) == 0.0
    assert abs(frame_rms(np.full(100, 0.5, dtype=np.float32)) - 0.5) < 1e-6
    # non-finite audio reads as silence, never a spurious trigger
    assert frame_rms(np.array([np.inf, np.nan], dtype=np.float32)) == 0.0


# --- listener capture logic (synchronous, no mic / no thread) -------------
# We drive VadListener._on_frame directly with synthetic frames — this exercises the
# pre-roll + capture buffering offline without opening a device or starting a thread.

def _mono(amp: float, n: int) -> np.ndarray:
    return np.full(n, amp, dtype=np.float32)


def _listener(**cb):
    cfg = {
        "audio": {"sample_rate": 16000, "input_device": ""},
        "listen": {"frame_ms": 10.0, "energy_threshold": 0.1,
                   "start_ms": 30.0, "min_speech_ms": 50.0, "hangover_ms": 50.0},
    }
    return VadListener(cfg, on_speech_start=cb.get("on_start", lambda: None),
                       on_utterance=cb.get("on_utt", lambda a: None),
                       log=lambda m: None)


def test_listener_fires_onset_and_delivers_captured_audio():
    started = []
    utterances: list[np.ndarray] = []
    lis = _listener(on_start=lambda: started.append(True),
                    on_utt=lambda a: utterances.append(a))
    flen = lis._frame_len  # samples per frame (160 at 16 kHz / 10 ms)

    # a couple of idle (quiet) frames feed the pre-roll buffer
    for _ in range(3):
        lis._on_frame(_mono(0.0, flen))
    # speech: 5 loud frames -> onset on the 3rd, 50 ms voiced
    for _ in range(5):
        lis._on_frame(_mono(0.5, flen))
    assert started == [True]  # onset fired exactly once
    # trailing silence ends the utterance
    for _ in range(5):
        lis._on_frame(_mono(0.0, flen))

    assert len(utterances) == 1
    audio = utterances[0]
    # captured audio is float32 and longer than the voiced portion alone, because it
    # includes pre-roll frames captured just before the confirmed onset.
    assert audio.dtype == np.float32
    assert audio.size > 5 * flen


def test_listener_drops_noise_blip_without_dispatching():
    utterances = []
    lis = _listener(on_utt=lambda a: utterances.append(a))
    flen = lis._frame_len
    for _ in range(3):
        lis._on_frame(_mono(0.5, flen))   # onset, only 30 ms voiced
    for _ in range(5):
        lis._on_frame(_mono(0.0, flen))   # hangover -> rejected as noise
    assert utterances == []  # nothing dispatched
