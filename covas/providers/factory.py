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
    if name == "openai":
        # OpenAI-compatible (OpenAI/Groq/DeepSeek/OpenRouter) — one impl, base_url selects the
        # endpoint; the router tiers it via [openai].tiers (issue #12, cloud path is fine in-game).
        from .openai_llm import OpenAILLM
        return OpenAILLM(cfg)
    raise ValueError(f"Unknown [llm].provider: {name!r} (use 'anthropic', 'openai', or 'ollama')")


def make_tts(cfg: dict, *, mixer=None) -> TTSProvider:  # noqa: ANN001
    """Build the configured TTS provider. When `mixer` is given (C9: the audio layer is on),
    the real providers stream COVAS speech through the shared BusMixer instead of opening their
    own device stream; the mock ignores it."""
    if mock_enabled(cfg):
        from .fakes import FakeTTS
        return FakeTTS(cfg)
    name = str(cfg.get("tts", {}).get("provider", "elevenlabs")).lower()
    if name == "piper":
        from .piper_tts import PiperTTS
        return PiperTTS(cfg, mixer=mixer)
    if name == "elevenlabs":
        from .elevenlabs_tts import ElevenLabsTTS
        return ElevenLabsTTS(cfg, mixer=mixer)
    if name == "edge":
        # Edge (edge-tts) is FREE but rides an undocumented, no-SLA endpoint (see edge_tts.py).
        # It's never load-bearing: fail soft to Piper when a local voice is configured, so a broken
        # endpoint degrades to the guaranteed free floor instead of to text.
        from .edge_tts import EdgeTTS
        fallback = None
        if str(cfg.get("piper", {}).get("model", "")).strip():
            try:
                from .piper_tts import PiperTTS
                fallback = PiperTTS(cfg, mixer=mixer)
            except Exception:  # noqa: BLE001 — no Piper floor available; degrade to text instead
                fallback = None
        return EdgeTTS(cfg, mixer=mixer, fallback=fallback)
    if name == "azure":
        # Official Azure Neural TTS — the reliable, free-tier sibling of Edge (real API + SLA).
        from .azure_tts import AzureTTS
        return AzureTTS(cfg, mixer=mixer)
    if name == "openai":
        # OpenAI-compatible TTS — a cheap cloud persona voice; base_url is configurable.
        from .openai_tts import OpenAITTS
        return OpenAITTS(cfg, mixer=mixer)
    if name == "cartesia":
        # Cartesia (Sonic) — a low-latency PREMIUM persona voice (persona-eligible only, #18).
        from .cartesia_tts import CartesiaTTS
        return CartesiaTTS(cfg, mixer=mixer)
    raise ValueError(
        f"Unknown [tts].provider: {name!r} "
        "(use 'edge', 'elevenlabs', 'piper', 'azure', 'openai', or 'cartesia')")
