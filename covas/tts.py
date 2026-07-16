"""ElevenLabs streaming TTS -> low-latency PCM playback (instantly cancellable)."""
from __future__ import annotations
import threading

import requests
import sounddevice as sd

from . import tts_speed


def _api_key(cfg: dict) -> str:
    """The ElevenLabs key, via firstrun so it's DPAPI-aware (decrypts / migrates plaintext) rather
    than reading the file raw. Returns "" when unconfigured — the request then fails soft."""
    from .firstrun import elevenlabs_key
    return elevenlabs_key(cfg) or ""


def _speed(cfg: dict) -> float:
    """The ElevenLabs native `voice_settings.speed` for this config: the ONE normalized
    `[tts].speed` (issue #99) mapped into ElevenLabs' quality-safe 0.7–1.2 band. Widens the old
    1.0–1.2 cap so COVAS can now slow BELOW normal; a bad/out-of-range stored value is capped, never
    sent raw."""
    return tts_speed.elevenlabs_speed(tts_speed.normalized_speed(cfg))


def build_tts_body(cfg: dict, text: str) -> dict:
    """The ElevenLabs request body. `voice_settings.speed` is added ONLY when it differs from
    the default 1.0, so the default request is byte-for-byte what it was before (no risk of
    resetting the voice's other settings)."""
    body: dict = {"text": text, "model_id": cfg["elevenlabs"]["model"]}
    speed = _speed(cfg)
    if abs(speed - 1.0) > 1e-6:
        body["voice_settings"] = {"speed": speed}
    return body


def synth_pcm(cfg: dict, text: str, voice_id: str | None = None) -> bytes:
    """Synthesize `text` to raw PCM (16-bit mono 16 kHz) and return all bytes.
    Used to pre-generate + cache spoken status lines (not for live replies)."""
    el = cfg["elevenlabs"]
    vid = voice_id or el["voice_id"]
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream"
        f"?output_format=pcm_16000"
    )
    body = build_tts_body(cfg, text)
    headers = {"xi-api-key": _api_key(cfg), "content-type": "application/json"}
    r = requests.post(url, json=body, headers=headers, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs TTS {r.status_code}: {r.text[:200]}")
    return r.content


def speak(cfg: dict, text: str, cancel: threading.Event, *, open_sink=None) -> None:  # noqa: ANN001
    """Stream ElevenLabs TTS to playback. By default (open_sink=None) it opens its OWN
    low-latency device stream — the original behaviour. When `open_sink(sr) -> SpeechStream`
    is supplied (C9: the audio layer is enabled), it feeds the same chunks into the shared
    BusMixer instead, so the mixer is the single owner of the device. Barge-in via `cancel` is
    preserved on both paths (abort / stream.cancel drops buffered audio immediately)."""
    text = text.strip()
    if not text:
        return
    el = cfg["elevenlabs"]
    fmt = el.get("output_format", "pcm_16000")
    sr = int(fmt.split("_")[1]) if fmt.startswith("pcm_") else 16000
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{el['voice_id']}/stream"
        f"?output_format={fmt}"
    )
    body = build_tts_body(cfg, text)
    headers = {"xi-api-key": _api_key(cfg), "content-type": "application/json"}

    with requests.post(url, json=body, headers=headers, stream=True, timeout=30) as r:
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs TTS {r.status_code}: {r.text[:200]}")
        if open_sink is not None:
            _stream_to_sink(r, cancel, open_sink(sr))
            return
        stream = sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16")
        stream.start()
        leftover = b""
        cancelled = False
        try:
            for chunk in r.iter_content(chunk_size=2048):  # ~64 ms/chunk -> snappy cancel
                if cancel.is_set():
                    cancelled = True
                    break
                if not chunk:
                    continue
                data = leftover + chunk
                if len(data) % 2:            # keep 16-bit samples whole
                    leftover = data[-1:]
                    data = data[:-1]
                else:
                    leftover = b""
                if data:
                    stream.write(data)
        finally:
            if cancelled:
                stream.abort()              # drop buffered audio -> stops immediately
            else:
                stream.stop()
            stream.close()


def _stream_to_sink(r, cancel: threading.Event, sink) -> None:  # noqa: ANN001
    """Feed the streamed 16-bit PCM into a mixer SpeechStream, preserving whole samples and
    barge-in. After feeding, block until the mixer drains it — so the caller still returns only
    once playback is done — but bail out promptly (cancelling the stream) if the Commander barges
    in during that drain."""
    leftover = b""
    cancelled = False
    try:
        for chunk in r.iter_content(chunk_size=2048):
            if cancel.is_set():
                cancelled = True
                break
            if not chunk:
                continue
            data = leftover + chunk
            if len(data) % 2:
                leftover = data[-1:]
                data = data[:-1]
            else:
                leftover = b""
            if data:
                sink.feed(data)
    finally:
        if cancelled:
            sink.cancel()
        else:
            sink.finish()
            while not sink.wait(0.1):        # drain, but stay responsive to a barge-in
                if cancel.is_set():
                    sink.cancel()
                    break
