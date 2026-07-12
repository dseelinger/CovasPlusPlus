"""Unit tests for providers.factory — provider name -> concrete class dispatch.

No servers, SDKs, or hardware. The not-locally-constructible providers (piper
needs the `piper` package; elevenlabs/anthropic reach out on construction) are
tested by injecting a stub module in place of the lazily-imported one, which also
proves the imports really are lazy (the stub is only touched for its own branch).
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest

from covas.providers import factory
from covas.providers.ollama_llm import OllamaLLM


def _stub_provider(monkeypatch, module_name: str, class_name: str):
    """Put a fake `covas.providers.<module_name>` in sys.modules exposing a stub
    `class_name`, so factory's lazy `from .<module_name> import <class_name>`
    resolves to the stub instead of the real (dependency-heavy) module."""
    mod = types.ModuleType(f"covas.providers.{module_name}")

    class _Stub:
        def __init__(self, cfg, **kwargs):   # accepts mixer=... like the real providers (C9)
            self.cfg = cfg
            self.kwargs = kwargs

    _Stub.__name__ = class_name
    setattr(mod, class_name, _Stub)
    monkeypatch.setitem(sys.modules, f"covas.providers.{module_name}", mod)
    return _Stub


# --- make_llm --------------------------------------------------------------

def test_make_llm_ollama_returns_ollama_instance():
    # Real construction — OllamaLLM is offline-safe (no network until ping/stream).
    cfg = {
        "llm": {"provider": "ollama"},
        "personality": {"enabled": False},
        "ollama": {"model": "qwen3"},
    }
    llm = factory.make_llm(cfg)
    assert isinstance(llm, OllamaLLM)
    assert llm.model == "qwen3"


def test_local_path_imports_without_anthropic(monkeypatch):
    # Force a CLEAN re-import of the local LLM module chain with the cloud SDK
    # made unimportable. ollama_llm pulls build_system from covas.llm; if covas.llm
    # did a top-level `import anthropic`, this re-import would raise. Guards the
    # decoupling that keeps the offline stack runnable without the cloud SDK.
    monkeypatch.setitem(sys.modules, "anthropic", None)  # any `import anthropic` -> fails
    for name in ("covas.providers.ollama_llm", "covas.llm"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    mod = importlib.import_module("covas.providers.ollama_llm")
    assert hasattr(mod, "OllamaLLM")


def test_make_llm_anthropic_selected(monkeypatch):
    stub = _stub_provider(monkeypatch, "anthropic_llm", "AnthropicLLM")
    llm = factory.make_llm({"llm": {"provider": "anthropic"}})
    assert isinstance(llm, stub)


def test_make_llm_defaults_to_anthropic(monkeypatch):
    stub = _stub_provider(monkeypatch, "anthropic_llm", "AnthropicLLM")
    assert isinstance(factory.make_llm({}), stub)  # no [llm].provider -> anthropic


def test_make_llm_provider_is_case_insensitive(monkeypatch):
    stub = _stub_provider(monkeypatch, "anthropic_llm", "AnthropicLLM")
    assert isinstance(factory.make_llm({"llm": {"provider": "Anthropic"}}), stub)


def test_make_llm_unknown_raises_with_name():
    with pytest.raises(ValueError) as exc:
        factory.make_llm({"llm": {"provider": "gpt4"}})
    msg = str(exc.value)
    assert "gpt4" in msg
    assert "anthropic" in msg and "ollama" in msg  # tells the user the valid names


# --- make_tts --------------------------------------------------------------

def test_make_tts_piper_selected(monkeypatch):
    stub = _stub_provider(monkeypatch, "piper_tts", "PiperTTS")
    tts = factory.make_tts({"tts": {"provider": "piper"}})
    assert isinstance(tts, stub)


def test_make_tts_elevenlabs_selected(monkeypatch):
    stub = _stub_provider(monkeypatch, "elevenlabs_tts", "ElevenLabsTTS")
    assert isinstance(factory.make_tts({"tts": {"provider": "elevenlabs"}}), stub)


def test_make_tts_defaults_to_elevenlabs(monkeypatch):
    stub = _stub_provider(monkeypatch, "elevenlabs_tts", "ElevenLabsTTS")
    assert isinstance(factory.make_tts({}), stub)


def test_make_tts_piper_does_not_require_elevenlabs(monkeypatch):
    # Selecting piper must not import the elevenlabs module.
    stub = _stub_provider(monkeypatch, "piper_tts", "PiperTTS")
    monkeypatch.setitem(sys.modules, "covas.providers.elevenlabs_tts", None)
    assert isinstance(factory.make_tts({"tts": {"provider": "piper"}}), stub)


def test_make_tts_unknown_raises_with_name():
    with pytest.raises(ValueError) as exc:
        factory.make_tts({"tts": {"provider": "coqui"}})
    msg = str(exc.value)
    assert "coqui" in msg
    assert "elevenlabs" in msg and "piper" in msg


# --- make_stt --------------------------------------------------------------

def test_make_stt_returns_whisper(monkeypatch):
    # Real WhisperSTT loads a model on construction, so stub it.
    stub = _stub_provider(monkeypatch, "whisper_stt", "WhisperSTT")
    assert isinstance(factory.make_stt({}), stub)
