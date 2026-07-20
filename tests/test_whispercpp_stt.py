"""Offline unit tests for the whisper.cpp STT provider (issue #206, checkpoint C1).

These are hermetic: they inject `FakeWhisperCppModel` from tests/fakes.py, so nothing imports
`pywhispercpp` or loads a real ggml model. They cover the provider's own logic — PCM shaping,
the empty-buffer guard, segment joining, and whisper.cpp's non-speech-marker stripping. The real
backend (a genuine transcribe on Doug's machine) is exercised separately under
`@pytest.mark.integration and local` (added with the on-hardware verification in C5).
"""
from __future__ import annotations

import numpy as np

from covas.providers.base import STTProvider
from covas.providers.whispercpp_stt import WhisperCppSTT, _clean, _to_float32_mono
from tests.fakes import FakeWhisperCppModel


def _stt(segments=None, *, language: str = "en") -> WhisperCppSTT:
    cfg = {"whisper": {"model": "small.en", "language": language}}
    return WhisperCppSTT(cfg, model=FakeWhisperCppModel(segments=segments))


# ---- structural typing -----------------------------------------------------
def test_satisfies_stt_protocol():
    assert isinstance(_stt(["hi"]), STTProvider)


# ---- transcribe: joining + guards ------------------------------------------
def test_joins_segment_text():
    assert _stt(["Set course", " to Sol."]).transcribe(np.zeros(16, np.float32)) == "Set course to Sol."


def test_empty_buffer_returns_empty_without_calling_model():
    model = FakeWhisperCppModel(segments=["should not be reached"])
    stt = WhisperCppSTT({"whisper": {"model": "small.en"}}, model=model)
    assert stt.transcribe(np.array([], dtype=np.float32)) == ""
    assert stt.transcribe(None) == ""
    assert model.heard == []  # guard short-circuits before the backend


def test_strips_nonspeech_markers():
    # A silent PTT press: whisper.cpp yields only a bracketed marker -> we return nothing.
    assert _stt(["[BLANK_AUDIO]"]).transcribe(np.zeros(16, np.float32)) == ""
    assert _stt([" [ Silence ] "]).transcribe(np.zeros(16, np.float32)) == ""
    # Markers mixed with speech are removed, real words kept.
    assert _stt(["(wind) Docking request granted."]).transcribe(np.zeros(16, np.float32)) \
        == "Docking request granted."


# ---- PCM normalization (the shape/dtype the backend actually receives) -----
def test_passes_contiguous_float32_to_backend():
    model = FakeWhisperCppModel(segments=["ok"])
    stt = WhisperCppSTT({"whisper": {"model": "small.en"}}, model=model)
    stt.transcribe(np.zeros(32, dtype=np.float32))
    heard = model.heard[0]
    assert heard.dtype == np.float32 and heard.flags["C_CONTIGUOUS"]


def test_int16_pcm_is_scaled_to_unit_float():
    got = _to_float32_mono(np.array([-32768, 0, 32767], dtype=np.int16))
    assert got.dtype == np.float32
    assert got[0] == -1.0 and got[1] == 0.0
    assert abs(got[2] - 1.0) < 1e-3


def test_stereo_is_downmixed_to_mono():
    stereo = np.array([[1.0, -1.0], [0.5, 0.5]], dtype=np.float32)  # (frames, channels)
    got = _to_float32_mono(stereo)
    assert got.ndim == 1
    assert np.allclose(got, [0.0, 0.5])


# ---- config wiring for the (lazily built) real backend ---------------------
def test_language_none_when_unset_for_autodetect():
    assert WhisperCppSTT({"whisper": {"model": "m", "language": ""}}, model=FakeWhisperCppModel()).language is None
    assert WhisperCppSTT({"whisper": {"model": "m", "language": "en"}}, model=FakeWhisperCppModel()).language == "en"


def test_clean_collapses_whitespace():
    assert _clean("  hello   world  ") == "hello world"
    assert _clean("") == ""
