"""Unit tests for the Personality tab, voice speed, and composition (N7) — offline, free.

Covers: presets.md parsing, Base+Persona+Campaign composition (incl. persona selection,
disabled, and the legacy campaign fallback), save-as-custom round-tripping to a git-ignored
dir, the TTS speed clamp/payload, and the web personality endpoints.
"""
from __future__ import annotations

import pytest

from covas import config
from covas import personality as persona
from covas.app import App
from covas.tts import build_tts_body
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


_PRESETS = """# Title

## Base — applied to every persona (do not delete)

You are the onboard computer. Speak for the ear.

---

## Persona — Classic  *(current default)*

Dry, precise, quietly amused.

> *"That's a fine plan, Commander."*

---

## Persona — The Deadpan Cynic

Flat, sardonic, gloriously pessimistic.

> *"Another system. Something wants us dead."*
"""


# --- parsing ---------------------------------------------------------------

def test_parse_presets_base_and_personas():
    base, personas = persona.parse_presets(_PRESETS)
    assert "onboard computer" in base and "---" not in base
    assert [p["name"] for p in personas] == ["Classic", "The Deadpan Cynic"]
    assert "Dry, precise" in personas[0]["body"]
    assert personas[0]["preview"] == "That's a fine plan, Commander."
    assert ">" not in personas[0]["body"]          # preview kept out of the body


def test_parse_real_presets_file():
    base, personas = persona.parse_presets(
        (config.ROOT / "personalities" / "presets.md").read_text(encoding="utf-8"))
    assert base and len(personas) >= 10
    assert any(p["name"] == "Classic" for p in personas)


def test_every_preset_body_carries_in_character_examples():
    # Issue #98: personas must ship few-shot example lines *in the body* (what the model sees),
    # while the blockquote stays a UI-only preview. Guards the parser split + the content contract.
    base, personas = persona.parse_presets(
        (config.ROOT / "personalities" / "presets.md").read_text(encoding="utf-8"))
    # Base must keep the voice-persistence + thinking-preservation guidance.
    assert "in character" in base.lower()
    for p in personas:
        assert ">" not in p["body"], f"{p['name']}: preview quote leaked into the body"
        assert p["preview"], f"{p['name']}: preview quote not extracted"
        assert "In character" in p["body"], f"{p['name']}: no in-character examples in body"


_MULTI_EXAMPLE = """# Title

## Base — applied to every persona (do not delete)

You are the onboard computer. Stay in character on every reply.

---

## Persona — Example Voice

Terse and wry. Voice: clipped, "Copy," a dry aside.

In character — asked to fly the ship: "Not my console. Heading: two-ninety."
Handing over a number: "Fuel at eight percent. Barely."
Flagging a bad idea: "Four ships, one of us. Your call."

> *"The illustrative preview quote, Commander."*
"""


def test_multi_example_body_splits_cleanly_from_preview():
    base, personas = persona.parse_presets(_MULTI_EXAMPLE)
    assert "every reply" in base
    (p,) = personas
    assert p["name"] == "Example Voice"
    # All three example beats survive in the body...
    assert "Not my console" in p["body"]
    assert "eight percent" in p["body"]
    assert "Four ships" in p["body"]
    # ...and none of the blockquote preview bleeds in.
    assert ">" not in p["body"]
    assert "illustrative preview quote" not in p["body"]
    assert p["preview"] == "The illustrative preview quote, Commander."


# --- composition -----------------------------------------------------------

def _cfg(tmp_path, *, persona_name="Classic", enabled=True):
    presets = tmp_path / "presets.md"
    presets.write_text(_PRESETS, encoding="utf-8")
    return {"personality": {
        "enabled": enabled, "persona": persona_name,
        "presets_file": str(presets),
        "campaign_file": str(tmp_path / "campaign.txt"),
        "custom_dir": str(tmp_path / "custom"),
        "file": str(tmp_path / "legacy_none.txt"),
    }}


def test_compose_base_persona_no_campaign(tmp_path):
    out = persona.compose_system(_cfg(tmp_path, persona_name="The Deadpan Cynic"))
    assert "onboard computer" in out and "sardonic" in out
    assert "campaign" not in out.lower()          # no campaign file yet


def test_compose_includes_campaign(tmp_path):
    cfg = _cfg(tmp_path)
    persona.save_campaign(cfg, "I am CMDR Test, Elite in exploration.")
    out = persona.compose_system(cfg)
    assert "CMDR Test" in out and "Dry, precise" in out    # Classic persona + campaign


def test_compose_disabled_is_none(tmp_path):
    assert persona.compose_system(_cfg(tmp_path, enabled=False)) is None


def test_persona_selection_falls_back_to_first(tmp_path):
    out = persona.compose_system(_cfg(tmp_path, persona_name="Nonexistent"))
    assert "Dry, precise" in out                  # unknown -> first persona (Classic)


def test_legacy_campaign_fallback(tmp_path):
    # No campaign file, but a legacy personality.txt exists -> used as the campaign (migration).
    cfg = _cfg(tmp_path)
    legacy = tmp_path / "legacy.txt"
    legacy.write_text("Legacy commander facts here.", encoding="utf-8")
    cfg["personality"]["file"] = str(legacy)
    assert "Legacy commander facts" in persona.compose_system(cfg)


# --- custom personas -------------------------------------------------------

def test_save_custom_persona_round_trips(tmp_path):
    cfg = _cfg(tmp_path)
    persona.save_custom_persona(cfg, "My Voice", "Terse and calm.")
    names = [p["name"] for p in persona.list_personas(cfg)]
    assert "My Voice" in names
    found = persona.find_persona(cfg, "my voice")     # case-insensitive
    assert found["source"] == "custom" and "Terse and calm" in found["body"]
    # written under the git-ignored custom dir
    assert list((tmp_path / "custom").glob("*.md"))


def test_custom_overrides_preset_of_same_name(tmp_path):
    cfg = _cfg(tmp_path)
    persona.save_custom_persona(cfg, "Classic", "My reworked classic.")
    classics = [p for p in persona.list_personas(cfg) if p["name"] == "Classic"]
    assert len(classics) == 1 and classics[0]["source"] == "custom"


# --- TTS speed -------------------------------------------------------------

def test_speed_default_omits_voice_settings():
    assert build_tts_body({"elevenlabs": {"model": "m", "speed": 1.0}}, "hi") == {
        "text": "hi", "model_id": "m"}


def test_speed_included_when_raised():
    b = build_tts_body({"elevenlabs": {"model": "m", "speed": 1.1}}, "x")
    assert b["voice_settings"] == {"speed": 1.1}


def test_speed_clamped_to_range():
    assert build_tts_body({"elevenlabs": {"model": "m", "speed": 5.0}}, "x")["voice_settings"] == {"speed": 1.2}
    # below 1.0 clamps to 1.0 -> omitted (default request unchanged)
    assert "voice_settings" not in build_tts_body({"elevenlabs": {"model": "m", "speed": 0.5}}, "x")


def test_speed_bad_value_defaults():
    assert "voice_settings" not in build_tts_body({"elevenlabs": {"model": "m", "speed": "fast"}}, "x")


# --- web endpoints ---------------------------------------------------------

def _app_cfg(tmp_path) -> dict:
    checklist = tmp_path / "cl.md"
    checklist.write_text("- [ ] x\n", encoding="utf-8")
    presets = tmp_path / "presets.md"
    presets.write_text(_PRESETS, encoding="utf-8")
    return {
        "keys": {"push_to_talk": "["},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "whisper": {"model": "small", "device": "cpu", "compute_type": "int8"},
        "anthropic": {"model": "claude-sonnet-5", "available_models": ["claude-sonnet-5"],
                      "max_tokens": 1024, "thinking": {"default": "Off"}},
        "web_search": {"enabled": True},
        "personality": {"enabled": True, "persona": "Classic", "presets_file": str(presets),
                        "campaign_file": str(tmp_path / "campaign.txt"),
                        "custom_dir": str(tmp_path / "custom"),
                        "file": str(tmp_path / "none.txt")},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "x", "voice_name": "S", "speed": 1.0},
        "conversation": {"max_turns": 20},
        "elite": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "logging": {"dir": str(tmp_path / "logs")},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from covas.web import create_app
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    core = App(_app_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    app = create_app(core)
    app.config.update(TESTING=True)
    return app.test_client(), core


def test_api_personality_lists_and_selected(client):
    c, _ = client
    data = c.get("/api/personality").get_json()
    assert data["selected"] == "Classic"
    assert {p["name"] for p in data["personas"]} == {"Classic", "The Deadpan Cynic"}
    assert data["campaign"] == ""


def test_select_persona_persists(client):
    c, core = client
    r = c.post("/api/personality/select", json={"persona": "The Deadpan Cynic"})
    assert r.status_code == 200
    assert core.cfg["personality"]["persona"] == "The Deadpan Cynic"


def test_select_unknown_persona_rejected(client):
    c, _ = client
    assert c.post("/api/personality/select", json={"persona": "Nope"}).status_code == 400


def test_save_campaign_round_trips(client):
    c, core = client
    c.post("/api/personality/campaign", json={"campaign": "I am CMDR Web."})
    assert "CMDR Web" in c.get("/api/personality").get_json()["campaign"]


def test_save_custom_selects_it(client):
    c, core = client
    r = c.post("/api/personality/custom", json={"name": "Gruff", "body": "Few words."})
    assert r.status_code == 200
    assert core.cfg["personality"]["persona"] == "Gruff"
    assert "Gruff" in {p["name"] for p in c.get("/api/personality").get_json()["personas"]}


def test_index_page_has_personality_speed_and_logfilter(client):
    c, _ = client
    html = c.get("/").get_data(as_text=True)
    # The voice-speed control is now rendered generically inside the provider-shaped Speech block
    # (issue #86), not a static id="speed" element — assert the block host instead.
    for marker in ('id="persona"', 'id="campaign"', 'id="ttsBlock"', 'id="fConv"', 'id="fAll"',
                   'conv-only'):
        assert marker in html
