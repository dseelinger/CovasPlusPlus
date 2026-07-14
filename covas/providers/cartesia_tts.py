"""Cartesia (Sonic) low-latency persona voice provider (issue #18).

A **snappier, premium alternative to ElevenLabs for the COVAS persona** — Cartesia's Sonic models
are built for very low time-to-first-audio, which is what a live voice companion feels most. Unlike
the cast providers (Edge/Azure/OpenAI, #15–#16/#17), this one's whole value is **latency**, so `speak`
**streams chunks to the output as they arrive** (like the ElevenLabs path in `tts.py`) rather than
collecting the whole clip first. It's registered as **persona-eligible** (via `make_tts`) — not a
cast backend, since it's premium and its value is the live reply, not background chatter.

(Deepgram **Aura** is the documented alternative; Cartesia Sonic was picked to start per the issue.)

Uses the Cartesia REST endpoint via `requests` (already a dep — no SDK). We request a `raw` /
`pcm_s16le` output format, so the streamed bytes ARE the raw 16-bit mono PCM the audio/cancel path
expects (no decode step). Needs a key (`CARTESIA_API_KEY` env or `[cartesia].api_key_file`) and a
`voice` id. Fail soft: no key / service error makes the persona degrade to text — never crashes.
"""
from __future__ import annotations

import json as _json
import threading
from typing import Iterator

import requests

_SAMPLE_RATE = 24000
_USER_AGENT = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion)"
_DEFAULT_BASE_URL = "https://api.cartesia.ai"
_DEFAULT_MODEL = "sonic-2"
_DEFAULT_LANGUAGE = "en"
# Cartesia pins its API behind a dated version header.
_CARTESIA_VERSION = "2024-11-13"
# Small stream chunk keeps time-to-first-audio (and cancel latency) low.
_STREAM_CHUNK = 4096


class CartesiaTTS:
    """TTSProvider over Cartesia Sonic. `voice` is a Cartesia voice id (required); `model` the Sonic
    model. Streams for low latency; the mixer path mirrors the ElevenLabs streaming path."""

    def __init__(self, cfg: dict, *, mixer=None, bus: str = "covas") -> None:  # noqa: ANN001
        self._cfg = cfg
        self._mixer = mixer
        self._bus = bus
        c = cfg.get("cartesia", {}) or {}
        self._base_url = str(c.get("base_url", "")).strip().rstrip("/") or _DEFAULT_BASE_URL
        self._model = str(c.get("model", "")).strip() or _DEFAULT_MODEL
        self._voice = str(c.get("voice", "")).strip()
        self._language = str(c.get("language", "")).strip() or _DEFAULT_LANGUAGE
        self._out_device = cfg.get("audio", {}).get("tts_output_device") or None

    def _key(self) -> str:
        from ..firstrun import cartesia_key
        key = cartesia_key(self._cfg)
        if not key:
            raise RuntimeError(
                "Cartesia TTS selected but no key found (set CARTESIA_API_KEY or "
                "[cartesia].api_key_file)."
            )
        return key

    def _body(self, text: str, voice_id: str | None = None) -> dict:
        voice = (voice_id or "").strip() or self._voice
        return {
            "model_id": self._model,
            "transcript": text,
            "voice": {"mode": "id", "id": voice},
            "output_format": {"container": "raw", "encoding": "pcm_s16le",
                              "sample_rate": _SAMPLE_RATE},
            "language": self._language,
        }

    # ---- synthesis --------------------------------------------------------
    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        """Collect the full synthesis to raw 16-bit mono PCM (used for cached/status lines, where
        streaming buys nothing). Raises on a service/config error."""
        text = (text or "").strip()
        if not text:
            return b"", _SAMPLE_RATE
        buf = bytearray()
        for chunk in _iter_pcm_chunks(self._key(), self._base_url, self._body(text, voice_id)):
            buf += chunk
        if not buf:
            raise RuntimeError("Cartesia TTS returned no audio")
        return bytes(buf), _SAMPLE_RATE

    def speak(self, text: str, cancel: threading.Event) -> None:
        """STREAM `text` to the output, playing chunks as they arrive for low time-to-first-audio,
        stopping promptly if `cancel` is set. Re-raises on a service/config error so the caller
        degrades to text — never crashes the loop."""
        text = (text or "").strip()
        if not text:
            return
        key = self._key()
        body = self._body(text)
        if self._mixer is not None:
            self._stream_to_mixer(key, body, cancel)
        else:
            self._stream_to_device(key, body, cancel)

    # ---- streaming playback ----------------------------------------------
    def _stream_to_device(self, key: str, body: dict, cancel: threading.Event) -> None:
        import sounddevice as sd

        stream = sd.RawOutputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="int16",
                                    device=self._out_device)
        stream.start()
        leftover = b""
        cancelled = False
        try:
            for chunk in _iter_pcm_chunks(key, self._base_url, body):
                if cancel.is_set():
                    cancelled = True
                    break
                data, leftover = _whole_samples(leftover + chunk)
                if data:
                    stream.write(data)
        finally:
            if cancelled:
                stream.abort()   # drop buffered audio -> stops immediately
            else:
                stream.stop()
            stream.close()

    def _stream_to_mixer(self, key: str, body: dict, cancel: threading.Event) -> None:
        """Feed streamed PCM into the shared BusMixer (C9): same barge-in + drain-until-done as the
        ElevenLabs streaming path."""
        sink = self._mixer.open_speech(self._bus, _SAMPLE_RATE)
        leftover = b""
        cancelled = False
        try:
            for chunk in _iter_pcm_chunks(key, self._base_url, body):
                if cancel.is_set():
                    cancelled = True
                    break
                data, leftover = _whole_samples(leftover + chunk)
                if data:
                    sink.feed(data)
        finally:
            if cancelled:
                sink.cancel()
            else:
                sink.finish()
                while not sink.wait(0.1):
                    if cancel.is_set():
                        sink.cancel()
                        break

    # ---- voice catalog ----------------------------------------------------
    def list_voices(self, language_prefix: str = "en") -> list[dict]:
        """The Cartesia voice catalog for reference/help, filtered to `language_prefix` (blank =
        all). Each entry: {'ref': id, 'name', 'gender', 'locale': language}. Fails soft to []."""
        try:
            return list_cartesia_voices(self._key(), self._base_url, language_prefix)
        except Exception:  # noqa: BLE001 — no catalog if unreachable/unconfigured
            return []


# ---- module helpers -------------------------------------------------------
def _whole_samples(data: bytes) -> tuple[bytes, bytes]:
    """Split a byte buffer into (whole 16-bit samples, trailing leftover byte). Keeps a stray odd
    byte across streamed chunks so a sample is never split mid-write."""
    if len(data) % 2:
        return data[:-1], data[-1:]
    return data, b""


def _headers(key: str) -> dict:
    return {
        "X-API-Key": key,
        "Cartesia-Version": _CARTESIA_VERSION,
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }


def _iter_pcm_chunks(key: str, base_url: str, body: dict,
                     *, timeout: float = 30.0) -> Iterator[bytes]:
    """Stream raw PCM chunks from Cartesia `/tts/bytes` as they arrive (low latency). Raises
    RuntimeError on a non-200 response."""
    url = f"{base_url}/tts/bytes"
    with requests.post(url, data=_json.dumps(body), headers=_headers(key),
                       stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Cartesia TTS {r.status_code}: {r.text[:200]}")
        for chunk in r.iter_content(chunk_size=_STREAM_CHUNK):
            if chunk:
                yield chunk


def _gender(raw: str) -> str:
    g = str(raw or "").strip().lower()
    return g if g in ("male", "female") else "neutral"


def _normalize_voices(raw, language_prefix: str = "en") -> list[dict]:  # noqa: ANN001
    """Pure: normalize a Cartesia `/voices` payload (a list, or a {'data': [...]} envelope) to the
    reference shape, filtered to `language_prefix` (blank = all), sorted by name."""
    items = raw.get("data", []) if isinstance(raw, dict) else (raw or [])
    out: list[dict] = []
    for v in items:
        vid = str((v or {}).get("id", "")).strip()
        if not vid:
            continue
        lang = str(v.get("language", "")).strip()
        if language_prefix and not lang.startswith(language_prefix):
            continue
        out.append({
            "ref": vid,
            "name": str(v.get("name", "")).strip() or vid,
            "gender": _gender(v.get("gender", "")),
            "locale": lang,
        })
    out.sort(key=lambda d: d["name"])
    return out


def list_cartesia_voices(key: str, base_url: str, language_prefix: str = "en",
                         *, timeout: float = 15.0) -> list[dict]:
    """Fetch (network) + normalize the Cartesia voice catalog. See _normalize_voices/list_voices."""
    r = requests.get(f"{base_url}/voices", headers=_headers(key), timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"Cartesia voices {r.status_code}: {r.text[:200]}")
    return _normalize_voices(r.json(), language_prefix)
