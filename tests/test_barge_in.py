"""Unit tests for barge-in playback teardown (issue #71). Offline, no device/network.

Barge-in = pressing PTT (or a VAD onset in continuous mode) while COVAS is still speaking. The
mic must NOT open until playback has actually gone silent, or it records the tail of COVAS's own
reply leaking from the speakers (there is no acoustic echo cancellation) and pollutes the next
utterance. These tests assert the ordering across ALL barge-in entry points: the mixer is
hard-stopped (cancel_speech) and awaited BEFORE the recorder starts, and the barge-in capture gets
a leading mute window. We drive App with a fake mixer/recorder/cues that record call order.
"""
from __future__ import annotations

import threading

import numpy as np

from covas.app import _BARGE_IN_MUTE_MS
from covas.audio import Recorder
from covas.app import App
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Jump to Sol\n", encoding="utf-8")
    return {
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "keys": {"push_to_talk": "right ctrl"},
    }


class _Log:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class _FakeRecorder:
    def __init__(self, log: _Log) -> None:
        self._log = log
        self.mute_ms = None

    def start(self, mute_ms: float = 0.0) -> None:
        self.mute_ms = mute_ms
        self._log.calls.append(("recorder.start", mute_ms))

    def stop(self):
        self._log.calls.append(("recorder.stop",))
        return object()


class _FakeMixer:
    """Records cancel_speech + reports speech_active. `active_reads` scripts what speech_active()
    returns on successive calls so a test can prove _halt_playback AWAITS silence (not just calls
    cancel). Defaults to going silent immediately after cancel."""

    def __init__(self, log: _Log, active_reads: list[bool] | None = None) -> None:
        self._log = log
        self.cancel_count = 0
        self._silent = False
        self._reads = list(active_reads) if active_reads is not None else None

    def cancel_speech(self) -> None:
        self.cancel_count += 1
        self._silent = True
        self._log.calls.append(("mixer.cancel_speech",))

    def speech_active(self) -> bool:
        self._log.calls.append(("mixer.speech_active",))
        if self._reads:
            return self._reads.pop(0)
        return not self._silent


class _FakeCues:
    def __init__(self, log: _Log) -> None:
        self._log = log

    def play(self, name: str) -> None:
        self._log.calls.append(("cues.play", name))

    def stop(self) -> None:
        self._log.calls.append(("cues.stop",))

    def stop_loop(self) -> None:  # set_state calls this on leaving a working state
        pass

    def start_loop(self, name: str) -> None:
        pass


def _barge_app(tmp_path):
    app = App(_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    log = _Log()
    app.recorder = _FakeRecorder(log)
    app.mixer = _FakeMixer(log)
    app.cues = _FakeCues(log)
    return app, log


def _assert_halt_before_record(app, log) -> None:
    names = log.names()
    assert "mixer.cancel_speech" in names, "playback was never hard-stopped on barge-in"
    assert "recorder.start" in names
    assert names.index("mixer.cancel_speech") < names.index("recorder.start"), (
        "the mic opened before playback was silenced — it will capture the reply's tail")
    assert app.active_cancel.is_set()
    start = next(c for c in log.calls if c[0] == "recorder.start")
    assert start[1] == _BARGE_IN_MUTE_MS, "barge-in capture must get the leading mute window"


def test_on_ptt_down_halts_playback_before_recording(tmp_path):
    app, log = _barge_app(tmp_path)
    app.state = "Speaking"                       # a reply is playing
    app.active_cancel = threading.Event()
    app.on_ptt_down()
    _assert_halt_before_record(app, log)


def test_reflex_ptt_down_halts_playback_before_recording(tmp_path):
    app, log = _barge_app(tmp_path)
    app.state = "Speaking"
    app.active_cancel = threading.Event()
    app.on_reflex_ptt_down()
    _assert_halt_before_record(app, log)


def test_continuous_vad_onset_halts_playback(tmp_path):
    """The continuous-mode barge-in (_on_vad_speech_start) has no recorder.start (the VadListener
    owns the mic), so the mixer hard-stop IS the fix there."""
    app, log = _barge_app(tmp_path)
    app.state = "Speaking"
    app.active_cancel = threading.Event()
    app.ptt_held = False
    app._on_vad_speech_start()
    assert "mixer.cancel_speech" in log.names()
    assert app.active_cancel.is_set()


def test_non_barge_press_uses_no_mute_window(tmp_path):
    """From Idle nothing is playing, so a normal press must NOT clip the first word."""
    app, log = _barge_app(tmp_path)
    app.state = "Idle"
    app.on_ptt_down()
    start = next(c for c in log.calls if c[0] == "recorder.start")
    assert start[1] == 0.0


def test_halt_playback_awaits_confirmed_silence(tmp_path):
    """_halt_playback must POLL speech_active until it reads False (bounded), not return the moment
    cancel_speech is issued — the device buffer drains a few ms later."""
    app, log = _barge_app(tmp_path)
    # Report 'still playing' twice, then silent: proves the bounded await loop.
    app.mixer = _FakeMixer(log, active_reads=[True, True, False])
    app._halt_playback()
    assert app.mixer.cancel_count == 1
    # cancel_speech first, then repeated speech_active polls until it reads False.
    names = log.names()
    assert names[0] == "mixer.cancel_speech"
    assert names.count("mixer.speech_active") >= 3


# ---- the Recorder mute window (pure, no device) --------------------------------------------
def test_recorder_drops_leading_mute_window():
    rec = Recorder({"audio": {"sample_rate": 16000, "input_device": ""}})
    rec._drop_samples = int(16000 * _BARGE_IN_MUTE_MS / 1000.0)  # what start(mute_ms=...) sets
    # 4800 samples = 300 ms of capture; the first 2400 (150 ms) is the muted TTS-tail window.
    rec._frames = [np.arange(4800, dtype=np.float32).reshape(-1, 1)]
    out = rec.stop()
    assert out.shape[0] == 4800 - int(16000 * _BARGE_IN_MUTE_MS / 1000.0)
    assert out[0] == float(int(16000 * _BARGE_IN_MUTE_MS / 1000.0))  # tail dropped, speech kept
    assert rec._drop_samples == 0  # reset so the next (normal) capture isn't clipped


def test_recorder_no_mute_window_keeps_everything():
    rec = Recorder({"audio": {"sample_rate": 16000, "input_device": ""}})
    rec._frames = [np.arange(1600, dtype=np.float32).reshape(-1, 1)]  # mute_ms=0 default
    out = rec.stop()
    assert out.shape[0] == 1600
    assert out[0] == 0.0
