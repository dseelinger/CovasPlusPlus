"""Unit tests for the web memory browser's server side (issue #62; offline, DESIGN §9).

The browser UI is client-side vanilla JS (a light template check is all that's sensible here);
what these tests lock is every server guarantee the prompt names:
  * GET lists memories plus a content-hash `version`, and honours a `?q=` search filter;
  * add / edit / delete round-trip LOSSLESSLY through `covas/memory/store.py` (edit preserves a
    record's `id` and original `when`, mutating only the edited fields);
  * every mutation shares the SAME physical JSONL file the voice path uses — the store instance
    is `core.memory.store`, so a voice write and a web write can't diverge;
  * the stale-write guard 409s when the file changed underneath the tab (a voice edit), the file
    is NOT clobbered, and `force` deliberately overrides.
"""
from __future__ import annotations

import queue

import pytest

from covas import config
from covas.app import App
from covas.web import create_app
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path) -> dict:
    """Minimal config for a real App with fakes: elite OFF (no watchers), audio inert, memory ON
    with its dir on tmp (the store under test lives at <dir>/memory.jsonl)."""
    checklist = tmp_path / "checklist.md"
    checklist.write_text("# Ultimate checklist\n", encoding="utf-8")
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
        "memory": {"enabled": True, "dir": str(tmp_path / "memory"), "cap": 500},
        "logging": {"dir": str(tmp_path / "logs")},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    core = App(_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    assert core.memory is not None, "memory must be wired for the browser tests"
    flask_app = create_app(core)
    flask_app.config.update(TESTING=True)
    # The store the voice path writes to == the store the web browser edits (issue #62's hard
    # constraint). Every test drives the web routes and cross-checks core.memory.store.
    return flask_app.test_client(), core


def _state(c, q=None):
    return c.get("/api/memory", query_string={"q": q} if q else None).get_json()


def _seed(core, text, **kw):
    """Add a fact straight through the shared store, standing in for the voice path."""
    return core.memory.store.add(_record(text, **kw))


def _record(text, **kw):
    from covas.memory.store import MemoryRecord
    return MemoryRecord(text=text, **kw)


# --- list -------------------------------------------------------------------------------------

def test_get_lists_memories_with_a_stable_version(client):
    c, core = client
    _seed(core, "prefers the Krait Mk II", type="preference", tags=["ship"])
    a = _state(c)
    b = _state(c)
    assert a["ok"] and a["total"] == 1 and len(a["memories"]) == 1
    assert a["memories"][0]["text"] == "prefers the Krait Mk II"
    assert a["memories"][0]["type"] == "preference" and a["memories"][0]["tags"] == ["ship"]
    assert a["version"] == b["version"]                 # content-hash: stable across reads
    assert a["name"] == "memory.jsonl"

def test_empty_store_lists_nothing_cleanly(client):
    c, _core = client
    data = _state(c)
    assert data["ok"] and data["memories"] == [] and data["total"] == 0


# --- search -----------------------------------------------------------------------------------

def test_search_filters_by_text_type_and_tags(client):
    c, core = client
    _seed(core, "prefers the Krait Mk II", type="preference", tags=["ship"])
    _seed(core, "Commander's name is Jameson", type="fact", tags=["name"])
    _seed(core, "likes metric units", type="note", tags=["units"])
    by_text = _state(c, "krait")
    assert [m["text"] for m in by_text["memories"]] == ["prefers the Krait Mk II"]
    assert by_text["total"] == 3                        # total is the whole file, not the filter
    assert [m["text"] for m in _state(c, "name")["memories"]] == ["Commander's name is Jameson"]
    assert [m["text"] for m in _state(c, "preference")["memories"]] == ["prefers the Krait Mk II"]
    assert _state(c, "nothingmatches")["memories"] == []


# --- add --------------------------------------------------------------------------------------

def test_add_appends_to_the_shared_file(client):
    c, core = client
    version = _state(c)["version"]
    r = c.post("/api/memory/add", json={"text": "likes metric units", "type": "preference",
                                        "tags": ["units", "UI"], "base_version": version})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body["total"] == 1
    # Landed in the SAME store the voice path holds, tags normalized (lower-cased).
    recs = core.memory.store.load()
    assert len(recs) == 1 and recs[0].text == "likes metric units"
    assert recs[0].type == "preference" and recs[0].tags == ("units", "ui")

def test_add_rejects_empty_text(client):
    c, _core = client
    version = _state(c)["version"]
    r = c.post("/api/memory/add", json={"text": "   ", "base_version": version})
    assert r.status_code == 400


# --- edit -------------------------------------------------------------------------------------

def test_edit_changes_fields_but_preserves_id_and_when(client):
    c, core = client
    _seed(core, "prefers the Krait", type="note", tags=["ship"])
    before = core.memory.store.load()[0]
    version = _state(c)["version"]
    r = c.post("/api/memory/edit", json={
        "id": before.id, "text": "prefers the Krait Mk II for combat",
        "type": "preference", "tags": ["ship", "combat"], "base_version": version})
    assert r.status_code == 200
    after = core.memory.store.load()[0]
    assert after.id == before.id and after.when == before.when      # identity + timestamp kept
    assert after.text == "prefers the Krait Mk II for combat"
    assert after.type == "preference" and after.tags == ("ship", "combat")

def test_edit_unknown_id_is_a_404(client):
    c, core = client
    _seed(core, "a fact")
    version = _state(c)["version"]
    r = c.post("/api/memory/edit", json={"id": "deadbeef", "text": "x", "base_version": version})
    assert r.status_code == 404


# --- delete -----------------------------------------------------------------------------------

def test_delete_removes_one_record_by_id(client):
    c, core = client
    _seed(core, "keep me", tags=["keep"])
    doomed = _seed(core, "delete me", tags=["drop"])
    version = _state(c)["version"]
    r = c.post("/api/memory/delete", json={"id": doomed.id, "base_version": version})
    assert r.status_code == 200
    texts = [m.text for m in core.memory.store.load()]
    assert texts == ["keep me"]

def test_delete_unknown_id_is_a_404(client):
    c, core = client
    _seed(core, "a fact")
    version = _state(c)["version"]
    r = c.post("/api/memory/delete", json={"id": "nope", "base_version": version})
    assert r.status_code == 404


# --- the stale-write guard ---------------------------------------------------------------------

def test_stale_add_is_refused_and_the_voice_edit_survives(client):
    c, core = client
    stale = _state(c)["version"]
    _seed(core, "a voice memory landed meanwhile", tags=["voice"])   # file changes underneath
    on_disk = core.memory.store.load()
    r = c.post("/api/memory/add", json={"text": "web add that must not clobber",
                                        "base_version": stale})
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "stale"
    assert body["version"] != stale                     # the client is handed the current token
    assert {m["text"] for m in body["memories"]} == {"a voice memory landed meanwhile"}
    # File untouched by the refused add: only the voice memory is present.
    assert [r.text for r in core.memory.store.load()] == [r.text for r in on_disk]

def test_stale_edit_and_delete_are_also_refused(client):
    c, core = client
    target = _seed(core, "original")
    stale = _state(c)["version"]
    _seed(core, "voice memory")                         # bump the version
    edit = c.post("/api/memory/edit", json={"id": target.id, "text": "hijacked",
                                            "base_version": stale})
    assert edit.status_code == 409
    delete = c.post("/api/memory/delete", json={"id": target.id, "base_version": stale})
    assert delete.status_code == 409
    assert {r.text for r in core.memory.store.load()} == {"original", "voice memory"}

def test_force_write_deliberately_overrides_the_guard(client):
    c, core = client
    stale = _state(c)["version"]
    _seed(core, "voice memory")
    r = c.post("/api/memory/add", json={"text": "web wins this time", "force": True,
                                        "base_version": stale})
    assert r.status_code == 200
    assert "web wins this time" in {rec.text for rec in core.memory.store.load()}

def test_a_successful_web_write_publishes_a_sync_event(client):
    c, core = client
    q = core.bus.subscribe(replay=False)
    version = _state(c)["version"]
    assert c.post("/api/memory/add", json={"text": "note this", "base_version": version}
                  ).status_code == 200
    events = []
    try:
        while True:
            events.append(q.get_nowait())
    except queue.Empty:
        pass
    assert any("Memory updated from the web browser" in e.get("text", "") for e in events)


# --- template + config guard -------------------------------------------------------------------

def test_memory_page_renders_the_browser(client):
    c, _core = client
    html = c.get("/memory").get_data(as_text=True)
    assert 'id="list"' in html and 'id="add"' in html and 'id="search"' in html

def test_memory_browser_falls_back_to_config_store_when_capture_is_off(tmp_path, monkeypatch):
    """With memory disabled, `core.memory` is None; the browser still opens the SAME file via
    store_from_config so a user can curate memory even before turning capture on."""
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    cfg = _cfg(tmp_path)
    cfg["memory"]["enabled"] = False
    core = App(cfg, llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    assert core.memory is None
    app = create_app(core)
    app.config.update(TESTING=True)
    c = app.test_client()
    version = c.get("/api/memory").get_json()["version"]
    r = c.post("/api/memory/add", json={"text": "curated before capture", "base_version": version})
    assert r.status_code == 200
    # Written to <memory.dir>/memory.jsonl, the same path store_from_config resolves.
    assert (tmp_path / "memory" / "memory.jsonl").exists()
