"""Local speech-to-text via whisper.cpp (`pywhispercpp`) — a fully permissive STT backend
(whisper.cpp is MIT; reads float32 PCM directly, so no FFmpeg / PyAV / GPL x264/x265 in the
installer). Satisfies the `STTProvider` Protocol so `app.py` and the provider seam are unchanged.

Issue #206 replaces the faster-whisper + ctranslate2 + av stack with this. The real whisper.cpp
`Model` is built lazily *and* is injectable, so the unit tests stay offline — they drive a fake
backend and never import `pywhispercpp` or load a ~465 MB ggml model. Model-path resolution lives
in `firstrun.stt_model_path` (per-user models dir); the factory selects this provider.
"""
from __future__ import annotations

import os
import re

import numpy as np

# whisper.cpp emits bracketed non-speech markers ("[BLANK_AUDIO]", "[ Silence ]", "(wind blowing)"
# style cues) as ordinary segment text. They aren't words the Commander spoke, so drop them before
# the text reaches the LLM — otherwise a silent push-to-talk would "say" "[BLANK_AUDIO]".
_NONSPEECH = re.compile(r"[\[(][^\])]*[\])]")


def _to_float32_mono(audio: np.ndarray) -> np.ndarray:
    """Normalize to the contiguous mono float32 in [-1, 1] that whisper.cpp expects. app.py already
    feeds 16 kHz mono float32 (see covas/app.py capture), but be defensive: downmix a stereo buffer
    and rescale int16 PCM, so a caller handing us raw samples doesn't get silence or a native crash."""
    a = np.asarray(audio)
    if a.ndim > 1:  # (samples, channels) -> mono
        a = a.mean(axis=1)
    if a.dtype == np.int16:
        a = a.astype(np.float32) / 32768.0
    elif a.dtype != np.float32:
        a = a.astype(np.float32)
    return np.ascontiguousarray(a)


def _clean(text: str) -> str:
    """Strip whisper.cpp's bracketed non-speech markers and collapse whitespace to a tidy line."""
    return " ".join(_NONSPEECH.sub(" ", text).split()).strip()


class WhisperCppSTT:
    """STTProvider backed by pywhispercpp's whisper.cpp bindings.

    `model` is injectable for tests: any object exposing `transcribe(np.ndarray) -> iterable` of
    segments with a `.text` attribute. `None` (the app path) builds the real CPU model lazily.
    """

    def __init__(self, cfg: dict, *, model=None) -> None:  # noqa: ANN001 — fake backend in tests
        w = cfg.get("whisper", {}) or {}
        # "" (auto-detect) stays as None so we ask whisper.cpp to detect rather than forcing a code.
        self.language = (str(w.get("language") or "")).strip() or None
        self._model = model if model is not None else self._build_model(cfg)

    @staticmethod
    def _build_model(cfg: dict):
        """Construct the real CPU whisper.cpp model. `pywhispercpp` is imported lazily so the local
        STT stack isn't a hard dependency for tests or the cloud-only providers. The model file is
        resolved to a ggml-*.bin under the per-user models dir (mirrors `stt_download_root`) by
        `firstrun.stt_model_path`, so weights stay out of the read-only install tree."""
        from pywhispercpp.model import Model  # heavy native dep — only at the app entry

        from ..firstrun import DEFAULT_STT_MODEL, stt_model_path

        w = cfg.get("whisper", {}) or {}
        params: dict = {
            # CPU-only: CLAUDE.md keeps ML off the GPU so STT never fights Elite for it. The shipped
            # wheel is a CPU build anyway; setting this makes the intent explicit and quiets the log.
            "context_params": {"use_gpu": False},
            # Keep whisper.cpp's per-load stderr out of the voice loop (None => /dev/null).
            "redirect_whispercpp_logs_to": None,
            "print_realtime": False,
            "print_progress": False,
            "n_threads": int(w.get("n_threads") or 4),
        }
        if w.get("language"):
            params["language"] = str(w["language"])
        else:
            params["detect_language"] = True
        model_ref = stt_model_path(cfg)
        # whisper.cpp SEGFAULTS (native access violation) on a missing/unreadable ggml file rather
        # than raising, and a fatal crash slips past the voice loop's fail-soft `except` guards.
        # Validate here so a missing model degrades cleanly (text mode / keeps the previous STT on a
        # live reload) — we manage downloads ourselves, so we never want pywhispercpp to auto-fetch.
        if not os.path.exists(model_ref):
            raise FileNotFoundError(
                f"whisper.cpp model not found: {model_ref} — run setup to download the "
                f"'{w.get('model', DEFAULT_STT_MODEL)}' model.")
        return Model(model_ref, **params)

    def transcribe(self, audio: np.ndarray) -> str:
        """Turn mono float32 audio into text (empty string if nothing was heard)."""
        if audio is None or len(audio) == 0:
            return ""
        segments = self._model.transcribe(_to_float32_mono(audio))
        return _clean("".join(getattr(s, "text", "") for s in segments))
