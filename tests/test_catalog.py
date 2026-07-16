"""Fetched-catalog resolver tests (issues #92 + #88).

All OFFLINE: the provider fetchers are monkeypatched, so sentinel→options wiring, the fail-soft
contract (None + reason on any error, never a raise), and the pure parse helpers are all exercised
with no key or socket. Proves the two guarantees the issues care about: catalog values resolve from a
fake payload, and a fetch failure degrades to (None, reason) so the UI keeps free-text.
"""
from __future__ import annotations

import pytest

from covas import catalog
from covas import settings_schema as schema
from covas.providers import gemini_llm, ollama_llm, openai_llm


# ---- pure parse helpers ----------------------------------------------------
def test_parse_openai_models_dedupes_and_orders():
    payload = {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}, {"id": "gpt-4o-mini"},
                        {"nope": 1}, "x"]}
    assert openai_llm.parse_openai_models(payload) == ["gpt-4o-mini", "gpt-4o"]
    assert openai_llm.parse_openai_models({}) == []


def test_parse_ollama_tags():
    payload = {"models": [{"name": "qwen3:latest"}, {"name": "llama3"}, {"name": "qwen3:latest"}]}
    assert ollama_llm.parse_ollama_tags(payload) == ["qwen3:latest", "llama3"]
    assert ollama_llm.parse_ollama_tags({}) == []


def test_parse_gemini_models_strips_prefix():
    payload = {"models": [{"name": "models/gemini-2.5-flash"}, {"name": "gemini-2.5-pro"}]}
    assert gemini_llm.parse_models_list(payload) == ["gemini-2.5-flash", "gemini-2.5-pro"]


# ---- static sources (always available, no network) -------------------------
def test_base_urls_source_is_static_presets():
    opts, err = catalog.resolve(schema.OPT_OPENAI_BASE_URLS, {})
    assert err is None
    labels = [o["label"] for o in opts]
    assert "OpenAI" in labels and "Groq" in labels and "OpenRouter" in labels
    assert all("value" in o and "label" in o for o in opts)


def test_unknown_source_returns_reason_not_raise():
    opts, err = catalog.resolve("@nope", {})
    assert opts is None and "unknown source" in err


# ---- model catalogs (fetchers monkeypatched) -------------------------------
def test_openai_models_resolve_with_key(monkeypatch):
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: "k")
    monkeypatch.setattr(openai_llm, "list_openai_models", lambda url, key, **k: ["gpt-4o", "gpt-4o-mini"])
    opts, err = catalog.resolve(schema.OPT_OPENAI_MODELS, {"openai": {"base_url": "https://x/v1"}})
    assert err is None
    assert [o["value"] for o in opts] == ["gpt-4o", "gpt-4o-mini"]


def test_openai_models_no_key_degrades(monkeypatch):
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: None)
    opts, err = catalog.resolve(schema.OPT_OPENAI_MODELS, {})
    assert opts is None and "no OpenAI key" in err


def test_openai_models_base_url_override_is_used(monkeypatch):
    seen = {}
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: "k")
    monkeypatch.setattr(openai_llm, "list_openai_models",
                        lambda url, key, **k: seen.setdefault("url", url) and [] or [])
    catalog.resolve(schema.OPT_OPENAI_MODELS, {"openai": {"base_url": "https://cfg/v1"}},
                    base_url="https://override/v1")
    assert seen["url"] == "https://override/v1"   # the page's pending base_url wins (refetch, #92)


def test_fetch_failure_is_failsoft(monkeypatch):
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: "k")

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(openai_llm, "list_openai_models", boom)
    opts, err = catalog.resolve(schema.OPT_OPENAI_MODELS, {})
    assert opts is None and "connection refused" in err   # reason surfaced, no raise


def test_ollama_models_no_key_needed(monkeypatch):
    monkeypatch.setattr(ollama_llm, "list_ollama_models", lambda host, **k: ["qwen3:latest"])
    opts, err = catalog.resolve(schema.OPT_OLLAMA_MODELS, {"ollama": {"host": "http://h:11434"}})
    assert err is None and opts[0]["value"] == "qwen3:latest"


def test_gemini_models_resolve(monkeypatch):
    monkeypatch.setattr("covas.firstrun.gemini_key", lambda cfg: "k")
    monkeypatch.setattr(gemini_llm, "list_gemini_models", lambda url, key, **k: ["gemini-2.5-flash"])
    opts, err = catalog.resolve(schema.OPT_GEMINI_MODELS, {})
    assert err is None and opts[0]["value"] == "gemini-2.5-flash"


# ---- anthropic: live preferred, static fallback ----------------------------
def test_anthropic_live_merges_static(monkeypatch):
    monkeypatch.setattr("covas.llm.list_anthropic_models", lambda cfg, **k: ["claude-new-1"])
    cfg = {"anthropic": {"available_models": ["claude-sonnet-5", "claude-new-1"]}}
    opts, err = catalog.resolve(schema.OPT_ANTHROPIC_MODELS_LIVE, cfg)
    vals = [o["value"] for o in opts]
    assert err is None and vals == ["claude-new-1", "claude-sonnet-5"]   # live first, static extras, deduped


def test_anthropic_falls_back_to_static_when_live_fails(monkeypatch):
    def boom(cfg, **k):
        raise RuntimeError("no key")

    monkeypatch.setattr("covas.llm.list_anthropic_models", boom)
    cfg = {"anthropic": {"available_models": ["claude-sonnet-5"]}}
    opts, err = catalog.resolve(schema.OPT_ANTHROPIC_MODELS_LIVE, cfg)
    assert err is None and [o["value"] for o in opts] == ["claude-sonnet-5"]


# ---- voice catalogs --------------------------------------------------------
def test_edge_voices_no_key(monkeypatch):
    monkeypatch.setattr("covas.providers.edge_tts.list_edge_voices",
                        lambda **k: [{"ref": "en-US-AriaNeural", "name": "Aria",
                                      "gender": "Female", "locale": "en-US"}])
    opts, err = catalog.resolve(schema.OPT_EDGE_VOICES, {})
    assert err is None
    assert opts[0]["value"] == "en-US-AriaNeural" and "en-US" in opts[0]["meta"]


def test_azure_voices_need_key_and_region(monkeypatch):
    monkeypatch.setattr("covas.firstrun.azure_key", lambda cfg: None)
    opts, err = catalog.resolve(schema.OPT_AZURE_VOICES, {"azure": {"region": "eastus"}})
    assert opts is None and "region" in err   # key missing -> degrade

    monkeypatch.setattr("covas.firstrun.azure_key", lambda cfg: "k")
    monkeypatch.setattr("covas.providers.azure_tts.list_azure_voices",
                        lambda key, region, **k: [{"ref": "en-GB-RyanNeural", "name": "Ryan",
                                                   "gender": "Male", "locale": "en-GB"}])
    opts, err = catalog.resolve(schema.OPT_AZURE_VOICES, {"azure": {"region": "uksouth"}})
    assert err is None and opts[0]["value"] == "en-GB-RyanNeural"


def test_cartesia_voices_key_gated(monkeypatch):
    monkeypatch.setattr("covas.firstrun.cartesia_key", lambda cfg: None)
    opts, err = catalog.resolve(schema.OPT_CARTESIA_VOICES, {})
    assert opts is None and "Cartesia" in err


# ---- option_pairs adapter for the voice layer ------------------------------
def test_option_pairs_maps_and_failsofts(monkeypatch):
    monkeypatch.setattr(catalog, "resolve", lambda s, cfg, **k: ([{"value": "v", "label": "L"}], None))
    assert catalog.option_pairs("@x", {}) == [("v", "L")]
    monkeypatch.setattr(catalog, "resolve", lambda s, cfg, **k: (None, "offline"))
    assert catalog.option_pairs("@x", {}) is None


# ---- combobox contract: unlisted value stays valid (issue #92) -------------
@pytest.mark.parametrize("key", ["openai.model", "gemini.model", "edge.voice", "azure.voice",
                                 "cartesia.voice", "openai.base_url"])
def test_combobox_accepts_custom_value(key):
    s = schema.by_key[key]
    assert schema.is_combobox(s)
    # A value NOT in any fetched list must validate (custom / at-your-own-risk escape hatch).
    val, err = schema.validate_value(s, "totally-custom-value", options=["something-else"])
    assert err is None and val == "totally-custom-value"


def test_static_tts_enums_are_strict():
    # openai_tts.voice / model are fixed sets — a bogus value IS rejected (not a combobox).
    s = schema.by_key["openai_tts.voice"]
    assert not schema.is_combobox(s)
    assert schema.validate_value(s, "robot")[0] is None
