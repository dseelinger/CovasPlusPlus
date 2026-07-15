"""Tests for the OpenAI-compatible TTS provider (issue #16).

Default (unit) tests are OFFLINE and FREE: the network lives in one request helper (`_collect_pcm`),
which the tests monkeypatch. OpenAI's `pcm` response IS raw 16-bit PCM (no decode). The
`@pytest.mark.integration` `paid` test at the bottom hits the real API and skips unless a key is set.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from covas import firstrun
from covas.mixer import CastSynth, Voice
from covas.providers import openai_tts as oai


# ---- test doubles ----------------------------------------------------------
def _openai(monkeypatch=None, *, key="test-key", **cfg):
    """Build an OpenAITTS with defaults; when a key is given, patch the firstrun resolver so `_key()`
    resolves without a real service (keys are file-only/DPAPI now, not env vars). Extra kwargs land
    in the [openai_tts] config table."""
    if monkeypatch is not None and key is not None:
        monkeypatch.setattr(firstrun, "openai_key", lambda cfg: key)
    return oai.OpenAITTS({"openai_tts": {**cfg}})


class _FakeSink:
    def __init__(self):
        self.fed = bytearray()
        self.finished = False
        self.cancelled = False

    def feed(self, pcm):
        self.fed += pcm

    def finish(self):
        self.finished = True

    def cancel(self):
        self.cancelled = True

    def wait(self, timeout=None):
        return True


class _FakeMixer:
    def __init__(self, sink):
        self._sink = sink

    def open_speech(self, bus, sr):
        return self._sink


# ---- config defaults + request body ---------------------------------------
def test_defaults_and_base_url_trim(monkeypatch):
    e = _openai(monkeypatch, base_url="https://proxy.local/v1/")
    assert e._base_url == "https://proxy.local/v1"        # trailing slash stripped
    assert e._model == "gpt-4o-mini-tts" and e._voice == "alloy"


def test_body_shapes_request_and_omits_blank_instructions(monkeypatch):
    e = _openai(monkeypatch, voice="nova")
    body = e._body("hello", "echo")
    assert body == {"model": "gpt-4o-mini-tts", "voice": "echo", "input": "hello",
                    "response_format": "pcm"}         # no 'instructions' key when blank


def test_body_includes_instructions_when_set(monkeypatch):
    e = _openai(monkeypatch, instructions="Calm ship-computer tone")
    assert e._body("hi", "alloy")["instructions"] == "Calm ship-computer tone"


# ---- synth_pcm -------------------------------------------------------------
def test_synth_pcm_returns_pcm_and_rate(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda key, base, body, cancel: (b"\x01\x02" * 100, False))
    pcm, sr = _openai(monkeypatch).synth_pcm("hello", "shimmer")
    assert sr == 24000 and pcm == b"\x01\x02" * 100


def test_synth_pcm_passes_voice_and_model_into_body(monkeypatch):
    seen = {}
    def _fake(key, base, body, cancel):
        seen["body"] = body
        seen["base"] = base
        return b"PCM", False
    monkeypatch.setattr(oai, "_collect_pcm", _fake)
    _openai(monkeypatch, model="tts-1").synth_pcm("report", "onyx")
    assert seen["body"]["voice"] == "onyx" and seen["body"]["model"] == "tts-1"
    assert seen["body"]["response_format"] == "pcm"


def test_synth_pcm_empty_text_is_silent(monkeypatch):
    called = []
    monkeypatch.setattr(oai, "_collect_pcm", lambda *a: called.append(1) or (b"", False))
    assert _openai(monkeypatch).synth_pcm("   ") == (b"", 24000)
    assert not called


def test_synth_pcm_no_key_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        _openai(monkeypatch, key=None).synth_pcm("hi")


def test_synth_pcm_no_audio_raises(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda *a: (b"", False))
    with pytest.raises(RuntimeError):
        _openai(monkeypatch).synth_pcm("hi")


# ---- speak (playback + cancellation) ---------------------------------------
def test_speak_cancelled_during_synth_plays_nothing(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda key, base, body, cancel: (b"PCM" * 50, True))
    played = []
    monkeypatch.setattr(oai.OpenAITTS, "_play_direct", lambda self, pcm, sr, cancel: played.append(pcm))
    _openai(monkeypatch).speak("hi", threading.Event())
    assert not played


def test_speak_plays_pcm(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda key, base, body, cancel: (b"\x00\x01" * 500, False))
    played = []
    monkeypatch.setattr(oai.OpenAITTS, "_play_direct",
                        lambda self, pcm, sr, cancel: played.append((len(pcm), sr)))
    _openai(monkeypatch).speak("hi", threading.Event())
    assert played and played[0][1] == 24000 and played[0][0] > 0


def test_speak_empty_text_noop(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda *a: (_ for _ in ()).throw(AssertionError()))
    _openai(monkeypatch).speak("  ", threading.Event())


def test_speak_no_key_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        _openai(monkeypatch, key=None).speak("hi", threading.Event())


def test_play_via_mixer_feeds_and_finishes(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda key, base, body, cancel: (b"\x00\x01" * 500, False))
    sink = _FakeSink()
    monkeypatch.setattr(firstrun, "openai_key", lambda cfg: "k")
    e = oai.OpenAITTS({"openai_tts": {}}, mixer=_FakeMixer(sink))
    e.speak("hi", threading.Event())
    assert sink.fed and sink.finished and not sink.cancelled


def test_play_via_mixer_cancel_aborts(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda key, base, body, cancel: (b"\x00\x01" * 500, False))
    sink = _FakeSink()
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(firstrun, "openai_key", lambda cfg: "k")
    oai.OpenAITTS({"openai_tts": {}}, mixer=_FakeMixer(sink)).speak("hi", cancel)
    assert sink.cancelled and not sink.finished


# ---- voice catalog (static) ------------------------------------------------
def test_list_voices_is_static_catalog(monkeypatch):
    voices = _openai(monkeypatch).list_voices()
    refs = [v["ref"] for v in voices]
    assert "alloy" in refs and "shimmer" in refs
    assert all(v["gender"] == "neutral" for v in voices)
    assert voices[0]["name"] == voices[0]["ref"].capitalize()


# ---- cast registry integration ---------------------------------------------
def test_openai_is_cast_eligible_via_registry(monkeypatch):
    monkeypatch.setattr(oai, "_collect_pcm", lambda key, base, body, cancel: (b"\x02\x03" * 100, False))
    e = _openai(monkeypatch)
    cs = CastSynth(el_synth=None, piper_loader=None)
    cs.registry.register("openai", lambda text, ref: e.synth_pcm(text, ref or None))
    pcm, sr = cs(Voice("openai", "nova"), "hello")
    assert sr == 24000 and len(pcm) > 0


def test_openai_cast_voice_fails_soft_to_silence(monkeypatch):
    e = _openai(monkeypatch, key=None)
    cs = CastSynth(el_synth=None, piper_loader=None)
    cs.registry.register("openai", lambda text, ref: e.synth_pcm(text, ref or None))
    assert cs(Voice("openai", "nova"), "hello") == (b"", 16000)


# ---- opt-in integration (real OpenAI API; needs a key) ---------------------
@pytest.mark.integration
@pytest.mark.paid
def test_live_openai_synth_pcm_returns_audio():
    """One real OpenAI call. Needs OPENAI_API_KEY exported; skipped otherwise so the paid suite
    stays deliberate. Uses the cheap gpt-4o-mini-tts model."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("set OPENAI_API_KEY to run the live OpenAI TTS test")
    e = oai.OpenAITTS({"openai_tts": {"model": "gpt-4o-mini-tts", "voice": "alloy"}})
    pcm, sr = e.synth_pcm("Docking request granted, Commander.")
    assert sr == 24000 and len(pcm) > 1000
    # sanity: request body is JSON-serializable as sent
    assert json.dumps(e._body("x", "alloy"))
