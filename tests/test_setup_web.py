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
        # A key file per managed section + provider selections (default anthropic + edge, matching
        # config.toml). The wizard may switch these to any supported combo (issue #87).
        "llm": {"provider": "anthropic"},
        "tts": {"provider": "elevenlabs"},
        "anthropic": {"api_key_file": str(tmp_path / "anth.txt")},
        "openai": {"api_key_file": str(tmp_path / "openai.txt"),
                   "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
        "gemini": {"api_key_file": str(tmp_path / "gemini.txt"), "model": "gemini-flash-lite-latest"},
        "elevenlabs": {"api_key_file": str(tmp_path / "el.txt")},
        "azure": {"api_key_file": str(tmp_path / "azure.txt")},
        "cartesia": {"api_key_file": str(tmp_path / "cartesia.txt")},
        "edge": {"voice": "en-US-AriaNeural"},
        "piper": {"model": ""},
        "openai_tts": {"voice": "alloy"},
        "whisper": {"model": "small.en", "download_root": ""},
    }
    # Never touch the real overrides.json when the wizard writes choices; merge overrides into the
    # live cfg (as firstrun.apply_override does) so status reads reflect provider switches.
    saved = {}
    monkeypatch.setattr(firstrun, "load_overrides", lambda: {})
    monkeypatch.setattr(firstrun, "save_overrides", lambda o: saved.update({"o": o}))
    done = threading.Event()
    app = setup_web.create_setup_app(cfg, done)
    app.config.update(TESTING=True)
    return app.test_client(), cfg, done, saved


def test_status_reports_unconfigured_fresh(client, monkeypatch):
    c, cfg, _, _ = client
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: False)
    st = c.get("/api/setup/status").get_json()
    # Provider-aware shape (issue #87): active provider + readiness flags, no bare "anthropic" key.
    assert st["llm_provider"] == "anthropic" and st["tts_provider"] == "elevenlabs"
    assert st["llm"] is False and st["stt"] is False and st["configured"] is False
    assert st["download"]["state"] == "idle"


def test_save_keys_persists_and_ignores_blank_el(client):
    c, cfg, _, _ = client
    r = c.post("/api/setup/keys", json={"llm_provider": "anthropic",
                                        "keys": {"anthropic": "sk-ant-1", "elevenlabs": ""}})
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
    c.post("/api/setup/keys", json={"keys": {"anthropic": "k"}})
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: True)
    r2 = c.post("/api/setup/finish")
    assert r2.status_code == 200 and done.is_set() is True


def test_voice_step_skips_without_el_key(client):
    c, cfg, _, _ = client   # fixture sets tts.provider = elevenlabs
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


# --- provider-aware onboarding (issue #87) -----------------------------------------------

def test_finish_succeeds_with_gemini_edge_no_anthropic(client, monkeypatch):
    """A non-Anthropic LLM + a free non-ElevenLabs voice (Gemini + Edge) can finish onboarding —
    NO Anthropic key, NO ElevenLabs key, ending at a launchable state."""
    c, cfg, done, _ = client
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: True)
    # Pick Gemini + Edge and paste only the Gemini key.
    r = c.post("/api/setup/keys", json={"llm_provider": "gemini", "tts_provider": "edge",
                                        "keys": {"gemini": "AIza-key"}})
    assert r.status_code == 200
    st = r.get_json()["status"]
    assert st["llm_provider"] == "gemini" and st["tts_provider"] == "edge"
    assert st["llm"] is True and st["voice"] is True and st["configured"] is True
    assert firstrun.anthropic_key(cfg) is None            # finished with no Anthropic key
    r2 = c.post("/api/setup/finish")
    assert r2.status_code == 200 and done.is_set() is True


def test_save_keys_switches_provider_and_persists_fields(client, monkeypatch):
    c, cfg, _, _ = client
    monkeypatch.setattr(firstrun, "stt_model_available", lambda *a, **k: True)
    c.post("/api/setup/keys", json={
        "llm_provider": "openai", "keys": {"openai": "sk-groq"},
        "openai_base_url": "https://api.groq.com/openai/v1", "openai_model": "llama-3.3-70b"})
    assert cfg["llm"]["provider"] == "openai"
    assert cfg["openai"]["base_url"] == "https://api.groq.com/openai/v1"
    assert cfg["openai"]["model"] == "llama-3.3-70b"
    assert firstrun.openai_key(cfg) == "sk-groq"


def test_voice_edge_needs_no_key_and_persists_voice(client):
    c, cfg, _, _ = client
    c.post("/api/setup/keys", json={"tts_provider": "edge"})
    data = c.post("/api/setup/voice", json={"voice": "en-GB-RyanNeural"}).get_json()
    assert data["ok"] is True and not data.get("skipped")     # free voice, never text-only
    assert cfg["edge"]["voice"] == "en-GB-RyanNeural"


def test_voice_piper_persists_model_no_fetch(client):
    c, cfg, _, _ = client
    c.post("/api/setup/keys", json={"tts_provider": "piper"})
    data = c.post("/api/setup/voice", json={"model": "voices/en_US-lessac-medium.onnx"}).get_json()
    assert data["ok"] is True and data["provider"] == "piper"
    assert cfg["piper"]["model"] == "voices/en_US-lessac-medium.onnx"


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


# --- finish copy is native-aware (the "close this tab" quit-the-app bug, I9/I7) ------------
# In the NATIVE single window, closing it QUITS the app, so the finish page must NOT tell the
# user to close a tab — it swaps itself to the panel. A browser tab is safe to close. The copy
# is injected server-side per `native`, so the served page carries exactly one of the two.

def _setup_html(native: bool) -> str:
    cfg = {"ui": {"host": "127.0.0.1", "port": 8765},
           "anthropic": {"api_key_file": "x"}, "elevenlabs": {"api_key_file": "y"},
           "whisper": {"model": "small.en"}}
    app = setup_web.create_setup_app(cfg, threading.Event(), native=native)
    app.config.update(TESTING=True)
    return app.test_client().get("/").get_data(as_text=True)


def test_native_finish_copy_never_says_close_the_tab():
    html = _setup_html(native=True)
    assert "close this tab" not in html.lower()          # closing the native window would quit
    assert "control panel" in html.lower()               # it swaps itself to the panel instead


def test_browser_finish_copy_still_says_close_the_tab():
    html = _setup_html(native=False)                     # the run_covas_ui.py path is unchanged
    assert "close this tab" in html.lower()


def test_finish_copy_defaults_to_browser():
    # Default (no native kwarg) keeps the browser wording — start_setup_server opts in explicitly.
    assert setup_web._FINISH_MSG_BROWSER != setup_web._FINISH_MSG_NATIVE
    assert "close this tab" in setup_web._FINISH_MSG_BROWSER.lower()
    assert "close this tab" not in setup_web._FINISH_MSG_NATIVE.lower()
