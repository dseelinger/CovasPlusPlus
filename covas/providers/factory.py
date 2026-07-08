"""Build the configured provider for each swappable piece. Imports are lazy so
the local stack doesn't require the cloud SDKs (and vice versa)."""
from __future__ import annotations

from ..config import mock_enabled
from .base import LLMProvider, STTProvider, TTSProvider


def make_stt(cfg: dict) -> STTProvider:
    if mock_enabled(cfg):
        from .fakes import FakeSTT
        return FakeSTT(cfg)
    # Only faster-whisper today; kept behind the seam for symmetry.
    from .whisper_stt import WhisperSTT
    return WhisperSTT(cfg)


def make_llm(cfg: dict) -> LLMProvider:
    if mock_enabled(cfg):
        from .fakes import FakeLLM
        return FakeLLM(cfg)
    name = str(cfg.get("llm", {}).get("provider", "anthropic")).lower()
    if name == "ollama":
        from .ollama_llm import OllamaLLM
        return OllamaLLM(cfg)
    if name == "anthropic":
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(cfg)
    raise ValueError(f"Unknown [llm].provider: {name!r} (use 'anthropic' or 'ollama')")


def make_tts(cfg: dict) -> TTSProvider:
    if mock_enabled(cfg):
        from .fakes import FakeTTS
        return FakeTTS(cfg)
    name = str(cfg.get("tts", {}).get("provider", "elevenlabs")).lower()
    if name == "piper":
        from .piper_tts import PiperTTS
        return PiperTTS(cfg)
    if name == "elevenlabs":
        from .elevenlabs_tts import ElevenLabsTTS
        return ElevenLabsTTS(cfg)
    raise ValueError(f"Unknown [tts].provider: {name!r} (use 'elevenlabs' or 'piper')")
