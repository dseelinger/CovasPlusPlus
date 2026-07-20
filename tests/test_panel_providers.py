"""Offline unit tests for the provider-shaped quick-panel state (issue #86).

The control panel's LLM/Speech blocks REFLECT the active [llm]/[tts].provider: `public_settings()`
(and `/api/state`) must carry only the ACTIVE provider's quick fields, serialized from the ONE
schema, so index.html renders them generically. These prove the payload shape for representative
combos — Anthropic+ElevenLabs and non-default alternates — without touching the network (fake
providers injected, dynamic catalogs left unresolved for client-side fetch).
"""
from __future__ import annotations

import pytest

from covas import config
from covas.app import App
from covas.web import create_app
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path, llm_provider="anthropic", tts_provider="elevenlabs") -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    return {
        "keys": {"push_to_talk": "[", "tap_cancel_ms": 400, "cancel": ""},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "whisper": {"model": "small", "n_threads": 4, "language": "en"},
        "llm": {"provider": llm_provider},
        "tts": {"provider": tts_provider, "speed": 1.0},
        "anthropic": {
            "model": "claude-sonnet-5",
            "available_models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
            "max_tokens": 1024, "cache_ttl": "1h", "thinking": {"default": "Off"},
        },
        "openai": {"provider": "", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b"},
        "gemini": {"base_url": "", "model": "gemini-flash-lite-latest"},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "EXAVITQu4vr4xnSDxMaL",
                       "voice_name": "Sarah", "speed": 1.0, "output_format": "pcm_16000"},
        "edge": {"voice": "en-US-AriaNeural"},
        "azure": {"region": "eastus", "voice": "en-US-AriaNeural", "style": ""},
        "openai_tts": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini-tts",
                       "voice": "alloy", "instructions": ""},
        "cartesia": {"model": "sonic-2", "voice": "", "language": "en"},
        "piper": {"model": ""},
        "router": {"enabled": True, "pin": "", "full_breakdown_max_tokens": 2048},
        "web_search": {"enabled": True, "max_uses": 3},
        "personality": {"enabled": True},
        "conversation": {"max_turns": 20},
        "elite": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "logging": {"dir": str(tmp_path / "logs")},
    }


def _app(tmp_path, monkeypatch, **kw) -> App:
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    return App(_cfg(tmp_path, **kw), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())


def _keys(block) -> list[str]:
    return [f["key"] for f in block["fields"]]


# --- LLM block reflects the active provider --------------------------------

def test_anthropic_llm_block_has_model_and_thinking(tmp_path, monkeypatch):
    s = _app(tmp_path, monkeypatch, llm_provider="anthropic").public_settings()
    assert s["llm"]["provider"] == "anthropic"
    assert s["llm"]["supports_thinking"] is True
    keys = _keys(s["llm"])
    assert "anthropic.model" in keys and "anthropic.thinking.default" in keys
    model = next(f for f in s["llm"]["fields"] if f["key"] == "anthropic.model")
    assert model["value"] == "claude-sonnet-5"
    assert "claude-opus-4-8" in model["options"]        # resolved from config, offline


def test_openai_llm_block_is_combobox_model_plus_readonly_base_url(tmp_path, monkeypatch):
    s = _app(tmp_path, monkeypatch, llm_provider="openai").public_settings()
    assert s["llm"]["provider"] == "openai"
    assert s["llm"]["supports_thinking"] is False        # thinking is Anthropic-only v1
    assert "anthropic.thinking.default" not in _keys(s["llm"])
    base = next(f for f in s["llm"]["fields"] if f["key"] == "openai.base_url")
    model = next(f for f in s["llm"]["fields"] if f["key"] == "openai.model")
    assert base["readonly"] is True                      # base_url shown but edited on Settings
    assert model["combobox"] is True                     # editable combobox (fetched catalog)
    assert model["value"] == "llama-3.3-70b"


def test_gemini_llm_block(tmp_path, monkeypatch):
    g = _app(tmp_path, monkeypatch, llm_provider="gemini").public_settings()
    assert _keys(g["llm"]) == ["gemini.model"] and g["llm"]["supports_thinking"] is False


# --- Speech block reflects the active provider -----------------------------

def test_elevenlabs_tts_block(tmp_path, monkeypatch):
    s = _app(tmp_path, monkeypatch, tts_provider="elevenlabs").public_settings()
    assert s["tts"]["provider"] == "elevenlabs"
    assert _keys(s["tts"]) == ["elevenlabs.model", "elevenlabs.voice_id", "tts.speed"]
    speed = next(f for f in s["tts"]["fields"] if f["key"] == "tts.speed")
    # Speed is the ONE normalized provider-agnostic field (#99), rendered generically off the
    # schema field's bounds — never hardcoded in the panel.
    assert speed["min"] == 0.5 and speed["max"] == 2.0 and speed["value"] == 1.0


@pytest.mark.parametrize("provider,expected", [
    ("edge", ["edge.voice", "tts.speed"]),
    ("azure", ["azure.region", "azure.voice", "azure.style", "tts.speed"]),
    ("openai", ["openai_tts.model", "openai_tts.voice", "openai_tts.instructions", "tts.speed"]),
    ("cartesia", ["cartesia.model", "cartesia.voice", "cartesia.language", "tts.speed"]),
    ("piper", ["piper.model", "tts.speed"]),
])
def test_alternate_tts_blocks(tmp_path, monkeypatch, provider, expected):
    s = _app(tmp_path, monkeypatch, tts_provider=provider).public_settings()
    assert s["tts"]["provider"] == provider
    assert _keys(s["tts"]) == expected


def test_edge_voice_is_combobox(tmp_path, monkeypatch):
    s = _app(tmp_path, monkeypatch, tts_provider="edge").public_settings()
    voice = s["tts"]["fields"][0]
    assert voice["key"] == "edge.voice" and voice["combobox"] is True
    assert voice["value"] == "en-US-AriaNeural"


# --- /api/state carries the provider-shaped payload + whisper option list ---

def test_api_state_shape_for_alternate_combo(tmp_path, monkeypatch):
    core = _app(tmp_path, monkeypatch, llm_provider="gemini", tts_provider="edge")
    app = create_app(core)
    app.config.update(TESTING=True)
    st = app.test_client().get("/api/state").get_json()
    assert st["settings"]["llm"]["provider"] == "gemini"
    assert st["settings"]["tts"]["provider"] == "edge"
    assert "whisper" in st["options"] and "models" not in st["options"]
    # kept-as-is flat keys survive the reshape
    assert st["settings"]["whisper"] == "small"
    assert st["settings"]["personality"] is True
