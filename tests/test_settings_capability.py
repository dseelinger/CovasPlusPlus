"""Unit tests for the voice settings capability (Prompt N2) — offline, free.

Two layers:
  * pure dialog logic over an in-memory config (fake apply/options) — resolve a spoken
    setting, coerce a natural value, refuse bad values WITH options, route unknowns to help;
  * a real-App round-trip proving a spoken change lands in overrides.json AND takes effect
    in the live config (through the same update_settings path the web page uses).

Everything projects from the ONE schema (Prompt N1), so voice + web can't drift.
"""
from __future__ import annotations

import json

import pytest

from covas import config
from covas import settings_schema as schema
from covas.app import App
from covas.capabilities.settings_capability import SettingsCapability, find_settings
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


# --- pure matching ---------------------------------------------------------

@pytest.mark.parametrize("spoken,key", [
    ("personality", "personality.enabled"),
    ("thinking", "anthropic.thinking.default"),
    ("whisper model", "whisper.model"),
    ("the voice", "elevenlabs.voice_id"),
    ("web search", "web_search.enabled"),
    ("max tokens", "anthropic.max_tokens"),
    ("the model", "anthropic.model"),
])
def test_find_settings_resolves_phrasings(spoken, key):
    matches = find_settings(spoken)
    assert [m.key for m in matches] == [key]


def test_find_settings_unknown_is_empty():
    assert find_settings("warp drive charge") == []


def test_find_settings_ambiguous_returns_several():
    matches = find_settings("search size")
    assert len(matches) > 1  # nav / star_systems / search all have a search size


def test_hidden_settings_are_never_matched():
    assert find_settings("elevenlabs voice name") != [schema.by_key["elevenlabs.voice_name"]]
    assert all(not m.hidden for m in find_settings("voice name"))


# --- protected safety gates (issue #183) -----------------------------------
# The keybind/macro/comms safety toggles are carved OUT of the voice-writable surface: the LLM
# consumes untrusted text (web results, in-game/NPC strings, poisoned memory), so a guard it can
# flip via set_setting is a prompt-injection privilege-escalation path. They stay web-editable.

_PROTECTED_KEYS = [
    "keybinds.enabled", "keybinds.require_confirmation", "keybinds.combat_guard",
    "macros.require_confirmation", "macros.combat_guard", "macros.mode_guard",
    "comms_send.enabled",
]


def test_the_expected_guards_are_flagged_protected():
    # Every guard we mean to carve out is marked, and no non-guard setting is accidentally hidden
    # from the voice surface by the flag.
    flagged = {s.key for s in schema.SCHEMA if s.protected}
    assert flagged == set(_PROTECTED_KEYS)


@pytest.mark.parametrize("spoken", [
    "keybind automation", "ship controls", "keybind confirmation", "combat guard",
    "macro confirmation", "macro combat guard", "macro mode guard",
    "send messages", "in-game messages", "voice comms",
])
def test_find_settings_never_surfaces_a_protected_gate(spoken):
    # The default voice resolver must NEVER return a protected setting for a guard-naming phrase...
    assert all(not s.protected for s in find_settings(spoken)), spoken
    # ...but the phrase DOES resolve to a protected gate under the opt-in flag, so the capability
    # can detect it and name it in the refusal.
    assert any(s.protected for s in find_settings(spoken, include_protected=True)), spoken


def test_set_setting_refuses_each_protected_guard(cap):
    c, cfg, writes = cap
    for spoken in ("keybind automation", "keybind confirmation", "combat guard",
                   "macro confirmation", "macro combat guard", "macro mode guard",
                   "send messages"):
        out = c.run_tool("set_setting", {"setting": spoken, "value": "off"})
        low = out.lower()
        assert "safety control" in low, spoken          # named as a guard, not "unknown"
        assert "control panel" in low, spoken           # routed to the web panel
        assert "what can i change" not in low, spoken   # NOT the generic unknown-setting reply
    assert writes == []                                 # nothing was persisted


def test_set_setting_refusal_names_the_armed_guard(cap):
    c, _, _ = cap
    out = c.run_tool("set_setting", {"setting": "combat guard", "value": "off"})
    assert "Combat guard" in out                        # the actual guard label is spoken back


def test_get_setting_also_refuses_protected_guard(cap):
    # Reading a guard by voice is likewise carved out (find_settings is the shared resolver), so a
    # probe like "is the combat guard on" is answered with the carve-out, not the live value.
    c, _, _ = cap
    out = c.run_tool("get_setting", {"setting": "combat guard"})
    assert "safety control" in out.lower()


# --- pure capability dialog ------------------------------------------------

@pytest.fixture()
def cap():
    """A capability over an in-memory config, with fake apply + dynamic options."""
    cfg = {
        "personality": {"enabled": True},
        "anthropic": {"thinking": {"default": "Off"}, "model": "claude-sonnet-5",
                      "available_models": ["claude-opus-4-8", "claude-sonnet-5",
                                           "claude-haiku-4-5-20251001"],
                      "max_tokens": 1024},
        "web_search": {"enabled": True, "max_uses": 3},
        "whisper": {"model": "small"},
        "elevenlabs": {"voice_id": "id_sarah", "voice_name": "Sarah"},
    }
    writes: list[dict] = []

    def apply(patch):
        config.deep_merge(cfg, patch)
        writes.append(patch)

    def options(s):
        if s.options_source == schema.OPT_EL_VOICES:
            return [("id_sarah", "Sarah [premade]"), ("id_george", "George [premade]")]
        if s.options_source == schema.OPT_MODELS:
            return [(m, m) for m in cfg["anthropic"]["available_models"]]
        return None

    c = SettingsCapability(get_value=lambda s: schema.get_value(cfg, s),
                           apply_patch=apply, options_for=options)
    return c, cfg, writes


def test_set_bool_by_phrasing(cap):
    c, cfg, writes = cap
    out = c.run_tool("set_setting", {"setting": "personality", "value": "off"})
    assert out == "Personality turned off."
    assert cfg["personality"]["enabled"] is False
    assert writes[-1] == {"personality": {"enabled": False}}


def test_set_enum_by_natural_value(cap):
    c, cfg, _ = cap
    out = c.run_tool("set_setting", {"setting": "thinking", "value": "high"})
    assert out == "Thinking depth set to High."
    assert cfg["anthropic"]["thinking"]["default"] == "High"


def test_set_number_coerces_and_confirms(cap):
    c, cfg, _ = cap
    out = c.run_tool("set_setting", {"setting": "max tokens", "value": "2000"})
    assert out == "Max reply tokens set to 2000."
    assert cfg["anthropic"]["max_tokens"] == 2000


def test_set_voice_by_name_pairs_id_and_name(cap):
    c, cfg, _ = cap
    out = c.run_tool("set_setting", {"setting": "the voice", "value": "George"})
    assert out == "ElevenLabs voice set to George."
    assert cfg["elevenlabs"] == {"voice_id": "id_george", "voice_name": "George"}


def test_set_model_by_shorthand(cap):
    c, cfg, _ = cap
    out = c.run_tool("set_setting", {"setting": "the model", "value": "opus"})
    assert cfg["anthropic"]["model"] == "claude-opus-4-8"
    assert "claude-opus-4-8" in out


def test_invalid_number_refused_with_range(cap):
    c, cfg, writes = cap
    out = c.run_tool("set_setting", {"setting": "max tokens", "value": "999999"})
    assert "at most 8192" in out
    assert cfg["anthropic"]["max_tokens"] == 1024  # unchanged
    assert writes == []


def test_invalid_enum_refused_with_options(cap):
    c, cfg, writes = cap
    out = c.run_tool("set_setting", {"setting": "whisper model", "value": "gigantic"})
    assert "gigantic" in out
    for opt in ("tiny", "small", "large-v3"):
        assert opt in out          # the valid options are spoken back
    assert writes == []            # nothing applied


def test_unknown_setting_routes_to_help(cap):
    c, _, writes = cap
    out = c.run_tool("set_setting", {"setting": "warp drive", "value": "on"})
    assert "what can i change" in out.lower()
    assert writes == []


def test_ambiguous_setting_asks_which(cap):
    c, _, writes = cap
    out = c.run_tool("set_setting", {"setting": "search size", "value": "10"})
    assert "did you mean" in out.lower()
    assert writes == []


def test_placement_verb_over_hud_declines_instead_of_ambiguous(cap):
    # #141/§3.8.1: 'pin/place/position the HUD here' is a VR-only look-to-place action, so the
    # settings path defers to adjust_vr_hud rather than emitting the multi-HUD ambiguous list —
    # WITH OR WITHOUT the word "VR".
    c, _, writes = cap
    for phrase in ("pin the hud here", "place the hud here", "position the hud there",
                   "recentre the hud on me"):
        out = c.run_tool("set_setting", {"setting": phrase, "value": "here"})
        assert "did you mean" not in out.lower(), phrase
        assert "pin the hud here" in out.lower(), phrase
        assert writes == []


def test_plain_hud_toggle_still_works_under_the_placement_guard(cap):
    # The guard must not eat ordinary toggles: exact "hud" still resolves (exact-wins), and
    # "turn the hud on" isn't a placement phrase.
    from covas.capabilities.settings_capability import is_placement_phrase
    assert not is_placement_phrase("hud")
    assert not is_placement_phrase("turn the hud on")
    assert not is_placement_phrase("vr hud tilt")
    assert is_placement_phrase("pin the hud here")
    assert is_placement_phrase("position the HUD there")
    assert is_placement_phrase("recenter the hud")


def test_get_reports_current_value(cap):
    c, cfg, _ = cap
    assert c.run_tool("get_setting", {"setting": "personality"}) == "Personality is on."
    cfg["anthropic"]["thinking"]["default"] = "Medium"
    assert c.run_tool("get_setting", {"setting": "thinking"}) == "Thinking depth is Medium."


def test_help_meta_is_complete():
    from covas.capabilities.base import help_meta_problems
    c = SettingsCapability(get_value=lambda s: None, apply_patch=lambda p: None)
    assert help_meta_problems(c.help_meta()) == []


# --- real-App round-trip to overrides.json ---------------------------------

def _app_cfg(tmp_path) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] x\n", encoding="utf-8")
    return {
        "keys": {"push_to_talk": "["},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "whisper": {"model": "small", "n_threads": 4},
        "anthropic": {"model": "claude-sonnet-5",
                      "available_models": ["claude-opus-4-8", "claude-sonnet-5"],
                      "max_tokens": 1024, "thinking": {"default": "Off"}},
        "web_search": {"enabled": True},
        "personality": {"enabled": True},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "x", "voice_name": "Sarah"},
        "conversation": {"max_turns": 20},
        "elite": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "logging": {"dir": str(tmp_path / "logs")},
    }


@pytest.fixture()
def app(tmp_path, monkeypatch):
    ov = tmp_path / "overrides.json"
    monkeypatch.setattr(config, "OVERRIDES_PATH", ov)
    core = App(_app_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    return core, ov


def test_voice_set_bool_round_trips_to_overrides(app):
    core, ov = app
    out = core.settings_cap.run_tool("set_setting", {"setting": "personality", "value": "off"})
    assert out == "Personality turned off."
    assert core.cfg["personality"]["enabled"] is False            # takes effect
    assert json.loads(ov.read_text())["personality"]["enabled"] is False  # persists


def test_voice_set_enum_round_trips_to_overrides(app):
    core, ov = app
    core.settings_cap.run_tool("set_setting", {"setting": "thinking", "value": "medium"})
    assert core.cfg["anthropic"]["thinking"]["default"] == "Medium"
    assert json.loads(ov.read_text())["anthropic"]["thinking"]["default"] == "Medium"


def test_voice_set_number_round_trips_to_overrides(app):
    core, ov = app
    core.settings_cap.run_tool("set_setting", {"setting": "max tokens", "value": "1536"})
    assert core.cfg["anthropic"]["max_tokens"] == 1536
    assert json.loads(ov.read_text())["anthropic"]["max_tokens"] == 1536


def test_settings_capability_is_registered_on_the_app(app):
    core, _ = app
    assert "settings" in core.registry.categories()
    names = {t["name"] for t in core.registry.tools()}
    assert {"get_setting", "set_setting"} <= names
