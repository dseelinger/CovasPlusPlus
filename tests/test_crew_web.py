"""Unit tests for the web crew editor's server side (issue #70; offline, DESIGN §9).

The editor UI is client-side vanilla JS (a light template check is all that's sensible here);
what these tests lock is every server guarantee:
  * GET lists the roster + the available voice options + a content-hash `version`, and reports
    whether crew is enabled;
  * a whole-roster save round-trips LOSSLESSLY through `covas/crew.py` (name / persona / voice_ref),
    dropping nameless rows, and the saved file is the SAME one the voice + prompt paths read;
  * the stale-write guard 409s when the file changed underneath the tab, leaving it unclobbered,
    and `force` deliberately overrides;
  * (issue #124) the snapshot surfaces a resolved best-fit voice name for Auto members only, and a
    save kicks the background crew pairing recompute (offline-tested in test_crew_voice_pairing.py).
"""
from __future__ import annotations


import pytest

from covas import bootstrap
from covas import config
from covas.app import App
from covas.web import create_app
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path) -> dict:
    """Minimal config for a real App with fakes: elite OFF, audio inert, crew ON with its roster
    file on tmp. A cast pool is configured so the voice-options endpoint has something to list."""
    checklist = tmp_path / "checklist.md"
    checklist.write_text("# Ultimate checklist\n", encoding="utf-8")
    return {
        "keys": {"push_to_talk": "[", "tap_cancel_ms": 400, "cancel": ""},
        "audio": {"sample_rate": 16000, "input_device": "",
                  "voices": {"cast_provider": "elevenlabs", "random_el": False,
                             "pool": [{"provider": "elevenlabs", "ref": r, "gender": "neutral"}
                                      for r in ("VA", "VB", "VC")]}},
        "sound_cues": {},
        "whisper": {"model": "small", "n_threads": 4, "language": "en"},
        "anthropic": {
            "model": "claude-sonnet-5",
            "available_models": ["claude-opus-4-8", "claude-sonnet-5"],
            "max_tokens": 1024, "cache_ttl": "1h",
            "thinking": {"default": "Off"},
        },
        "router": {"enabled": True, "pin": "", "full_breakdown_max_tokens": 2048},
        "web_search": {"enabled": True, "max_uses": 3},
        "personality": {"enabled": False},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "PERSONA", "voice_name": "Sarah",
                       "output_format": "pcm_16000"},
        "conversation": {"max_turns": 20},
        "elite": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "crew": {"enabled": True, "file": str(tmp_path / "crew.json"), "roster": []},
        "experimental": {"crew": {"enabled": True}},   # crew is gated behind this too (#123)
        "memory": {"enabled": False, "dir": str(tmp_path / "memory"), "cap": 500},
        "logging": {"dir": str(tmp_path / "logs")},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    core = App(_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    flask_app = create_app(core)
    flask_app.config.update(TESTING=True)
    return flask_app.test_client(), core


def _state(c):
    return c.get("/api/crew").get_json()


def _save(c, members, **kw):
    return c.post("/api/crew", json={"members": members, **kw})


# --- list --------------------------------------------------------------------------------------

def test_get_lists_empty_roster_with_voices_and_version(client):
    c, _core = client
    data = _state(c)
    assert data["ok"] and data["members"] == [] and data["enabled"] is True
    assert data["name"] == "crew.json"
    refs = {v["ref"] for v in data["voices"]}
    assert {"VA", "VB", "VC", "PERSONA"} <= refs        # cast pool + persona offered
    assert _state(c)["version"] == data["version"]      # content-hash: stable across reads


# --- save round-trip ---------------------------------------------------------------------------

def test_save_persists_the_roster_to_the_shared_file(client, tmp_path):
    c, core = client
    version = _state(c)["version"]
    r = _save(c, [{"name": "Nyx", "persona": "Terse.", "voice_ref": "VB"},
                  {"name": "Vela", "persona": "", "voice_ref": ""}], base_version=version)
    assert r.status_code == 200
    body = r.get_json()
    assert [m["name"] for m in body["members"]] == ["Nyx", "Vela"]
    # Landed in the SAME file the voice + prompt paths read live (issue #70's hard constraint).
    from covas import crew as crew_mod
    members = crew_mod.load_members(core.cfg)
    assert members[0] == crew_mod.CrewMember("Nyx", "Terse.", "VB")
    # And it's woven into the (now cache-busted-once) static system instruction.
    assert "Terse." in crew_mod.system_instruction(core.cfg)


def test_save_drops_nameless_rows(client):
    c, _core = client
    version = _state(c)["version"]
    r = _save(c, [{"name": "Nyx"}, {"name": "  ", "persona": "orphan"}], base_version=version)
    assert [m["name"] for m in r.get_json()["members"]] == ["Nyx"]


def test_save_rejects_a_non_list_members(client):
    c, _core = client
    version = _state(c)["version"]
    r = c.post("/api/crew", json={"members": "nope", "base_version": version})
    assert r.status_code == 400


# --- the stale-write guard ---------------------------------------------------------------------

def test_stale_save_is_refused_and_the_on_disk_roster_survives(client, tmp_path):
    c, core = client
    stale = _state(c)["version"]
    # A hand-edit lands on disk underneath the tab.
    from covas import crew as crew_mod
    crew_mod.save_members(tmp_path / "crew.json", [crew_mod.CrewMember("HandEdit", "", "")])
    r = _save(c, [{"name": "WebWins"}], base_version=stale)
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "stale" and body["version"] != stale
    assert [m["name"] for m in body["members"]] == ["HandEdit"]     # the on-disk version is handed back
    assert [m.name for m in crew_mod.load_members(core.cfg)] == ["HandEdit"]  # file untouched


def test_force_save_overrides_the_guard(client, tmp_path):
    c, core = client
    stale = _state(c)["version"]
    from covas import crew as crew_mod
    crew_mod.save_members(tmp_path / "crew.json", [crew_mod.CrewMember("HandEdit", "", "")])
    r = _save(c, [{"name": "WebWins"}], base_version=stale, force=True)
    assert r.status_code == 200
    assert [m.name for m in crew_mod.load_members(core.cfg)] == ["WebWins"]


def test_a_successful_save_publishes_a_sync_event(client):
    c, core = client
    import queue
    q = core.bus.subscribe(replay=False)
    version = _state(c)["version"]
    assert _save(c, [{"name": "Nyx"}], base_version=version).status_code == 200
    events = []
    try:
        while True:
            events.append(q.get_nowait())
    except queue.Empty:
        pass
    assert any("Crew roster updated from the web editor" in e.get("text", "") for e in events)


# --- hired NPC pilots + adopt suggestion (issue #125) ------------------------------------------

def test_get_includes_hired_empty_when_elite_off(client):
    c, _core = client
    # With elite monitoring off there's no registry, so the datalist source is simply empty.
    assert _state(c)["hired"] == []


def test_suggest_persona_returns_editable_text(client):
    c, _core = client
    r = c.post("/api/crew/suggest_persona", json={"name": "Zeta", "combat_rank": 5})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and isinstance(body["persona"], str) and body["persona"].strip()


def test_suggest_persona_requires_a_name(client):
    c, _core = client
    assert c.post("/api/crew/suggest_persona", json={"name": "  "}).status_code == 400


# --- template + config guard -------------------------------------------------------------------

def test_crew_page_renders_the_editor(client):
    c, _core = client
    html = c.get("/crew").get_data(as_text=True)
    assert 'id="list"' in html and 'id="save"' in html and 'id="addRow"' in html


# --- best-fit crew voice pairing (issue #124) ---------------------------------------------------

def test_crew_pairings_surface_only_for_auto_members_with_a_resolved_pairing(client):
    """`_crew_snapshot`'s `crew_pairings` map (issue #124) resolves a member's paired voice_id to
    its display name via the app's `_voice_names` catalog, but ONLY for members left on Auto
    (blank voice_ref) — a pinned member is never listed even if a stale pairing exists for them."""
    c, core = client
    version = _state(c)["version"]
    _save(c, [{"name": "Nyx", "persona": "Terse.", "voice_ref": ""},
              {"name": "Kael", "persona": "Warm.", "voice_ref": "VPINNED"}],
         base_version=version)
    core._crew_voice_pairings = {"nyx": "v_gruff", "kael": "v_warm"}
    core._voice_names = {"v_gruff": "Bruno", "v_warm": "Sarah"}
    data = _state(c)
    assert data["crew_pairings"] == {"Nyx": "Bruno"}       # Kael is pinned -> excluded


def test_crew_pairings_falls_back_to_the_raw_id_when_the_name_is_unknown(client):
    c, core = client
    version = _state(c)["version"]
    _save(c, [{"name": "Nyx", "persona": "Terse.", "voice_ref": ""}], base_version=version)
    core._crew_voice_pairings = {"nyx": "v_unknown"}
    core._voice_names = {}
    assert _state(c)["crew_pairings"] == {"Nyx": "v_unknown"}


def test_crew_pairings_empty_when_nothing_paired_yet(client):
    c, _core = client
    version = _state(c)["version"]
    _save(c, [{"name": "Nyx", "persona": "Terse.", "voice_ref": ""}], base_version=version)
    assert _state(c)["crew_pairings"] == {}


def test_save_kicks_the_crew_pairing_recompute(client, monkeypatch):
    """A roster save re-runs the crew pairing worker in the background (issue #124) — mirroring how
    a settings change kicks the persona pairing. We don't exercise the real (network) worker here;
    that's covered offline in tests/test_crew_voice_pairing.py."""
    c, core = client
    kicked = []
    monkeypatch.setattr(bootstrap, "kick_crew_voice_pairing", lambda app: kicked.append(app))
    version = _state(c)["version"]
    assert _save(c, [{"name": "Nyx"}], base_version=version).status_code == 200
    assert kicked == [core]


def test_crew_editor_unavailable_without_a_configured_file(tmp_path, monkeypatch):
    """With no [crew].file the editor cleanly reports unavailable rather than crashing."""
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    cfg = _cfg(tmp_path)
    cfg["crew"].pop("file")
    core = App(cfg, llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    app = create_app(core)
    app.config.update(TESTING=True)
    c = app.test_client()
    assert c.get("/api/crew").status_code == 400
    assert c.post("/api/crew", json={"members": []}).status_code == 400
