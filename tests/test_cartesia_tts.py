"""Tests for the Cartesia (Sonic) low-latency persona voice provider (issue #18).

Default (unit) tests are OFFLINE and FREE: the network lives in `_iter_pcm_chunks` /
`list_cartesia_voices`, which the tests monkeypatch. Cartesia streams raw `pcm_s16le`, so the chunks
ARE the PCM (no decode). The `@pytest.mark.integration` `paid` test at the bottom hits the real
service and skips unless a key + voice id are set.
"""
from __future__ import annotations

import os
import threading

import pytest

from covas.providers import cartesia_tts as cart
from covas import firstrun
from covas import settings_schema as ss


# ---- test doubles ----------------------------------------------------------
def _cartesia(monkeypatch=None, *, key="test-key", **cfg):
    """Build a CartesiaTTS with a voice id; when a key is given, patch the firstrun resolver so
    `_key()` resolves without a real service (keys are file-only/DPAPI now, not env vars). Extra
    kwargs land in the [cartesia] config table."""
    if monkeypatch is not None and key is not None:
        monkeypatch.setattr(firstrun, "cartesia_key", lambda cfg: key)
    return cart.CartesiaTTS({"cartesia": {"voice": "vid-123", **cfg}})


def _chunks(*parts):
    """A fake _iter_pcm_chunks that yields the given byte parts (ignores its args)."""
    def _gen(key, base_url, body, *, timeout=30.0):
        for p in parts:
            yield p
    return _gen


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


# ---- pure helpers ----------------------------------------------------------
def test_whole_samples_keeps_odd_byte_leftover():
    assert cart._whole_samples(b"\x01\x02\x03") == (b"\x01\x02", b"\x03")
    assert cart._whole_samples(b"\x01\x02") == (b"\x01\x02", b"")


def test_body_shapes_cartesia_request(monkeypatch):
    body = _cartesia(monkeypatch, model="sonic-2", language="en")._body("hello", "override-voice")
    assert body["model_id"] == "sonic-2"
    assert body["transcript"] == "hello"
    assert body["voice"] == {"mode": "id", "id": "override-voice"}   # per-call voice wins
    assert body["output_format"] == {"container": "raw", "encoding": "pcm_s16le",
                                     "sample_rate": 24000}
    assert body["language"] == "en"


def test_body_uses_configured_voice_when_none(monkeypatch):
    assert _cartesia(monkeypatch)._body("hi")["voice"]["id"] == "vid-123"


def test_normalize_voices_handles_list_and_envelope():
    raw = {"data": [
        {"id": "v2", "name": "Bravo", "gender": "male", "language": "en"},
        {"id": "v1", "name": "Alpha", "gender": "female", "language": "en"},
        {"id": "v3", "name": "Fr", "gender": "female", "language": "fr"},
        {"id": "", "name": "skip", "language": "en"},
    ]}
    voices = cart._normalize_voices(raw, "en")
    assert [v["name"] for v in voices] == ["Alpha", "Bravo"]      # sorted by name, en-only
    assert voices[0]["ref"] == "v1" and voices[0]["gender"] == "female"
    assert cart._normalize_voices(raw["data"], "") and len(cart._normalize_voices(raw["data"], "")) == 3


# ---- synth_pcm -------------------------------------------------------------
def test_synth_pcm_joins_streamed_chunks(monkeypatch):
    monkeypatch.setattr(cart, "_iter_pcm_chunks", _chunks(b"\x01\x02", b"\x03\x04"))
    pcm, sr = _cartesia(monkeypatch).synth_pcm("hello")
    assert sr == 24000 and pcm == b"\x01\x02\x03\x04"


def test_synth_pcm_empty_text_is_silent(monkeypatch):
    monkeypatch.setattr(cart, "_iter_pcm_chunks", _chunks(b"XX"))
    assert _cartesia(monkeypatch).synth_pcm("   ") == (b"", 24000)


def test_synth_pcm_no_audio_raises(monkeypatch):
    monkeypatch.setattr(cart, "_iter_pcm_chunks", _chunks())   # yields nothing
    with pytest.raises(RuntimeError):
        _cartesia(monkeypatch).synth_pcm("hi")


def test_synth_pcm_no_key_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        _cartesia(monkeypatch, key=None).synth_pcm("hi")


# ---- speak (STREAMING playback + cancellation) -----------------------------
def test_speak_streams_whole_samples_to_mixer(monkeypatch):
    # Odd-length chunks must be reassembled into whole 16-bit samples across the stream.
    monkeypatch.setattr(cart, "_iter_pcm_chunks", _chunks(b"\x01\x02\x03", b"\x04"))
    sink = _FakeSink()
    monkeypatch.setattr(firstrun, "cartesia_key", lambda cfg: "k")
    cart.CartesiaTTS({"cartesia": {"voice": "v"}}, mixer=_FakeMixer(sink)).speak("hi", threading.Event())
    assert bytes(sink.fed) == b"\x01\x02\x03\x04" and sink.finished and not sink.cancelled


def test_speak_cancel_aborts_mixer(monkeypatch):
    monkeypatch.setattr(cart, "_iter_pcm_chunks", _chunks(b"\x00\x01" * 100))
    sink = _FakeSink()
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(firstrun, "cartesia_key", lambda cfg: "k")
    cart.CartesiaTTS({"cartesia": {"voice": "v"}}, mixer=_FakeMixer(sink)).speak("hi", cancel)
    assert sink.cancelled and not sink.finished


def test_speak_device_path_consumes_without_error(monkeypatch):
    # No mixer -> the device path (sounddevice is stubbed to a null stream by conftest).
    monkeypatch.setattr(cart, "_iter_pcm_chunks", _chunks(b"\x00\x01" * 50))
    _cartesia(monkeypatch).speak("hi", threading.Event())   # must not raise


def test_speak_empty_text_noop(monkeypatch):
    monkeypatch.setattr(cart, "_iter_pcm_chunks",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    _cartesia(monkeypatch).speak("   ", threading.Event())


def test_speak_no_key_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        _cartesia(monkeypatch, key=None).speak("hi", threading.Event())


# ---- voice catalog ---------------------------------------------------------
def test_list_voices_fails_soft_to_empty(monkeypatch):
    assert _cartesia(monkeypatch, key=None).list_voices() == []


def test_list_voices_returns_catalog(monkeypatch):
    monkeypatch.setattr(cart, "list_cartesia_voices",
                        lambda key, base, prefix="en": [{"ref": "v1", "name": "Alpha"}])
    assert _cartesia(monkeypatch).list_voices() == [{"ref": "v1", "name": "Alpha"}]


# ---- persona-only (NOT a cast provider) ------------------------------------
def test_cartesia_is_persona_only_not_cast():
    assert "cartesia" in ss.TTS_PROVIDERS       # selectable as the COVAS persona voice
    assert "cartesia" not in ss.CAST_PROVIDERS  # deliberately NOT offered for the cast


# ---- opt-in integration (real Cartesia service; needs a key + voice id) ----
@pytest.mark.integration
@pytest.mark.paid
def test_live_cartesia_synth_pcm_returns_audio():
    """One real Cartesia call. Needs CARTESIA_API_KEY + CARTESIA_VOICE_ID exported; skipped
    otherwise so the paid suite stays deliberate."""
    key = os.environ.get("CARTESIA_API_KEY")
    voice = os.environ.get("CARTESIA_VOICE_ID")
    if not (key and voice):
        pytest.skip("set CARTESIA_API_KEY + CARTESIA_VOICE_ID to run the live Cartesia test")
    e = cart.CartesiaTTS({"cartesia": {"voice": voice, "model": "sonic-2"}})
    pcm, sr = e.synth_pcm("Docking request granted, Commander.")
    assert sr == 24000 and len(pcm) > 1000
