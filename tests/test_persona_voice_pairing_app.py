"""App-level tests for auto persona->voice pairing wiring (issue #96).

Offline + free: they drive `App.update_settings` with fakes and monkeypatched provider builders
(no network, no real TTS/LLM), proving the reconcile rules:
  * selecting a persona dresses it in its PAIRED default voice;
  * an EXPLICIT user voice for a persona always wins and is never overwritten;
  * a manual voice change while a persona is active is REMEMBERED as that persona's explicit voice;
  * the one-time background pairing call is GATED (opt-in + ElevenLabs + a non-lean tiering level).
"""
from __future__ import annotations

import pytest

from covas import app as app_mod
from covas.app import App
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path, **personality) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    pers = {"enabled": True, "persona": "Classic", "auto_voice_pairing": False}
    pers.update(personality)
    return {
        "llm": {"provider": "anthropic"},
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "available_models": ["claude-haiku-4-5", "claude-sonnet-5"],
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "tts": {"provider": "elevenlabs"},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "v_classic",
                       "voice_name": "Sarah", "speed": 1.0},
        "web_search": {"enabled": False},
        "personality": pers,
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "whisper": {"model": "small", "n_threads": 4},
        "keys": {"push_to_talk": "right ctrl"},
    }


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No overrides.json writes, and provider rebuilds return fakes (never touch the network)."""
    monkeypatch.setattr(app_mod, "save_overrides", lambda o: None)
    monkeypatch.setattr(app_mod, "make_tts", lambda cfg, mixer=None: FakeTTS())
    monkeypatch.setattr(app_mod, "make_llm", lambda cfg: FakeLLM())


def _app(tmp_path, **personality):
    app = App(_cfg(tmp_path, **personality), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    app._voice_names = {"v_classic": "Sarah", "v_gruff": "Bruno"}
    return app


def test_selecting_a_persona_applies_its_paired_voice(tmp_path):
    app = _app(tmp_path)
    app._voice_pairings = {"gruff": "v_gruff"}
    app.update_settings({"personality": {"persona": "Gruff"}})
    assert app.cfg["elevenlabs"]["voice_id"] == "v_gruff"
    assert app.cfg["elevenlabs"]["voice_name"] == "Bruno"     # display name resolved from catalog


def test_explicit_user_voice_wins_and_is_not_overwritten(tmp_path):
    app = _app(tmp_path, persona_voices={"Gruff": "v_user_pick"})
    app._voice_pairings = {"gruff": "v_gruff"}                 # a paired default exists...
    app.update_settings({"personality": {"persona": "Gruff"}})
    assert app.cfg["elevenlabs"]["voice_id"] == "v_user_pick"  # ...but the explicit choice wins


def test_manual_voice_change_is_remembered_as_explicit(tmp_path):
    app = _app(tmp_path)                                       # persona "Classic"
    app.update_settings({"elevenlabs": {"voice_id": "v_manual", "voice_name": "Custom"}})
    assert app.cfg["personality"]["persona_voices"]["Classic"] == "v_manual"
    # And once remembered, re-selecting Classic keeps the user's voice over any future pairing.
    app._voice_pairings = {"classic": "v_classic"}
    app.update_settings({"personality": {"persona": "Other"}})
    app.update_settings({"personality": {"persona": "Classic"}})
    assert app.cfg["elevenlabs"]["voice_id"] == "v_manual"


def test_no_pairing_no_apply_keeps_current_voice(tmp_path):
    app = _app(tmp_path)                                       # no _voice_pairings set
    app.update_settings({"personality": {"persona": "Gruff"}})
    assert app.cfg["elevenlabs"]["voice_id"] == "v_classic"    # unchanged — nothing to apply


def test_pairing_is_gated_off_for_non_elevenlabs_and_lean(tmp_path, monkeypatch):
    app = _app(tmp_path)
    # Opt-in on + ElevenLabs + Full level -> allowed.
    app.cfg["personality"]["auto_voice_pairing"] = True
    app.text_only = False
    assert app._voice_pairing_allowed() is True
    # Non-ElevenLabs TTS -> a voice_id pairing doesn't apply -> skipped.
    app.cfg["tts"]["provider"] = "edge"
    assert app._voice_pairing_allowed() is False
    app.cfg["tts"]["provider"] = "elevenlabs"
    # A lean/constrained tiering level (proactive off) -> skipped.
    import covas.tiering as tiering
    app.tier_level = tiering.LEVELS["Lean"]
    assert app._voice_pairing_allowed() is False
    app.tier_level = tiering.LEVELS["Full"]
    # Opted out -> skipped.
    app.cfg["personality"]["auto_voice_pairing"] = False
    assert app._voice_pairing_allowed() is False
