"""Unit tests for the schema-driven settings web layer (Prompt N1).

Offline and free: a real App is built with injected fake providers (DESIGN §9),
config.OVERRIDES_PATH is redirected to a tmp file, and Flask's test client drives
the endpoints. These prove the three N1 server-side guarantees end to end:
  * a valid POST round-trips into overrides.json and the running config,
  * out-of-range / unknown / bad-type writes are rejected (400) and NOTHING is
    written to overrides.json, and
  * a reset drops the key from overrides.json (back to the config.toml default).
"""
from __future__ import annotations

import json

import pytest

from covas import config
from covas.app import App
from covas.web import create_app
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path) -> dict:
    """A config with every section the web layer + public_settings read. elite is
    OFF (no watcher threads), audio/cues inert (no hardware), checklist on tmp."""
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    return {
        "keys": {"push_to_talk": "[", "tap_cancel_ms": 400, "cancel": ""},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "whisper": {"model": "small", "device": "cpu", "compute_type": "int8", "language": "en"},
        "anthropic": {
            "model": "claude-sonnet-5",
            "available_models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
            "max_tokens": 1024, "cache_ttl": "1h",
            "thinking": {"default": "Off"},
        },
        "router": {"enabled": True, "pin": "", "full_breakdown_max_tokens": 2048},
        "web_search": {"enabled": True, "max_uses": 3},
        "personality": {"enabled": True},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "EXAVITQu4vr4xnSDxMaL",
                       "voice_name": "Sarah", "output_format": "pcm_16000"},
        "conversation": {"max_turns": 20},
        "elite": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "logging": {"dir": str(tmp_path / "logs")},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Redirect overrides.json to a tmp file BEFORE the App reads/writes it, so the
    # developer's real overrides are never touched and round-trips are observable.
    ov = tmp_path / "overrides.json"
    monkeypatch.setattr(config, "OVERRIDES_PATH", ov)
    core = App(_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    flask_app = create_app(core)
    flask_app.config.update(TESTING=True)
    return flask_app.test_client(), core, ov


# --- schema endpoint -------------------------------------------------------

def test_api_schema_lists_grouped_settings_with_values(client):
    c, core, _ = client
    data = c.get("/api/schema").get_json()
    flat = {s["key"]: s for g in data["groups"] for s in g["settings"]}
    assert flat["anthropic.model"]["value"] == "claude-sonnet-5"
    # dynamic model options are resolved server-side from config
    assert "claude-opus-4-8" in flat["anthropic.model"]["options"]
    # nothing overridden yet
    assert flat["web_search.enabled"]["overridden"] is False


# --- valid write round-trips ----------------------------------------------

def test_valid_update_persists_to_overrides_and_config(client):
    c, core, ov = client
    r = c.post("/api/settings/update", json={"updates": {"anthropic.max_tokens": 2000}})
    assert r.status_code == 200
    assert core.cfg["anthropic"]["max_tokens"] == 2000
    assert json.loads(ov.read_text())["anthropic"]["max_tokens"] == 2000


def test_nested_enum_update_round_trips(client):
    c, core, ov = client
    r = c.post("/api/settings/update", json={"updates": {"anthropic.thinking.default": "High"}})
    assert r.status_code == 200
    assert core.cfg["anthropic"]["thinking"]["default"] == "High"
    assert json.loads(ov.read_text())["anthropic"]["thinking"]["default"] == "High"


def test_single_key_form_is_accepted(client):
    c, core, _ = client
    r = c.post("/api/settings/update", json={"key": "web_search.enabled", "value": False})
    assert r.status_code == 200
    assert core.cfg["web_search"]["enabled"] is False


# --- rejections never write ------------------------------------------------

def test_out_of_range_rejected_and_nothing_written(client):
    c, core, ov = client
    r = c.post("/api/settings/update", json={"updates": {"anthropic.max_tokens": 999999}})
    assert r.status_code == 400
    assert "anthropic.max_tokens" in r.get_json()["errors"]
    assert core.cfg["anthropic"]["max_tokens"] == 1024  # unchanged
    assert not ov.exists() or "anthropic" not in json.loads(ov.read_text())


def test_unknown_key_rejected(client):
    c, core, ov = client
    r = c.post("/api/settings/update", json={"updates": {"bogus.setting": 1}})
    assert r.status_code == 400
    assert r.get_json()["errors"]["bogus.setting"] == "unknown setting"
    assert not ov.exists()


def test_bad_enum_value_rejected(client):
    c, _, ov = client
    r = c.post("/api/settings/update", json={"updates": {"whisper.model": "gigantic"}})
    assert r.status_code == 400
    assert "whisper.model" in r.get_json()["errors"]
    assert not ov.exists()


def test_batch_with_one_bad_field_is_all_or_nothing(client):
    """A single invalid field aborts the whole batch — the valid sibling in the
    same request must NOT be written."""
    c, core, ov = client
    r = c.post("/api/settings/update", json={"updates": {
        "web_search.max_uses": 2,          # valid
        "anthropic.max_tokens": 999999,    # invalid
    }})
    assert r.status_code == 400
    assert core.cfg["web_search"]["max_uses"] == 3  # not applied
    assert not ov.exists()


# --- reset -----------------------------------------------------------------

def test_reset_drops_key_from_overrides(client):
    c, core, ov = client
    c.post("/api/settings/update", json={"updates": {"anthropic.max_tokens": 2000}})
    assert json.loads(ov.read_text())["anthropic"]["max_tokens"] == 2000

    r = c.post("/api/settings/reset", json={"key": "anthropic.max_tokens"})
    assert r.status_code == 200
    # config.toml default is restored and the override husk is pruned away
    assert core.cfg["anthropic"]["max_tokens"] == 1024
    assert "anthropic" not in json.loads(ov.read_text())


def test_reset_unknown_key_is_rejected(client):
    c, _, _ = client
    r = c.post("/api/settings/reset", json={"key": "nope.nope"})
    assert r.status_code == 400


# --- legacy quick-config endpoint still validates --------------------------

def test_legacy_endpoint_routes_through_schema(client):
    c, core, ov = client
    ok = c.post("/api/settings", json={"personality": False})
    assert ok.status_code == 200
    assert core.cfg["personality"]["enabled"] is False

    bad = c.post("/api/settings", json={"whisper": "gigantic"})
    assert bad.status_code == 400
