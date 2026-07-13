"""Offline unit tests for the first-run wizard server (covas/setup_web.py, I3).

Drives the /api/setup/* endpoints with Flask's test client. Key writes go to tmp files;
the STT download, mic enumeration, and ElevenLabs fetch are monkeypatched so nothing touches
the network or hardware. Proves the gate wiring: finish is refused until configured, keys
persist, and the voice step skips cleanly with no ElevenLabs key.
"""
from __future__ import annotations

import threading
import time

import pytest

from covas import firstrun, setup_web


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {
        "ui": {"host": "127.0.0.1", "port": 8765},
        "anthropic": {"api_key_file": str(tmp_path / "anth.txt")},
        "elevenlabs": {"api_key_file": str(tmp_path / "el.txt")},
        "whisper": {"model": "small.en", "download_root": ""},
    }
    # Never touch the real overrides.json when the wizard writes choices.
    monkeypatch.setattr(firstrun, "load_overrides", lambda: {})
    saved = {}
    monkeypatch.setattr(firstrun, "save_overrides", lambda o: saved.update({"o": o}))
    done = threading.Event()
    app = setup_web.create_setup_app(cfg, done)
    app.config.update(TESTING=True)
    return app.test_client(), cfg, done, saved


def test_status_reports_unconfigured_fresh(client, monkeypatch):
    c, cfg, _, _ = client
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: False)
    st = c.get("/api/setup/status").get_json()
    assert st["anthropic"] is False and st["stt"] is False and st["configured"] is False
    assert st["download"]["state"] == "idle"


def test_save_keys_persists_and_ignores_blank_el(client):
    c, cfg, _, _ = client
    r = c.post("/api/setup/keys", json={"anthropic": "sk-ant-1", "elevenlabs": ""})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert firstrun.anthropic_key(cfg) == "sk-ant-1"
    assert firstrun.elevenlabs_key(cfg) is None      # blank EL not written


def test_finish_refused_until_configured(client, monkeypatch):
    c, cfg, done, _ = client
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: False)
    # No key, no model yet.
    r = c.post("/api/setup/finish")
    assert r.status_code == 400 and done.is_set() is False
    # Add key + model -> finish succeeds and sets the done event.
    c.post("/api/setup/keys", json={"anthropic": "k"})
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: True)
    r2 = c.post("/api/setup/finish")
    assert r2.status_code == 200 and done.is_set() is True


def test_voice_step_skips_without_el_key(client):
    c, cfg, _, _ = client
    data = c.post("/api/setup/voice").get_json()
    assert data["ok"] is True and data["skipped"] is True


def test_voice_step_resolves_and_saves(client, monkeypatch):
    c, cfg, _, saved = client
    firstrun.save_elevenlabs_key(cfg, "el-key")
    monkeypatch.setattr(setup_web.el, "list_voices", lambda cfg: [
        {"voice_id": "1", "name": "Sarah"}, {"voice_id": "2", "name": "George"}])
    data = c.post("/api/setup/voice").get_json()
    assert data["ok"] is True and data["voice"] == {"voice_id": "2", "name": "George"}
    # Persisted to overrides.
    assert saved["o"]["elevenlabs"]["voice_id"] == "2"
    assert cfg["elevenlabs"]["voice_id"] == "2"        # merged into live cfg too


def test_model_download_runs_and_flips_ready(client, monkeypatch):
    c, cfg, _, _ = client
    called = {}
    monkeypatch.setattr(firstrun, "download_stt_model",
                        lambda model, root: called.setdefault("model", model))
    monkeypatch.setattr(firstrun, "stt_download_root", lambda cfg: None)
    r = c.post("/api/setup/model")
    assert r.status_code == 200 and r.get_json()["state"] == "downloading"
    # The worker thread is a daemon; give it a moment to finish the (fake) download.
    for _ in range(50):
        st = c.get("/api/setup/status").get_json()["download"]
        if st["state"] != "downloading":
            break
        time.sleep(0.02)
    assert st["state"] == "ready" and called["model"] == firstrun.DEFAULT_STT_MODEL
    assert cfg["whisper"]["model"] == firstrun.DEFAULT_STT_MODEL   # override applied


def test_mic_saved_to_overrides(client, monkeypatch):
    c, cfg, _, saved = client
    r = c.post("/api/setup/mic", json={"device": "Blue Yeti"})
    assert r.status_code == 200
    assert cfg["audio"]["input_device"] == "Blue Yeti"
    assert saved["o"]["audio"]["input_device"] == "Blue Yeti"
