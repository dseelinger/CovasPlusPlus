"""Offline regression tests for the four web-robustness fixes in covas/web.py (issue #163).

Each test pins ONE defect the audit flagged, driven through Flask's test client with no network:
  1. `checklist_save` closes the check-then-act window — a voice edit that lands DURING the save is
     detected and 409'd instead of being silently clobbered (the version is re-read under the save
     lock immediately before the write).
  2. a non-dict `updates` payload is the clean 400 every other bad input gets, not an AttributeError
     -> HTTP 500.
  3. `memory_edit` that OMITS `tags` preserves the record's existing tags (symmetric with `type`);
     an explicit empty list still clears them.
  4. `_catalog_cached` resolves at most ONCE for concurrent cold opens of the same key — the TTL
     throttle is honoured under a per-key lock, not defeated by a check-then-act race.
"""
from __future__ import annotations

import threading
import time

import pytest

import covas.web as web
from covas import config
from covas.app import App
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


# --- shared config for a real App with fakes (elite OFF, audio inert) ------------------------
def _cfg(tmp_path, *, memory: bool = False) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    cfg = {
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
    if memory:
        cfg["memory"] = {"enabled": True, "dir": str(tmp_path / "memory"), "cap": 500}
    return cfg


def _app(tmp_path, monkeypatch, *, memory: bool = False):
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    core = App(_cfg(tmp_path, memory=memory), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    flask_app = web.create_app(core)
    flask_app.config.update(TESTING=True)
    return flask_app, core


# --- defect 1: checklist_save atomicity (no silent clobber under a mid-save voice edit) --------
def test_checklist_save_409s_a_voice_edit_that_lands_during_the_save(tmp_path, monkeypatch):
    """A voice edit landing AFTER the client captured its base_version but BEFORE the write must be
    caught. We inject that edit by hooking `_normalize_tasks` (called on the save path) to write to
    the file mid-save; the fix re-reads the version under the lock right before writing, so the now-
    stale base_version 409s and the voice edit survives — the old code read the version too early
    and clobbered it with a 200."""
    flask_app, _core = _app(tmp_path, monkeypatch)
    path = tmp_path / "checklist.md"
    c = flask_app.test_client()
    base = c.get("/api/checklist").get_json()["version"]

    orig = web._normalize_tasks
    voice_text = "- [x] Scoop fuel — voice edit landed\n"

    def racing_normalize(markdown):
        # Simulate a concurrent voice write completing during this save (once, before the guard).
        path.write_text(voice_text, encoding="utf-8")
        return orig(markdown)

    monkeypatch.setattr(web, "_normalize_tasks", racing_normalize)
    r = c.post("/api/checklist",
               json={"markdown": "- [ ] web edit that must not clobber", "base_version": base})
    assert r.status_code == 409
    assert r.get_json()["error"] == "stale"
    # The voice edit is intact on disk — the web save did NOT overwrite it.
    assert path.read_text(encoding="utf-8") == voice_text


# --- defect 2: non-dict `updates` is a clean 400, not a 500 ------------------------------------
@pytest.mark.parametrize("bad", [[1, 2, 3], "oops", 5, True])
def test_non_dict_updates_is_a_clean_400(tmp_path, monkeypatch, bad):
    flask_app, _core = _app(tmp_path, monkeypatch)
    c = flask_app.test_client()
    r = c.post("/api/settings/update", json={"updates": bad})
    assert r.status_code == 400                       # not a 500 from `.keys()` on a non-dict
    body = r.get_json()
    assert body["ok"] is False and "updates" in body["errors"]


# --- defect 3: memory_edit preserves existing tags when `tags` is omitted ----------------------
def test_memory_edit_omitting_tags_preserves_them(tmp_path, monkeypatch):
    flask_app, core = _app(tmp_path, monkeypatch, memory=True)
    assert core.memory is not None
    from covas.memory.store import MemoryRecord
    seeded = core.memory.store.add(MemoryRecord(text="prefers the Krait", type="note",
                                                tags=["ship", "combat"]))
    c = flask_app.test_client()
    version = c.get("/api/memory").get_json()["version"]

    # Edit only text + type; `tags` is absent from the payload -> existing tags must survive.
    r = c.post("/api/memory/edit", json={"id": seeded.id, "text": "prefers the Krait Mk II",
                                         "type": "preference", "base_version": version})
    assert r.status_code == 200
    after = core.memory.store.load()[0]
    assert after.text == "prefers the Krait Mk II" and after.type == "preference"
    assert after.tags == ("ship", "combat")           # NOT wiped to ()

    # An EXPLICIT empty list is still a deliberate clear (unchanged behaviour).
    version = c.get("/api/memory").get_json()["version"]
    r = c.post("/api/memory/edit", json={"id": seeded.id, "text": "prefers the Krait Mk II",
                                         "tags": [], "base_version": version})
    assert r.status_code == 200
    assert core.memory.store.load()[0].tags == ()


# --- defect 4: catalog TTL guard resolves at most once for concurrent same-key cold opens ------
def test_catalog_cold_opens_resolve_at_most_once(tmp_path, monkeypatch):
    """Two cold dropdown opens of the SAME source must trigger only ONE `catalog.resolve` — the
    per-key lock + double-check honours the TTL throttle instead of both racing the network."""
    flask_app, _core = _app(tmp_path, monkeypatch)
    calls = {"n": 0}
    lock = threading.Lock()

    def counting_resolve(source, cfg, *, base_url=None):
        with lock:
            calls["n"] += 1
        time.sleep(0.05)                              # widen the race window
        return [{"value": "a"}, {"value": "b"}], None

    monkeypatch.setattr(web.catalog, "resolve", counting_resolve)
    source = web.schema.OPT_EDGE_VOICES               # a keyless local source in _CATALOG_SOURCES

    n = 6
    barrier = threading.Barrier(n)
    results: list[int] = []
    res_lock = threading.Lock()

    def hit():
        client = flask_app.test_client()
        barrier.wait()                                # all threads fire simultaneously
        r = client.get("/api/catalog", query_string={"source": source})
        with res_lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=hit) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200] * n                       # every request served
    assert calls["n"] == 1                            # ...but the network was hit exactly once
