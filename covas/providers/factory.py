"""Build the configured provider for each swappable piece. Imports are lazy so
the local stack doesn't require the cloud SDKs (and vice versa)."""
from __future__ import annotations

from ..config import experimental, mock_enabled
from .base import LLMProvider, STTProvider, TTSProvider

# Providers gated behind an [experimental.<flag>] toggle (issue #123). Selecting one while its
# flag is off is treated exactly like an unknown provider — it isn't offered on the public Settings
# surface, so this is only reachable by a deliberate overrides.json opt-in, where the matching flag
# is set alongside. Kept here (not scattered in the branches) so the gate reads in one place.
_EXPERIMENTAL_TTS = {"azure": "azure_tts", "cartesia": "cartesia_tts"}


def make_stt(cfg: dict) -> STTProvider:
    if mock_enabled(cfg):
        from .fakes import FakeSTT
        return FakeSTT(cfg)
    # whisper.cpp via pywhispercpp (issue #206) — MIT, CPU-side, no FFmpeg/GPL. Kept behind the
    # seam for symmetry with the TTS/LLM providers.
    from .whispercpp_stt import WhisperCppSTT
    return WhisperCppSTT(cfg)


def make_llm(cfg: dict) -> LLMProvider:
    if mock_enabled(cfg):
        from .fakes import FakeLLM
        return FakeLLM(cfg)
    name = str(cfg.get("llm", {}).get("provider", "anthropic")).lower()
    if name == "anthropic":
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(cfg)
    if name == "openai":
        # OpenAI-compatible (OpenAI/Groq/DeepSeek/OpenRouter) — one impl, base_url selects the
        # endpoint; the router tiers it via [openai].tiers (issue #12, cloud path is fine in-game).
        from .openai_llm import OpenAILLM
        return OpenAILLM(cfg)
    if name == "gemini":
        # Gemini native API — function calling + Google Search grounding; tiered via [gemini].tiers
        # (Flash-Lite/3.5 Flash). Issue #13; cloud path is fine in-game.
        from .gemini_llm import GeminiLLM
        return GeminiLLM(cfg)
    raise ValueError(
        f"Unknown [llm].provider: {name!r} (use 'anthropic', 'openai', or 'gemini')")


def make_tts(cfg: dict, *, mixer=None) -> TTSProvider:  # noqa: ANN001
    """Build the configured TTS provider. When `mixer` is given (C9: the audio layer is on),
    the real providers stream COVAS speech through the shared BusMixer instead of opening their
    own device stream; the mock ignores it."""
    if mock_enabled(cfg):
        from .fakes import FakeTTS
        return FakeTTS(cfg)
    name = str(cfg.get("tts", {}).get("provider", "elevenlabs")).lower()
    # Experimental providers (issue #123): unavailable unless their flag is on. Raise as unavailable
    # (like an unknown provider) — the live TTS-swap path (App._reload_tts) handles it fail-soft
    # (keeps the previous voice), and these aren't offered on the public Settings/wizard surface, so
    # this is only reached by a deliberate overrides.json opt-in that forgot the matching flag.
    flag = _EXPERIMENTAL_TTS.get(name)
    if flag and not experimental(cfg, flag):
        raise ValueError(
            f"[tts].provider {name!r} is experimental — set [experimental.{flag}].enabled = true "
            "in overrides.json to use it.")
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
