"""OpenAI-compatible TTS provider (issue #16).

A **cheap cloud voice** for the persona or as a supplemental cast voice, over the OpenAI
`audio/speech` API. `base_url` is configurable, so any OpenAI-compatible endpoint (OpenAI itself,
a proxy, a local server) works with the same implementation — one key file (`[openai_tts]` /
`[openai].api_key_file`, DPAPI-encrypted) is shared with the OpenAI LLM provider.

Uses REST via `requests` (already a dep — no OpenAI SDK). We request `response_format = "pcm"`, which
OpenAI returns as raw 24 kHz 16-bit mono little-endian PCM — exactly the shape the audio/cancel path
expects, so there's no decode step (like Azure #17, unlike Edge's MP3). Streaming with prompt
cancellation keeps tap-cancel/barge-in snappy; the mixer path mirrors Piper's.

Modest voice count vs Edge/Azure (a fixed set), so it shines as a cheap persona voice. Fail soft: no
key or a service error makes the persona degrade to text and cast voices fall silent — never crashes.
"""
from __future__ import annotations

import json as _json
import threading
from typing import Optional

import requests

# OpenAI `pcm` response is raw 24 kHz, 16-bit signed, mono little-endian.
_SAMPLE_RATE = 24000
_USER_AGENT = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion)"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini-tts"
_DEFAULT_VOICE = "alloy"
# The built-in OpenAI voice names (there is no voices/list endpoint). Used for cast assignment.
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable",
                 "nova", "onyx", "sage", "shimmer", "verse")
# Playback chunk: ~85 ms at 24 kHz (2048 samples * 2 bytes) — small enough for a snappy cancel.
_PLAY_CHUNK = 4096


class OpenAITTS:
    """TTSProvider over an OpenAI-compatible `audio/speech` endpoint. `voice` is a voice name (e.g.
    'alloy'); `model` the TTS model; optional `instructions` steer tone/delivery (honored by newer
    models like gpt-4o-mini-tts, ignored by older ones). Key + base_url come from config/env."""

    def __init__(self, cfg: dict, *, mixer=None, bus: str = "covas") -> None:  # noqa: ANN001
        self._cfg = cfg
        self._mixer = mixer
        self._bus = bus
        o = cfg.get("openai_tts", {}) or {}
        self._base_url = str(o.get("base_url", "")).strip().rstrip("/") or _DEFAULT_BASE_URL
        self._model = str(o.get("model", "")).strip() or _DEFAULT_MODEL
        self._voice = str(o.get("voice", "")).strip() or _DEFAULT_VOICE
        self._instructions = str(o.get("instructions", "")).strip()
        self._out_device = cfg.get("audio", {}).get("tts_output_device") or None

    def _key(self) -> str:
        """Resolve the OpenAI key from its (DPAPI-encrypted) key file. Raises a clear error if
        unconfigured — callers fail soft (persona -> text, cast -> silence)."""
        from ..firstrun import openai_key
        key = openai_key(self._cfg)
        if not key:
            raise RuntimeError(
                "OpenAI TTS selected but no key found (add it in Settings, or to [openai_tts].api_key_file)."
            )
        return key

    # ---- synthesis --------------------------------------------------------
    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        """Synthesize `text` in voice `voice_id` (name; None/'' = the configured voice) to raw 16-bit
        mono PCM. Raises on a service/config error (CastSynth catches it -> silence; persona ->
        text)."""
        text = (text or "").strip()
        if not text:
            return b"", _SAMPLE_RATE
        voice = (voice_id or "").strip() or self._voice
        body = self._body(text, voice)
        pcm, _ = _collect_pcm(self._key(), self._base_url, body, None)
        if not pcm:
            raise RuntimeError("OpenAI TTS returned no audio")
        return pcm, _SAMPLE_RATE

    def speak(self, text: str, cancel: threading.Event) -> None:
        """Synthesize + play `text`, stopping promptly if `cancel` is set. Re-raises on a
        service/config error so the caller degrades to text — never crashes the loop."""
        text = (text or "").strip()
        if not text:
            return
        body = self._body(text, self._voice)
        pcm, cancelled = _collect_pcm(self._key(), self._base_url, body, cancel)
        if cancelled or not pcm:
            return  # barged in during synth, or no audio -> nothing to play
        if self._mixer is not None:
            self._play_via_mixer(pcm, _SAMPLE_RATE, cancel)
        else:
            self._play_direct(pcm, _SAMPLE_RATE, cancel)

    def _body(self, text: str, voice: str) -> dict:
        """The `audio/speech` request body. `instructions` and `speed` are included only when set /
        non-default (older models ignore instructions; omitting `speed` at 1.0 keeps the request
        minimal for maximum endpoint compatibility)."""
        body = {"model": self._model, "voice": voice, "input": text, "response_format": "pcm"}
        if self._instructions:
            body["instructions"] = self._instructions
        from .. import tts_speed
        n = tts_speed.normalized_speed(self._cfg)
        if not tts_speed.is_default(n):
            # OpenAI `speed` is a 1.0=normal multiplier (0.25–4.0); the normalized #99 value maps
            # straight in, clamped to that native range.
            body["speed"] = tts_speed.openai_speed(n)
        return body

    # ---- playback ---------------------------------------------------------
    def _play_direct(self, pcm: bytes, sr: int, cancel: threading.Event) -> None:
        import sounddevice as sd

        stream = sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16",
                                    device=self._out_device)
        stream.start()
        cancelled = False
        try:
            for i in range(0, len(pcm), _PLAY_CHUNK):
                if cancel.is_set():
                    cancelled = True
                    break
                stream.write(pcm[i:i + _PLAY_CHUNK])
        finally:
            if cancelled:
                stream.abort()   # drop buffered audio -> stops immediately
            else:
                stream.stop()
            stream.close()

    def _play_via_mixer(self, pcm: bytes, sr: int, cancel: threading.Event) -> None:
        """Feed the PCM into the shared BusMixer (C9): same barge-in + drain-until-done as Piper."""
        sink = self._mixer.open_speech(self._bus, sr)
        cancelled = False
        try:
            for i in range(0, len(pcm), _PLAY_CHUNK):
                if cancel.is_set():
                    cancelled = True
                    break
                sink.feed(pcm[i:i + _PLAY_CHUNK])
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
    def list_voices(self, locale_prefix: str = "") -> list[dict]:
        """The built-in OpenAI voices for cast assignment (there's no voices/list endpoint, so this
        is a static, gender-neutral catalog). `locale_prefix` is accepted for signature parity but
        ignored (voices aren't locale-tagged)."""
        return [{"ref": v, "name": v.capitalize(), "gender": "neutral", "locale": ""}
                for v in OPENAI_VOICES]


# ---- module helper (the network lives here) -------------------------------
def _collect_pcm(key: str, base_url: str, body: dict,
                 cancel: Optional[threading.Event], *, timeout: float = 30.0) -> tuple[bytes, bool]:
    """POST to `{base_url}/audio/speech` and stream the raw PCM back, returning (pcm, cancelled).
    Checks `cancel` between chunks so a barge-in stops the read promptly; a partial (cancelled)
    buffer is discarded by callers. Raises RuntimeError on a non-200 response."""
    url = f"{base_url}/audio/speech"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    buf = bytearray()
    with requests.post(url, data=_json.dumps(body), headers=headers,
                       stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI TTS {r.status_code}: {r.text[:200]}")
        for chunk in r.iter_content(chunk_size=_PLAY_CHUNK):
            if cancel is not None and cancel.is_set():
                return bytes(buf), True
            if chunk:
                buf += chunk
    return bytes(buf), False
