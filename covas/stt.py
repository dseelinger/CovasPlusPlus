"""Local speech-to-text via faster-whisper."""
from __future__ import annotations
import numpy as np
from faster_whisper import WhisperModel


class Transcriber:
    def __init__(self, cfg: dict) -> None:
        w = cfg["whisper"]
        self.model = WhisperModel(
            w["model"], device=w["device"], compute_type=w["compute_type"]
        )
        self.language = w["language"] or None

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) == 0:
            return ""
        segments, _info = self.model.transcribe(
            audio, language=self.language, beam_size=5, vad_filter=True
        )
        return "".join(s.text for s in segments).strip()
