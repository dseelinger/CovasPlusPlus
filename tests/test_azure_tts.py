"""Tests for the official Azure Neural TTS provider (issue #17).

Default (unit) tests are OFFLINE and FREE: the network lives in two request helpers
(`_collect_pcm` / `list_azure_voices`), which the tests monkeypatch. Azure returns raw 16-bit PCM
directly (no decode). The `@pytest.mark.integration` `paid` test at the bottom hits the real service
and is skipped unless a key + region are exported.
"""
from __future__ import annotations

import os
import threading

import pytest

from covas import firstrun
from covas.mixer import CastSynth, Voice
from covas.providers import azure_tts as az


# ---- test doubles ----------------------------------------------------------
def _azure(monkeypatch=None, *, key="test-key", **azure_cfg):
    """Build an AzureTTS with region/voice defaults; when a key is given, patch the firstrun
    resolver so `_key()` resolves without a real service (keys are file-only/DPAPI now, not env
    vars). Extra kwargs land in the [azure] config table."""
    cfg = {"azure": {"region": "eastus", "voice": "en-US-AriaNeural", **azure_cfg}}
    if monkeypatch is not None and key is not None:
        monkeypatch.setattr(firstrun, "azure_key", lambda cfg: key)
    return az.AzureTTS(cfg)


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


# ---- pure builders ---------------------------------------------------------
def test_lang_of_derives_bcp47():
    assert az._lang_of("en-US-AriaNeural") == "en-US"
    assert az._lang_of("de-DE-KatjaNeural") == "de-DE"
    assert az._lang_of("weird") == "en-US"          # fallback


def test_build_ssml_escapes_text_and_sets_voice_and_lang():
    ssml = az._build_ssml("Fuel < 25% & rising", "en-GB-RyanNeural")
    assert "name='en-GB-RyanNeural'" in ssml
    assert "xml:lang='en-GB'" in ssml
    assert "Fuel &lt; 25% &amp; rising" in ssml       # XML-escaped
    assert "express-as" not in ssml                   # no style -> no wrapper


def test_build_ssml_wraps_style_when_set():
    ssml = az._build_ssml("hi", "en-US-AriaNeural", style="cheerful")
    assert "<mstts:express-as style='cheerful'>hi</mstts:express-as>" in ssml
    assert "xmlns:mstts=" in ssml                      # namespace declared


def test_normalize_voices_filters_sorts_and_names():
    raw = [
        {"ShortName": "en-US-AriaNeural", "DisplayName": "Aria", "Gender": "Female",
         "Locale": "en-US"},
        {"ShortName": "en-GB-RyanNeural", "DisplayName": "Ryan", "Gender": "Male",
         "Locale": "en-GB"},
        {"ShortName": "fr-FR-DeniseNeural", "DisplayName": "Denise", "Gender": "Female",
         "Locale": "fr-FR"},
        {"ShortName": "", "Locale": "en-US"},          # dropped
    ]
    voices = az._normalize_voices(raw, "en-")
    assert [v["ref"] for v in voices] == ["en-GB-RyanNeural", "en-US-AriaNeural"]  # sorted, en-only
    assert voices[0]["gender"] == "male" and voices[1]["gender"] == "female"
    assert voices[1]["name"] == "Aria"
    assert len(az._normalize_voices(raw, "")) == 3     # blank prefix keeps all real entries


# ---- synth_pcm -------------------------------------------------------------
def test_synth_pcm_returns_pcm_and_rate(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda key, region, ssml, cancel: (b"\x01\x02" * 100, False))
    pcm, sr = _azure(monkeypatch).synth_pcm("hello", "en-US-JennyNeural")
    assert sr == 24000 and pcm == b"\x01\x02" * 100


def test_synth_pcm_passes_voice_and_style_into_ssml(monkeypatch):
    seen = {}
    def _fake(key, region, ssml, cancel):
        seen["ssml"] = ssml
        return b"PCM", False
    monkeypatch.setattr(az, "_collect_pcm", _fake)
    _azure(monkeypatch, style="newscast").synth_pcm("report", "en-US-GuyNeural")
    assert "name='en-US-GuyNeural'" in seen["ssml"]
    assert "style='newscast'" in seen["ssml"]


def test_synth_pcm_empty_text_is_silent(monkeypatch):
    called = []
    monkeypatch.setattr(az, "_collect_pcm", lambda *a: called.append(1) or (b"", False))
    assert _azure(monkeypatch).synth_pcm("   ") == (b"", 24000)
    assert not called                                  # never hit the service for empty text


def test_synth_pcm_no_key_raises(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda *a: (b"PCM", False))  # never reached
    with pytest.raises(RuntimeError):
        _azure(monkeypatch, key=None).synth_pcm("hi")  # no key -> raise; CastSynth degrades to silence


def test_synth_pcm_no_audio_raises(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda *a: (b"", False))
    with pytest.raises(RuntimeError):
        _azure(monkeypatch).synth_pcm("hi")


# ---- speak (playback + cancellation) ---------------------------------------
def test_speak_cancelled_during_synth_plays_nothing(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda key, region, ssml, cancel: (b"PCM" * 50, True))
    played = []
    monkeypatch.setattr(az.AzureTTS, "_play_direct", lambda self, pcm, sr, cancel: played.append(pcm))
    _azure(monkeypatch).speak("hi", threading.Event())
    assert not played


def test_speak_plays_pcm(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda key, region, ssml, cancel: (b"\x00\x01" * 500, False))
    played = []
    monkeypatch.setattr(az.AzureTTS, "_play_direct",
                        lambda self, pcm, sr, cancel: played.append((len(pcm), sr)))
    _azure(monkeypatch).speak("hi", threading.Event())
    assert played and played[0][1] == 24000 and played[0][0] > 0


def test_speak_empty_text_noop(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda *a: (_ for _ in ()).throw(AssertionError()))
    _azure(monkeypatch).speak("  ", threading.Event())


def test_speak_no_key_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        _azure(monkeypatch, key=None).speak("hi", threading.Event())


def test_play_via_mixer_feeds_and_finishes(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda key, region, ssml, cancel: (b"\x00\x01" * 500, False))
    sink = _FakeSink()
    monkeypatch.setattr(firstrun, "azure_key", lambda cfg: "k")
    e = az.AzureTTS({"azure": {"region": "eastus", "voice": "en-US-AriaNeural"}}, mixer=_FakeMixer(sink))
    e.speak("hi", threading.Event())
    assert sink.fed and sink.finished and not sink.cancelled


def test_play_via_mixer_cancel_aborts(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda key, region, ssml, cancel: (b"\x00\x01" * 500, False))
    sink = _FakeSink()
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(firstrun, "azure_key", lambda cfg: "k")
    az.AzureTTS({"azure": {"region": "eastus", "voice": "en-US-AriaNeural"}},
                mixer=_FakeMixer(sink)).speak("hi", cancel)
    assert sink.cancelled and not sink.finished


# ---- list_voices fails soft ------------------------------------------------
def test_list_voices_fails_soft_to_empty(monkeypatch):
    assert _azure(monkeypatch, key=None).list_voices() == []   # no key -> [] (never raises)


def test_list_voices_returns_catalog(monkeypatch):
    monkeypatch.setattr(az, "list_azure_voices",
                        lambda key, region, prefix="en-": [{"ref": "en-US-AriaNeural"}])
    assert _azure(monkeypatch).list_voices() == [{"ref": "en-US-AriaNeural"}]


# ---- cast registry integration ---------------------------------------------
def test_azure_is_cast_eligible_via_registry(monkeypatch):
    monkeypatch.setattr(az, "_collect_pcm", lambda key, region, ssml, cancel: (b"\x02\x03" * 100, False))
    e = _azure(monkeypatch)
    cs = CastSynth(el_synth=None, piper_loader=None)
    cs.registry.register("azure", lambda text, ref: e.synth_pcm(text, ref or None))
    pcm, sr = cs(Voice("azure", "en-GB-RyanNeural"), "hello")
    assert sr == 24000 and len(pcm) > 0


def test_azure_cast_voice_fails_soft_to_silence(monkeypatch):
    # No key -> synth_pcm raises -> CastSynth swallows it -> the NPC line degrades to silence.
    e = _azure(monkeypatch, key=None)
    cs = CastSynth(el_synth=None, piper_loader=None)
    cs.registry.register("azure", lambda text, ref: e.synth_pcm(text, ref or None))
    assert cs(Voice("azure", "en-GB-RyanNeural"), "hello") == (b"", 16000)


# ---- opt-in integration (real Azure service; needs a key + region) ---------
@pytest.mark.integration
@pytest.mark.paid
def test_live_azure_synth_pcm_returns_audio():
    """One real Azure call. Needs a Speech resource: export AZURE_SPEECH_KEY (+ AZURE_SPEECH_REGION,
    default eastus). Skipped when unset so the paid suite stays deliberate."""
    key = os.environ.get("AZURE_SPEECH_KEY")
    if not key:
        pytest.skip("set AZURE_SPEECH_KEY (+ AZURE_SPEECH_REGION) to run the live Azure test")
    region = os.environ.get("AZURE_SPEECH_REGION", "eastus")
    e = az.AzureTTS({"azure": {"region": region, "voice": "en-US-AriaNeural"}})
    pcm, sr = e.synth_pcm("Docking request granted, Commander.")
    assert sr == 24000 and len(pcm) > 1000
