"""Local speech-to-text provider — thin wrapper over the existing faster-whisper
Transcriber so it satisfies the STTProvider interface."""
from __future__ import annotations

import numpy as np

from ..stt import Transcriber


class WhisperSTT:
    def __init__(self, cfg: dict) -> None:
        self._t = Transcriber(cfg)

    def transcribe(self, audio: np.ndarray) -> str:
        return self._t.transcribe(audio)
