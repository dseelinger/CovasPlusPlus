"""Cloud TTS provider — wraps the existing ElevenLabs path so it satisfies
TTSProvider. No behaviour change on the default path; when a mixer is supplied
(C9), streaming playback is routed through the shared BusMixer's COVAS bus."""
from __future__ import annotations

import threading

from .. import tts


class ElevenLabsTTS:
    def __init__(self, cfg: dict, *, mixer=None, bus: str = "covas") -> None:  # noqa: ANN001
        self.cfg = cfg
        self._mixer = mixer
        self._bus = bus

    def speak(self, text: str, cancel: threading.Event) -> None:
        open_sink = None
        if self._mixer is not None:
            open_sink = lambda sr: self._mixer.open_speech(self._bus, sr)  # noqa: E731
        tts.speak(self.cfg, text, cancel, open_sink=open_sink)

    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        pcm = tts.synth_pcm(self.cfg, text, voice_id)
        fmt = self.cfg["elevenlabs"].get("output_format", "pcm_16000")
        sr = int(fmt.split("_")[1]) if fmt.startswith("pcm_") else 16000
        return pcm, sr
