"""Unit tests for the web checklist editor's server side (N10; offline, DESIGN §9).

The editor itself is client-side (TOAST UI from CDN — a light template check is all that's
sensible offline); what these tests lock is every server guarantee the prompt names:
  * GET hands back the file plus a content-hash `version`;
  * a save round-trips LOSSLESSLY through `covas/checklist.py`'s parser — checkbox states,
    texts, and indentation/nesting survive, including the `* [ ]` bullets and 4-space nesting
    a WYSIWYG serializer emits (normalized to the file's canonical `- [ ]` form);
  * a save reloads the voice side (cursor clamped; sync event published);
  * the stale-write guard 409s when the file changed underneath the tab (a voice edit), the
    file is NOT clobbered, and `force` deliberately overrides.
"""
from __future__ import annotations

import queue

import pytest

from covas import config
from covas.app import App
from covas.web import _normalize_tasks, create_app
from tests.fakes import FakeLLM, FakeSTT, FakeTTS

_CHECKLIST = """# Ultimate checklist

## Engineering
- [x] Unlock Felicity Farseer
- [ ] Grade 5 FSD on the Anaconda
  - [ ] Farm Datamined Wake Exceptions
- [ ] Unlock Prof. Palin

Notes: keep some CMMs for the carrier.
"""


def _cfg(tmp_path) -> dict:
    """Minimal config for a real App with fakes: elite OFF (no watchers), audio inert,
    checklist on tmp (the file under test)."""
    checklist = tmp_path / "checklist.md"
    checklist.write_text(_CHECKLIST, encoding="utf-8")
    return {
        "keys": {"push_to_talk": "[", "tap_cancel_ms": 400, "cancel": ""},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "whisper": {"model": "small", "device": "cpu", "compute_type": "int8", "language": "en"},
        "anthropic": {
            "model": "claude-sonnet-5",
            "available_models": ["claude-opus-4-8", "claude-sonnet-5"],
            "max_tokens": 1024, "cache_ttl": "1h",
            "thinking": {"default": "Off"},
        },
        "router": {"enabled": True, "pin": "", "full_breakdown_max_tokens": 2048},
        "web_search": {"enabled": True, "max_uses": 3},
        "personality": {"enabled": False},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "v", "voice_name": "Sarah",
                       "output_format": "pcm_16000"},
        "conversation": {"max_turns": 20},
        "elite": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "logging": {"dir": str(tmp_path / "logs")},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    core = App(_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    flask_app = create_app(core)
    flask_app.config.update(TESTING=True)
    return flask_app.test_client(), core, tmp_path / "checklist.md"


def _save(c, markdown, version, force=False):
    return c.post("/api/checklist", json={"markdown": markdown, "base_version": version,
                                          "force": force})


# --- load -----------------------------------------------------------------------------------

def test_get_returns_markdown_and_a_stable_version(client):
    c, _core, path = client
    a = c.get("/api/checklist").get_json()
    b = c.get("/api/checklist").get_json()
    assert a["ok"] and a["markdown"] == _CHECKLIST and a["name"] == "checklist.md"
    assert a["version"] == b["version"]                 # content-hash: stable across reads

def test_bom_from_a_hand_edit_never_reaches_the_editor(client):
    # Notepad saves UTF-8 with a BOM; leaked into the editor it turns the first heading
    # into literal text that gets `\#`-escaped on the next save. Stripped at the boundary.
    c, _core, path = client
    path.write_bytes(b"\xef\xbb\xbf" + _CHECKLIST.encode("utf-8"))
    data = c.get("/api/checklist").get_json()
    assert data["markdown"] == _CHECKLIST                # no BOM, heading intact

def test_missing_file_loads_as_an_empty_saveable_checklist(client):
    c, _core, path = client
    path.unlink()
    data = c.get("/api/checklist").get_json()
    assert data["ok"] and data["markdown"] == ""
    r = _save(c, "- [ ] first item", data["version"])   # first save CREATES the file
    assert r.status_code == 200
    assert path.read_text(encoding="utf-8") == "- [ ] first item\n"


# --- round-trip losslessness ------------------------------------------------------------------

def test_unchanged_save_round_trips_byte_for_byte(client):
    c, core, path = client
    before = core.checklist.items()
    data = c.get("/api/checklist").get_json()
    r = _save(c, data["markdown"], data["version"])
    assert r.status_code == 200
    assert path.read_text(encoding="utf-8") == _CHECKLIST      # nothing normalized away
    assert core.checklist.items() == before

def test_wysiwyg_serialization_normalizes_bullets_but_preserves_structure(client):
    """What TOAST UI's serializer emits — `* [ ]` bullets, 4-space nesting — must land on
    disk in the file's canonical `- [ ]` form with states, texts, and nesting intact."""
    c, core, path = client
    version = c.get("/api/checklist").get_json()["version"]
    editor_md = ("# Ultimate checklist\n\n"
                 "* [x] Unlock Felicity Farseer\n"
                 "* [ ] Grade 5 FSD on the Anaconda\n"
                 "    * [ ] Farm Datamined Wake Exceptions\n")
    assert _save(c, editor_md, version).status_code == 200
    text = path.read_text(encoding="utf-8")
    assert "- [x] Unlock Felicity Farseer" in text
    assert "    - [ ] Farm Datamined Wake Exceptions" in text  # nesting indent kept
    assert "* [" not in text                                    # every task line normalized
    assert "# Ultimate checklist" in text                       # non-task lines untouched
    assert core.checklist.items() == [(1, True, "Unlock Felicity Farseer"),
                                      (2, False, "Grade 5 FSD on the Anaconda"),
                                      (3, False, "Farm Datamined Wake Exceptions")]

def test_normalize_tasks_is_a_noop_on_canonical_input():
    assert _normalize_tasks(_CHECKLIST) == _CHECKLIST
    assert _normalize_tasks("") == ""
    # A `*` in the TEXT of a task (or in prose) is not a bullet and must survive.
    kept = _normalize_tasks("- [ ] buy 5 * 100 CMMs\nplain *emphasis* line\n")
    assert kept == "- [ ] buy 5 * 100 CMMs\nplain *emphasis* line\n"


# --- voice-side reload on save ----------------------------------------------------------------

def test_save_clamps_the_cursor_and_publishes_a_sync_event(client):
    c, core, _path = client
    core.checklist.current = 4                          # cursor on the last of 4 items
    q = core.bus.subscribe(replay=False)
    version = c.get("/api/checklist").get_json()["version"]
    r = _save(c, "- [ ] the only item left", version)
    assert r.status_code == 200
    assert r.get_json()["items"] == 1 and r.get_json()["done"] == 0
    assert core.checklist.current == 1                  # clamped to the shorter list
    events = []
    try:
        while True:
            events.append(q.get_nowait())
    except queue.Empty:
        pass
    assert any("Checklist updated from the web editor" in e.get("text", "") for e in events)


# --- the stale-write guard ---------------------------------------------------------------------

def test_stale_save_is_refused_and_the_voice_edit_survives(client):
    c, core, path = client
    stale_version = c.get("/api/checklist").get_json()["version"]
    core.checklist.set_number(2, True)                  # a voice edit lands meanwhile
    voice_text = path.read_text(encoding="utf-8")
    r = _save(c, "- [ ] web edit that would clobber", stale_version)
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "stale"
    assert body["markdown"] == voice_text               # theirs, offered back to the client
    assert path.read_text(encoding="utf-8") == voice_text   # file NOT overwritten

def test_force_save_deliberately_overwrites(client):
    c, core, path = client
    stale_version = c.get("/api/checklist").get_json()["version"]
    core.checklist.set_number(2, True)
    r = _save(c, "- [ ] web wins this time", stale_version, force=True)
    assert r.status_code == 200
    assert path.read_text(encoding="utf-8") == "- [ ] web wins this time\n"


# --- template + config guard -------------------------------------------------------------------

def test_checklist_page_renders_the_editor(client):
    c, _core, _path = client
    html = c.get("/checklist").get_data(as_text=True)
    assert "toastui" in html and 'id="editor"' in html
    assert 'id="fallback"' in html                      # CDN-down textarea fallback exists

def test_no_checklist_configured_is_a_clean_400(client, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg["checklist"]["file"] = ""
    core = App(cfg, llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    app = create_app(core)
    app.config.update(TESTING=True)
    c = app.test_client()
    assert c.get("/api/checklist").status_code == 400
    assert c.post("/api/checklist", json={"markdown": "x"}).status_code == 400
