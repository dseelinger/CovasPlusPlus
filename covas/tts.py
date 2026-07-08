"""ElevenLabs streaming TTS -> low-latency PCM playback (instantly cancellable)."""
from __future__ import annotations
import threading
from pathlib import Path

import requests
import sounddevice as sd


def _api_key(cfg: dict) -> str:
    return Path(cfg["elevenlabs"]["api_key_file"]).read_text(encoding="utf-8").strip()


def synth_pcm(cfg: dict, text: str, voice_id: str | None = None) -> bytes:
    """Synthesize `text` to raw PCM (16-bit mono 16 kHz) and return all bytes.
    Used to pre-generate + cache spoken status lines (not for live replies)."""
    el = cfg["elevenlabs"]
    vid = voice_id or el["voice_id"]
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream"
        f"?output_format=pcm_16000"
    )
    body = {"text": text, "model_id": el["model"]}
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
    body = {"text": text, "model_id": el["model"]}
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
