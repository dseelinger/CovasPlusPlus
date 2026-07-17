"""Tests for the Edge (edge-tts) free neural TTS provider (issue #15).

The default (unit) tests are OFFLINE and FREE: the network lives in two module helpers
(`_collect_mp3` / `list_edge_voices`), which the tests monkeypatch or feed a locally-built MP3
fixture (soundfile writes + reads MP3 with no network). Only the `@pytest.mark.integration`
`local` tests at the bottom hit the real (free, no-key) Edge endpoint.
"""
from __future__ import annotations

import io
import threading

import numpy as np
import pytest
import soundfile as sf

from covas.mixer import CastSynth, Voice
from covas.providers import edge_tts as edge


# ---- offline fixtures ------------------------------------------------------
def _mp3_fixture(seconds: float = 0.2, sr: int = 24000) -> bytes:
    """A tiny real MP3 built locally (no network) so the decode path is exercised for real."""
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    pcm = (np.sin(2 * np.pi * 220 * t) * 10000).astype("int16")
    b = io.BytesIO()
    sf.write(b, pcm, sr, format="MP3")
    return b.getvalue()


class _FakeFallback:
    """A stand-in TTSProvider floor (what Piper would be) that records that it was used."""

    def __init__(self) -> None:
        self.spoke: list[str] = []
        self.synthed: list[str] = []

    def speak(self, text: str, cancel: threading.Event) -> None:
        self.spoke.append(text)

    def synth_pcm(self, text: str, voice_id=None) -> tuple[bytes, int]:
        self.synthed.append(text)
        return b"FALLBACK", 22050


# ---- pure helpers ----------------------------------------------------------
def test_decode_pcm_returns_mono_int16_and_rate():
    pcm, sr = edge._decode_pcm(_mp3_fixture())
    assert sr == 24000
    assert isinstance(pcm, bytes) and len(pcm) > 0
    assert len(pcm) % 2 == 0                 # whole 16-bit samples


def test_gender_maps_to_cast_vocabulary():
    assert edge._gender("Female") == "female"
    assert edge._gender("MALE") == "male"
    assert edge._gender("") == "neutral"
    assert edge._gender("Other") == "neutral"


def test_normalize_voices_filters_and_sorts():
    catalog = [
        {"ShortName": "en-US-AriaNeural", "FriendlyName": "Aria", "Gender": "Female",
         "Locale": "en-US"},
        {"ShortName": "en-GB-RyanNeural", "FriendlyName": "Ryan", "Gender": "Male",
         "Locale": "en-GB"},
        {"ShortName": "fr-FR-DeniseNeural", "FriendlyName": "Denise", "Gender": "Female",
         "Locale": "fr-FR"},
        {"ShortName": "", "Locale": "en-US"},           # skipped: no ShortName
    ]
    voices = edge._normalize_voices(catalog, "en-")
    assert [v["ref"] for v in voices] == ["en-GB-RyanNeural", "en-US-AriaNeural"]  # sorted, en-only
    assert voices[0]["gender"] == "male" and voices[1]["gender"] == "female"
    assert voices[1]["name"] == "Aria" and voices[1]["locale"] == "en-US"
    # blank prefix keeps everything (still sorted, still drops the empty ShortName)
    assert len(edge._normalize_voices(catalog, "")) == 3


# ---- synth_pcm -------------------------------------------------------------
def test_synth_pcm_decodes_edge_audio(monkeypatch):
    monkeypatch.setattr(edge, "_collect_mp3", lambda text, voice, cancel, rate=None: (_mp3_fixture(), False))
    pcm, sr = EdgeUnderTest().synth_pcm("hello", "en-US-AriaNeural")
    assert sr == 24000 and len(pcm) > 0


def test_synth_pcm_empty_text_is_silent(monkeypatch):
    called = []
    monkeypatch.setattr(edge, "_collect_mp3", lambda *a: called.append(1) or (b"", False))
    assert EdgeUnderTest().synth_pcm("   ") == (b"", 24000)
    assert not called                        # never hit the endpoint for empty text


def test_synth_pcm_falls_back_on_endpoint_error(monkeypatch):
    def _boom(*a):
        raise RuntimeError("endpoint blocked")
    monkeypatch.setattr(edge, "_collect_mp3", _boom)
    fb = _FakeFallback()
    pcm, sr = EdgeUnderTest(fallback=fb).synth_pcm("hi", "en-US-AriaNeural")
    assert (pcm, sr) == (b"FALLBACK", 22050)
    assert fb.synthed == ["hi"]              # fallback got the text, single-voice (no voice_id)


def test_synth_pcm_reraises_without_fallback(monkeypatch):
    def _boom(*a):
        raise RuntimeError("endpoint blocked")
    monkeypatch.setattr(edge, "_collect_mp3", _boom)
    with pytest.raises(RuntimeError):
        EdgeUnderTest().synth_pcm("hi")      # no fallback -> propagate; CastSynth degrades to silence


def test_synth_pcm_no_audio_is_an_error(monkeypatch):
    monkeypatch.setattr(edge, "_collect_mp3", lambda *a: (b"", False))
    fb = _FakeFallback()
    assert EdgeUnderTest(fallback=fb).synth_pcm("hi")[0] == b"FALLBACK"  # empty audio -> fallback


# ---- speak (playback + cancellation) ---------------------------------------
def test_speak_cancelled_during_synth_plays_nothing(monkeypatch):
    # _collect_mp3 signals it was cancelled mid-stream -> speak must not open any device.
    monkeypatch.setattr(edge, "_collect_mp3", lambda text, voice, cancel, rate=None: (_mp3_fixture(), True))
    played = []
    monkeypatch.setattr(edge.EdgeTTS, "_play_direct",
                        lambda self, pcm, sr, cancel: played.append(pcm))
    EdgeUnderTest().speak("hi", threading.Event())
    assert not played


def test_speak_plays_decoded_pcm(monkeypatch):
    monkeypatch.setattr(edge, "_collect_mp3", lambda text, voice, cancel, rate=None: (_mp3_fixture(), False))
    played = []
    monkeypatch.setattr(edge.EdgeTTS, "_play_direct",
                        lambda self, pcm, sr, cancel: played.append((len(pcm), sr)))
    EdgeUnderTest().speak("hi", threading.Event())
    assert played and played[0][1] == 24000 and played[0][0] > 0


def test_speak_falls_back_on_endpoint_error(monkeypatch):
    def _boom(*a):
        raise RuntimeError("endpoint blocked")
    monkeypatch.setattr(edge, "_collect_mp3", _boom)
    fb = _FakeFallback()
    EdgeUnderTest(fallback=fb).speak("hello there", threading.Event())
    assert fb.spoke == ["hello there"]


def test_speak_empty_text_noop(monkeypatch):
    monkeypatch.setattr(edge, "_collect_mp3", lambda *a: (_ for _ in ()).throw(AssertionError()))
    EdgeUnderTest().speak("  ", threading.Event())   # returns before ever synthesizing


def test_play_via_mixer_feeds_and_finishes(monkeypatch):
    monkeypatch.setattr(edge, "_collect_mp3", lambda text, voice, cancel, rate=None: (_mp3_fixture(), False))
    sink = _FakeSink()
    e = EdgeUnderTest(mixer=_FakeMixer(sink))
    e.speak("hi", threading.Event())
    assert sink.fed and sink.finished and not sink.cancelled


def test_play_via_mixer_cancel_aborts(monkeypatch):
    monkeypatch.setattr(edge, "_collect_mp3", lambda text, voice, cancel, rate=None: (_mp3_fixture(), False))
    sink = _FakeSink()
    cancel = threading.Event()
    cancel.set()                              # already cancelled -> first chunk aborts
    EdgeUnderTest(mixer=_FakeMixer(sink)).speak("hi", cancel)
    assert sink.cancelled and not sink.finished


# ---- list_voices fails soft ------------------------------------------------
def test_list_voices_fails_soft_to_empty(monkeypatch):
    def _boom(prefix="en-"):
        raise RuntimeError("no endpoint")
    monkeypatch.setattr(edge, "list_edge_voices", _boom)
    assert EdgeUnderTest().list_voices() == []


# ---- cast registry integration ---------------------------------------------
def test_edge_is_cast_eligible_via_registry(monkeypatch):
    monkeypatch.setattr(edge, "_collect_mp3", lambda text, voice, cancel, rate=None: (_mp3_fixture(), False))
    e = EdgeUnderTest()
    cs = CastSynth(el_synth=None, piper_loader=None)
    cs.registry.register("edge", lambda text, ref: e.synth_pcm(text, ref or None))
    pcm, sr = cs(Voice("edge", "en-GB-RyanNeural"), "hello")
    assert sr == 24000 and len(pcm) > 0


def test_edge_cast_voice_fails_soft_to_silence(monkeypatch):
    # A cast Edge provider has NO fallback: an endpoint error must degrade the NPC line to SILENCE,
    # never crash the loop (CastSynth swallows the error).
    monkeypatch.setattr(edge, "_collect_mp3",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("blocked")))
    e = EdgeUnderTest()
    cs = CastSynth(el_synth=None, piper_loader=None)
    cs.registry.register("edge", lambda text, ref: e.synth_pcm(text, ref or None))
    assert cs(Voice("edge", "en-GB-RyanNeural"), "hello") == (b"", 16000)


# ---- test doubles ----------------------------------------------------------
def EdgeUnderTest(**kw):
    """Build an EdgeTTS with a default [edge].voice, letting per-test kwargs (mixer/fallback) win."""
    return edge.EdgeTTS({"edge": {"voice": "en-US-AriaNeural"}}, **kw)


class _FakeSink:
    def __init__(self) -> None:
        self.fed = bytearray()
        self.finished = False
        self.cancelled = False

    def feed(self, pcm: bytes) -> None:
        self.fed += pcm

    def finish(self) -> None:
        self.finished = True

    def cancel(self) -> None:
        self.cancelled = True

    def wait(self, timeout=None) -> bool:
        return True                           # drains instantly in the test


class _FakeMixer:
    def __init__(self, sink: _FakeSink) -> None:
        self._sink = sink

    def open_speech(self, bus: str, sr: int) -> _FakeSink:
        return self._sink


# ---- opt-in integration (real, free, no-key Edge endpoint) -----------------
@pytest.mark.integration
@pytest.mark.local
def test_live_edge_synth_pcm_returns_audio():
    """One real edge-tts call: proves the request/decode shape against the live endpoint (a canary
    if Microsoft rotates the anti-abuse tokens and edge-tts breaks)."""
    e = edge.EdgeTTS({"edge": {"voice": "en-US-AriaNeural"}})
    pcm, sr = e.synth_pcm("Docking request granted, Commander.")
    assert sr == 24000 and len(pcm) > 1000


@pytest.mark.integration
@pytest.mark.local
def test_live_edge_lists_english_voices():
    """The live catalog has many English voices for cast assignment."""
    voices = edge.list_edge_voices("en-")
    assert len(voices) > 10
    assert all(v["ref"] and v["gender"] in ("male", "female", "neutral") for v in voices)
    assert any(v["gender"] == "male" for v in voices)
    assert any(v["gender"] == "female" for v in voices)
