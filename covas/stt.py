"""Local speech-to-text via faster-whisper."""
from __future__ import annotations
import numpy as np
from faster_whisper import WhisperModel


class Transcriber:
    def __init__(self, cfg: dict) -> None:
        from .firstrun import stt_download_root
        w = cfg["whisper"]
        # download_root: None in a source run (default HF cache — dev models reused), a per-user
        # models dir when frozen so weights stay out of the read-only install tree.
        self.model = WhisperModel(
            w["model"], device=w["device"], compute_type=w["compute_type"],
            download_root=stt_download_root(cfg),
        )
        self.language = w["language"] or None

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) == 0:
            return ""
        segments, _info = self.model.transcribe(
            audio, language=self.language, beam_size=5, vad_filter=True
        )
        return "".join(s.text for s in segments).strip()
