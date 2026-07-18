"""Experimental feature-flag convention (issue #123) — offline, free.

Proves the four load-bearing properties of the convention:

  1. `config.experimental(cfg, name)` is public-safe: OFF for an absent section, an
     unknown/typo'd name, or a malformed sub-table — you only get True by explicitly
     setting `[experimental.<name>].enabled = true`.
  2. Each feature is gated at its REGISTRATION/seam, so a flag-off feature contributes
     NO tools and NO help metadata to the registry (public-invisibility) — shown
     end-to-end through the reference implementation, the Trade Route planner.
  3. The per-feature seams (crew / music / TTS providers / voice activation / the
     Settings option surface) each honour the flag.
  4. Self-enable works through the real config precedence: a flag set in overrides.json
     flips the feature on with no other change, and the flags never leak into the public
     Settings schema.

Everything is pure config/registry/factory assembly over fakes — no network, no audio,
no API, no LLM.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from covas import config
from covas.config import experimental


# --- 1. the accessor is public-safe by construction ------------------------

def test_experimental_true_only_when_explicitly_enabled():
    assert experimental({"experimental": {"trade_route": {"enabled": True}}}, "trade_route") is True


def test_experimental_false_when_enabled_false():
    assert experimental({"experimental": {"trade_route": {"enabled": False}}}, "trade_route") is False


def test_experimental_false_when_section_absent():
    assert experimental({}, "trade_route") is False


def test_experimental_false_for_unknown_or_typo_name():
    cfg = {"experimental": {"trade_route": {"enabled": True}}}
    assert experimental(cfg, "trade_rout") is False   # typo
    assert experimental(cfg, "teleporter") is False   # unknown


def test_experimental_false_when_subtable_malformed():
    # A non-dict sub-table (e.g. a flat `[experimental] trade_route = true` mistake) must not blow up.
    assert experimental({"experimental": {"trade_route": True}}, "trade_route") is False


def test_experimental_false_when_section_malformed():
    assert experimental({"experimental": "yes"}, "trade_route") is False


def test_experimental_missing_enabled_key_is_false():
    # Sub-table present but with no `enabled` key (e.g. only per-feature sub-settings).
    assert experimental({"experimental": {"music": {"volume": 3}}}, "music") is False


# --- 2. registry invisibility (reference impl: Trade Route) -----------------

def _base_cfg(tmp_path) -> dict:
    """A minimal App config: fake providers are injected, so no key/model/network is touched.
    input_device="" keeps the Recorder off the audio subsystem; empty sound_cues load nothing."""
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    return {
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "keys": {"push_to_talk": "right ctrl"},
    }


def _app(tmp_path, extra: dict):
    """Build a real App with fakes + the given extra config folded in (deep-merged)."""
    from covas.app import App
    from covas.config import deep_merge
    from tests.fakes import FakeLLM, FakeSTT, FakeTTS
    cfg = deep_merge(_base_cfg(tmp_path), extra)
    return App(cfg, llm=FakeLLM(text="ok"), tts=FakeTTS(), stt=FakeSTT(text="hi"))


def test_flag_off_capability_contributes_no_tools_or_help(tmp_path):
    # [route_plan].enabled is ON, but the experimental gate is OFF (the shipped public default):
    # the trade-route capability must be genuinely absent — no tool, no help category, no attr.
    app = _app(tmp_path, {"route_plan": {"enabled": True}})
    # The planner rows bind their attr only when built (Wiring(None, ...)), so a gated-off build
    # leaves it absent — the tools/help surface is the real invisibility check.
    assert getattr(app, "route_plan", None) is None
    assert not any(t["name"] == "plan_trade_route" for t in app.registry.tools())
    assert "trade routes" not in app.registry.categories()


def test_flag_on_registers_the_capability(tmp_path):
    # Same config + the experimental flag → the capability registers, exposing its tool + help.
    app = _app(tmp_path, {"route_plan": {"enabled": True},
                          "experimental": {"trade_route": {"enabled": True}}})
    assert app.route_plan is not None
    assert any(t["name"] == "plan_trade_route" for t in app.registry.tools())
    assert "trade routes" in app.registry.categories()


def test_flag_on_but_feature_disabled_stays_off(tmp_path):
    # The experimental flag is the GATE, not the switch: with [route_plan].enabled false it stays off
    # even with the flag on (the feature's own toggle is still required).
    app = _app(tmp_path, {"route_plan": {"enabled": False},
                          "experimental": {"trade_route": {"enabled": True}}})
    assert getattr(app, "route_plan", None) is None
    assert not any(t["name"] == "plan_trade_route" for t in app.registry.tools())


# --- 3a. seam: crew voicing --------------------------------------------------

def test_crew_is_enabled_requires_both_toggle_and_flag():
    from covas import crew
    assert crew.is_enabled({"crew": {"enabled": True}}) is False   # flag off (public default)
    assert crew.is_enabled({"crew": {"enabled": True},
                            "experimental": {"crew": {"enabled": True}}}) is True
    assert crew.is_enabled({"crew": {"enabled": False},
                            "experimental": {"crew": {"enabled": True}}}) is False


# --- 3b. seam: music director -----------------------------------------------

def test_music_director_gated_by_flag():
    from covas.mixer.music import MusicDirector
    on = {"music": {"enabled": True}}
    assert MusicDirector.from_cfg(on)._enabled is False   # flag off -> built disabled
    on_flagged = {"music": {"enabled": True}, "experimental": {"music": {"enabled": True}}}
    assert MusicDirector.from_cfg(on_flagged)._enabled is True


# --- 3c. seam: experimental TTS providers -----------------------------------

def _stub_tts(monkeypatch, module_name: str, class_name: str):
    """Stub a lazily-imported provider module so make_tts resolves to it without the real SDK."""
    from covas.providers import factory  # noqa: F401 — ensures package import
    mod = types.ModuleType(f"covas.providers.{module_name}")

    class _Stub:
        def __init__(self, cfg, **kwargs):
            self.cfg = cfg
    _Stub.__name__ = class_name
    setattr(mod, class_name, _Stub)
    monkeypatch.setitem(sys.modules, f"covas.providers.{module_name}", mod)
    return _Stub


def test_make_tts_azure_requires_flag(monkeypatch):
    from covas.providers import factory
    stub = _stub_tts(monkeypatch, "azure_tts", "AzureTTS")
    with pytest.raises(ValueError) as exc:
        factory.make_tts({"tts": {"provider": "azure"}})
    assert "experimental" in str(exc.value) and "azure_tts" in str(exc.value)
    tts = factory.make_tts({"tts": {"provider": "azure"},
                            "experimental": {"azure_tts": {"enabled": True}}})
    assert isinstance(tts, stub)


def test_make_tts_cartesia_requires_flag(monkeypatch):
    from covas.providers import factory
    stub = _stub_tts(monkeypatch, "cartesia_tts", "CartesiaTTS")
    with pytest.raises(ValueError):
        factory.make_tts({"tts": {"provider": "cartesia"}})
    tts = factory.make_tts({"tts": {"provider": "cartesia"},
                            "experimental": {"cartesia_tts": {"enabled": True}}})
    assert isinstance(tts, stub)


def test_make_tts_nonexperimental_provider_unaffected(monkeypatch):
    from covas.providers import factory
    stub = _stub_tts(monkeypatch, "elevenlabs_tts", "ElevenLabsTTS")
    assert isinstance(factory.make_tts({"tts": {"provider": "elevenlabs"}}), stub)


# --- 3d. seam: voice activation (listen mode) -------------------------------

def test_listen_mode_forced_to_ptt_without_flag(tmp_path):
    app = _app(tmp_path, {"listen": {"mode": "continuous"}})
    assert app._listen_mode() == "ptt"


def test_listen_mode_honours_continuous_with_flag(tmp_path):
    app = _app(tmp_path, {"listen": {"mode": "continuous"},
                          "experimental": {"voice_activation": {"enabled": True}}})
    assert app._listen_mode() == "continuous"


# --- 3d-bis. seam: HUD "enabled" helpers gate the prompt-context hint --------

def test_hud_enabled_helpers_gated_by_flag(tmp_path):
    # [hud].enabled alone must not report the HUD active (it feeds the LLM's hud_active hint) —
    # the experimental flag gates all three surface helpers, matching the registration gate.
    off = _app(tmp_path, {"hud": {"enabled": True, "vr_enabled": True, "web_enabled": True}})
    assert off._hud_enabled() is False
    assert off._vr_hud_enabled() is False and off._web_hud_enabled() is False
    on = _app(tmp_path, {"hud": {"enabled": True},
                         "experimental": {"hud": {"enabled": True}}})
    assert on._hud_enabled() is True


# --- 3e. seam: the public Settings option surface ---------------------------

def test_public_options_hides_experimental_providers():
    from covas import settings_schema as schema
    prov = schema.by_key["tts.provider"]
    base = list(prov.options)
    off = schema.public_options({}, prov, base)
    assert "azure" not in off and "cartesia" not in off
    assert "elevenlabs" in off and "edge" in off   # non-experimental choices survive
    # Flag one on → that choice reappears, the other stays hidden.
    on = schema.public_options({"experimental": {"azure_tts": {"enabled": True}}}, prov, base)
    assert "azure" in on and "cartesia" not in on


def test_public_options_hides_continuous_listen_mode():
    from covas import settings_schema as schema
    mode = schema.by_key["listen.mode"]
    base = list(mode.options)
    assert "continuous" not in schema.public_options({}, mode, base)
    on = schema.public_options({"experimental": {"voice_activation": {"enabled": True}}}, mode, base)
    assert "continuous" in on


# --- 4. self-enable via overrides.json + no leak into public settings -------

def test_overrides_json_self_enables_a_flag(tmp_path, monkeypatch):
    # The real config precedence: shipped config.toml < data-dir config.toml < overrides.json.
    # A flag set ONLY in overrides.json must flip the feature on for this install alone.
    monkeypatch.setenv("COVAS_DATA_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        "[experimental.trade_route]\nenabled = false\n", encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"experimental": {"trade_route": {"enabled": True}}}), encoding="utf-8")
    cfg = config.load_config()
    assert experimental(cfg, "trade_route") is True


def test_experimental_flags_absent_from_public_settings_schema():
    # No experimental toggle may be projected onto the public Settings page/voice surface.
    from covas import settings_schema as schema
    assert not any(s.path and s.path[0] == "experimental" for s in schema.SCHEMA)
    assert not any(s.key.startswith("experimental") for s in schema.SCHEMA)


def test_shipped_config_defaults_every_experimental_flag_off():
    # The shipped config.toml must ship all nine flags OFF (the public-safe default).
    import tomllib
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    with open(root / "config.toml", "rb") as f:
        toml = tomllib.load(f)
    exp = toml.get("experimental", {})
    expected = {"azure_tts", "cartesia_tts", "voice_activation", "crew", "trade_route",
                "macro", "auto_reflex", "music", "hud"}
    assert set(exp) == expected
    for name, sub in exp.items():
        assert sub.get("enabled") is False, f"[experimental.{name}] must ship disabled"
