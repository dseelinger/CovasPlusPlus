"""Web-editor tests for PER-SHIP crew rosters (issue #127) — offline, DESIGN §9.

Locks the server guarantees the per-ship editor relies on: the `/api/crew` fleet dimension (union
of the current Loadout ship + StoredShips + file-known ship ids, so a file-known ship survives a
stale/absent snapshot), a per-ship save that PRESERVES the other rosters + the whole-file 409 guard,
and the seat cap (§5) truncating a per-ship save. The client JS is exercised by hand (MANUAL_TESTS).
"""
from __future__ import annotations



from covas import config, crew
from covas.app import App
from covas.crew import CrewMember, RosterFile, ShipRoster
from covas.ed.loadout import LoadoutSnapshot
from covas.ed.stored import StoredShip, StoredShipsSnapshot
from covas.web import create_app
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path, **crew_extra) -> dict:
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
        "anthropic": {"model": "claude-sonnet-5",
                      "available_models": ["claude-opus-4-8", "claude-sonnet-5"],
                      "max_tokens": 1024, "cache_ttl": "1h", "thinking": {"default": "Off"}},
        "router": {"enabled": True, "pin": "", "full_breakdown_max_tokens": 2048},
        "web_search": {"enabled": True, "max_uses": 3},
        "personality": {"enabled": False},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "PERSONA", "voice_name": "Sarah",
                       "output_format": "pcm_16000"},
        "conversation": {"max_turns": 20},
        "elite": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "crew": {"enabled": True, "file": str(tmp_path / "crew.json"), "roster": [], **crew_extra},
        "memory": {"enabled": False, "dir": str(tmp_path / "memory"), "cap": 500},
        "logging": {"dir": str(tmp_path / "logs")},
    }


class _FakeCtx:
    """A duck-typed EDContext exposing just the two snapshot getters the fleet builder reads."""
    def __init__(self, loadout=None, stored=None):
        self._loadout, self._stored = loadout, stored

    def loadout_snapshot(self):
        return self._loadout

    def stored_ships_snapshot(self):
        return self._stored

    # the editor also probes these; keep them inert
    def npc_crew_hired(self):
        return []


def _client(tmp_path, monkeypatch, *, cfg=None, ctx=None):
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")
    core = App(cfg or _cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    if ctx is not None:
        core.ed_ctx = ctx
    flask_app = create_app(core)
    flask_app.config.update(TESTING=True)
    return flask_app.test_client(), core


def _get(c):
    return c.get("/api/crew").get_json()


def _save(c, members, *, base_version, ship_id=None, force=False):
    body = {"members": members, "base_version": base_version, "force": force}
    if ship_id is not None:
        body["ship_id"] = ship_id
    return c.post("/api/crew", json=body)


# ============================================================================================
# Fleet dimension
# ============================================================================================

def test_fleet_unions_loadout_and_stored_ships(tmp_path, monkeypatch):
    ctx = _FakeCtx(
        loadout=LoadoutSnapshot(ship="krait_light", ship_name="Persephone", ship_id=42),
        stored=StoredShipsSnapshot(ships=(
            StoredShip(ship_type="sidewinder", name="Runabout", ship_id=7),)))
    c, _ = _client(tmp_path, monkeypatch, ctx=ctx)
    fleet = {f["ship_id"]: f for f in _get(c)["fleet"]}
    assert set(fleet) == {"42", "7"}
    assert fleet["42"]["active"] is True and fleet["7"]["active"] is False
    assert "Persephone" in fleet["42"]["label"]
    assert fleet["42"]["seats"] == 2 and fleet["7"]["seats"] == 1     # from the ship-spec table


def test_fleet_keeps_a_file_known_ship_when_snapshot_is_absent(tmp_path, monkeypatch):
    # A ship with a saved roster stays selectable even with NO StoredShips snapshot and NO loadout.
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(
        default=(CrewMember("Def"),),
        ships={"99": ShipRoster(label="Anaconda", hull="anaconda",
                                members=(CrewMember("Gunner"),))}))
    c, _ = _client(tmp_path, monkeypatch, cfg=_cfg(tmp_path, file=str(f)),
                   ctx=_FakeCtx(loadout=None, stored=None))
    fleet = {x["ship_id"]: x for x in _get(c)["fleet"]}
    assert "99" in fleet and fleet["99"]["has_roster"] is True
    assert fleet["99"]["seats"] == 4                                  # anaconda seats


# ============================================================================================
# Per-ship save preserves other rosters + 409 guard
# ============================================================================================

def test_per_ship_save_preserves_the_default_roster(tmp_path, monkeypatch):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(default=(CrewMember("Def"),), ships={}))
    c, core = _client(tmp_path, monkeypatch, cfg=_cfg(tmp_path, file=str(f)))
    version = _get(c)["version"]
    r = _save(c, [{"name": "ShipMate"}], base_version=version, ship_id="42")
    assert r.status_code == 200
    back = crew.load_roster_file({"crew": {"file": str(f)}})
    assert [m.name for m in back.default] == ["Def"]                  # default untouched
    assert [m.name for m in back.ships["42"].members] == ["ShipMate"]


def test_default_save_preserves_ship_rosters(tmp_path, monkeypatch):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(default=(CrewMember("Old"),),
                                        ships={"42": ShipRoster(members=(CrewMember("Keep"),))}))
    c, _ = _client(tmp_path, monkeypatch, cfg=_cfg(tmp_path, file=str(f)))
    version = _get(c)["version"]
    assert _save(c, [{"name": "NewDef"}], base_version=version).status_code == 200   # default
    back = crew.load_roster_file({"crew": {"file": str(f)}})
    assert [m.name for m in back.default] == ["NewDef"]
    assert [m.name for m in back.ships["42"].members] == ["Keep"]     # ship roster preserved


def test_stale_save_is_rejected_with_409(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = _save(c, [{"name": "Nyx"}], base_version="stale-token", ship_id="42")
    assert r.status_code == 409


def test_snapshot_exposes_per_ship_rosters(tmp_path, monkeypatch):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(
        default=(CrewMember("Def"),),
        ships={"42": ShipRoster(label="Krait", hull="krait_light",
                                members=(CrewMember("Orin"),))}))
    c, _ = _client(tmp_path, monkeypatch, cfg=_cfg(tmp_path, file=str(f)))
    snap = _get(c)
    assert [m["name"] for m in snap["members"]] == ["Def"]            # default in `members`
    assert [m["name"] for m in snap["rosters"]["42"]["members"]] == ["Orin"]
    assert snap["rosters"]["42"]["seats"] == 2


# ============================================================================================
# Seat cap in the editor (§5) — a per-ship save truncates to the hull's seats
# ============================================================================================

def test_per_ship_save_truncates_to_seats_when_cap_on(tmp_path, monkeypatch):
    # A brand-new per-ship roster: the hull (and thus the seat count) comes from the journal fleet.
    ctx = _FakeCtx(loadout=LoadoutSnapshot(ship="sidewinder", ship_name="Runabout", ship_id=7))
    c, _ = _client(tmp_path, monkeypatch,
                   cfg=_cfg(tmp_path, limit_to_seats=True), ctx=ctx)
    version = _get(c)["version"]
    # Save 3 members to a 1-seat Sidewinder -> server truncates to 1.
    r = _save(c, [{"name": "A"}, {"name": "B"}, {"name": "C"}], base_version=version, ship_id="7")
    assert r.status_code == 200
    back = crew.load_roster_file({"crew": {"file": str(tmp_path / "crew.json")}})
    assert [m.name for m in back.ships["7"].members] == ["A"]
    assert back.ships["7"].hull == "sidewinder"                       # hull recorded from journal


def test_default_save_is_not_seat_capped(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, cfg=_cfg(tmp_path, limit_to_seats=True))
    version = _get(c)["version"]
    r = _save(c, [{"name": f"D{i}"} for i in range(5)], base_version=version)   # default roster
    assert r.status_code == 200
    assert len(_get(c)["members"]) == 5                                # Default is exempt
