"""Cloud TTS provider — wraps the existing ElevenLabs path so it satisfies
TTSProvider. No behavior change; this is just the seam."""
from __future__ import annotations

import threading

from .. import tts


class ElevenLabsTTS:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def speak(self, text: str, cancel: threading.Event) -> None:
        tts.speak(self.cfg, text, cancel)

    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        pcm = tts.synth_pcm(self.cfg, text, voice_id)
        fmt = self.cfg["elevenlabs"].get("output_format", "pcm_16000")
        sr = int(fmt.split("_")[1]) if fmt.startswith("pcm_") else 16000
        return pcm, sr
