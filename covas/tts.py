"""ElevenLabs streaming TTS -> low-latency PCM playback (instantly cancellable)."""
from __future__ import annotations
import threading
from pathlib import Path

import requests
import sounddevice as sd


def _api_key(cfg: dict) -> str:
    return Path(cfg["elevenlabs"]["api_key_file"]).read_text(encoding="utf-8").strip()


# ElevenLabs voice `speed` accepts 0.7–1.2; we expose only the faster half (a slower COVAS
# reads oddly). Clamp hard so a bad config can never send an out-of-range value.
_SPEED_MIN, _SPEED_MAX = 1.0, 1.2


def _speed(cfg: dict) -> float:
    try:
        return max(_SPEED_MIN, min(_SPEED_MAX, float(cfg["elevenlabs"].get("speed", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def build_tts_body(cfg: dict, text: str) -> dict:
    """The ElevenLabs request body. `voice_settings.speed` is added ONLY when it differs from
    the default 1.0, so the default request is byte-for-byte what it was before N7 (no risk of
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


def speak(cfg: dict, text: str, cancel: threading.Event) -> None:
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
