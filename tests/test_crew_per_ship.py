"""Unit tests for PER-SHIP crew rosters (issue #127) — offline, no device/network.

Covers the roster-file schema v2 (back-compat with the pre-#127 bare list), active-roster
resolution by ship id, the all-rosters union (#124 input), the active-ship helpers + the runtime
cfg stamp used by the prompt path, the optional seat cap (§5), and `LoadoutSnapshot.ship_id`.

The editor's fleet dimension + per-ship save/copy are exercised in `test_crew_per_ship_web.py`;
here everything is pure/hermetic (a tmp JSON file, a duck-typed context, no network/device).
"""
from __future__ import annotations

import json

from covas import crew
from covas.crew import CrewMember, RosterFile, ShipRoster
from covas.ed.loadout import parse_loadout


def _member(name, **kw):
    return {"name": name, **kw}


# ============================================================================================
# 1. Schema v2 — back-compat with the bare list, round-trip, corrupt fallback
# ============================================================================================

def test_bare_list_loads_as_default_roster(tmp_path):
    f = tmp_path / "crew.json"
    f.write_text(json.dumps([_member("Nyx"), _member("Vela")]), encoding="utf-8")
    rf = crew.load_roster_file({"crew": {"file": str(f)}})
    assert [m.name for m in rf.default] == ["Nyx", "Vela"]
    assert rf.ships == {}


def test_v2_round_trips_default_and_ships(tmp_path):
    f = tmp_path / "crew.json"
    rf = RosterFile(
        default=(CrewMember("Nyx", "Terse."),),
        ships={"42": ShipRoster(label='Krait Phantom "Persephone"', hull="krait_light",
                                members=(CrewMember("Orin", "Sharp.", "V1"),))})
    crew.save_roster_file(f, rf)
    back = crew.load_roster_file({"crew": {"file": str(f)}})
    assert [m.name for m in back.default] == ["Nyx"]
    assert set(back.ships) == {"42"}
    sr = back.ships["42"]
    assert sr.label == 'Krait Phantom "Persephone"' and sr.hull == "krait_light"
    assert back.ships["42"].members == (CrewMember("Orin", "Sharp.", "V1"),)


def test_save_always_writes_v2_dict_even_from_bare_list(tmp_path):
    f = tmp_path / "crew.json"
    f.write_text(json.dumps([_member("Nyx")]), encoding="utf-8")   # legacy v1 file
    rf = crew.load_roster_file({"crew": {"file": str(f)}})
    crew.save_roster_file(f, rf)
    on_disk = json.loads(f.read_text(encoding="utf-8"))
    assert isinstance(on_disk, dict) and "default" in on_disk and "ships" in on_disk


def test_empty_ship_roster_is_dropped_on_save(tmp_path):
    f = tmp_path / "crew.json"
    rf = RosterFile(default=(CrewMember("Nyx"),),
                    ships={"42": ShipRoster(hull="krait_light", members=())})
    crew.save_roster_file(f, rf)
    assert json.loads(f.read_text(encoding="utf-8"))["ships"] == {}   # inherited ship: no clutter


def test_corrupt_file_degrades_to_config_fallback(tmp_path):
    f = tmp_path / "crew.json"
    f.write_text("{not json", encoding="utf-8")
    rf = crew.load_roster_file({"crew": {"file": str(f), "roster": ["Backup"]}})
    assert [m.name for m in rf.default] == ["Backup"] and rf.ships == {}


def test_legacy_config_roster_is_the_default_when_no_file(tmp_path):
    cfg = {"crew": {"file": str(tmp_path / "absent.json"), "roster": ["Nyx", "Vela"]}}
    rf = crew.load_roster_file(cfg)
    assert [m.name for m in rf.default] == ["Nyx", "Vela"]


# ============================================================================================
# 2. Active-roster resolution — ship-with-roster / ship-without / no-ship
# ============================================================================================

def _cfg_two_rosters(tmp_path, **crew_extra):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(
        default=(CrewMember("Def"),),
        ships={"42": ShipRoster(hull="krait_light", members=(CrewMember("ShipMate"),))}))
    return {"crew": {"file": str(f), "enabled": True, **crew_extra}}


def test_ship_with_roster_resolves_to_its_members(tmp_path):
    cfg = _cfg_two_rosters(tmp_path)
    assert [m.name for m in crew.load_members(cfg, "42")] == ["ShipMate"]


def test_ship_without_roster_resolves_to_default(tmp_path):
    cfg = _cfg_two_rosters(tmp_path)
    assert [m.name for m in crew.load_members(cfg, "999")] == ["Def"]


def test_no_ship_known_resolves_to_default(tmp_path):
    cfg = _cfg_two_rosters(tmp_path)
    assert [m.name for m in crew.load_members(cfg, None)] == ["Def"]


def test_empty_ship_roster_falls_back_to_default(tmp_path):
    f = tmp_path / "crew.json"
    # A ship key present but with NO members must inherit Default, not resolve to empty.
    crew.save_roster_file(f, RosterFile(default=(CrewMember("Def"),),
                                        ships={"42": ShipRoster(members=(CrewMember("X"),))}))
    # Re-write ship 42 as empty (save drops it, so simulate a hand-edited file with empty members).
    raw = {"default": [{"name": "Def", "persona": "", "voice_ref": "", "role": ""}],
           "ships": {"42": {"label": "", "hull": "", "members": []}}}
    f.write_text(json.dumps(raw), encoding="utf-8")
    assert [m.name for m in crew.load_members({"crew": {"file": str(f)}}, "42")] == ["Def"]


def test_runtime_stamp_selects_the_active_roster_when_no_explicit_id(tmp_path):
    cfg = _cfg_two_rosters(tmp_path)
    cfg["crew"]["_active_ship_id"] = "42"          # the App's runtime stamp
    assert [m.name for m in crew.load_members(cfg)] == ["ShipMate"]
    cfg["crew"]["_active_ship_id"] = ""            # blank -> default
    assert [m.name for m in crew.load_members(cfg)] == ["Def"]


# ============================================================================================
# 3. system_instruction / voice_ref_for / roster — per-ship, and cache-stability within a ship
# ============================================================================================

def test_instruction_differs_across_ships_but_is_stable_within_one(tmp_path):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(
        default=(CrewMember("Def"),),
        ships={"1": ShipRoster(members=(CrewMember("Alpha"),)),
               "2": ShipRoster(members=(CrewMember("Beta"),))}))
    cfg = {"crew": {"file": str(f), "enabled": True}}
    a, b = crew.system_instruction(cfg, "1"), crew.system_instruction(cfg, "2")
    assert "Alpha" in a and "Beta" not in a
    assert "Beta" in b and "Alpha" not in b
    assert a == crew.system_instruction(cfg, "1")   # byte-stable within a ship (cache-safe)


def test_voice_ref_for_reads_the_active_ship_roster(tmp_path):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(
        default=(CrewMember("Nyx", voice_ref="DEF"),),
        ships={"42": ShipRoster(members=(CrewMember("Nyx", voice_ref="SHIP"),))}))
    cfg = {"crew": {"file": str(f)}}
    assert crew.voice_ref_for(cfg, "Nyx", "42") == "SHIP"
    assert crew.voice_ref_for(cfg, "Nyx", None) == "DEF"


# ============================================================================================
# 4. all_members — the #124 union across every roster, deduped, uncapped
# ============================================================================================

def test_all_members_unions_default_and_ship_rosters_deduped(tmp_path):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(
        default=(CrewMember("Shared", "d"), CrewMember("OnlyDefault")),
        ships={"1": ShipRoster(members=(CrewMember("Shared", "ship"), CrewMember("OnlyShip"))),
               "2": ShipRoster(members=(CrewMember("OnlyTwo"),))}))
    names = [m.name for m in crew.all_members({"crew": {"file": str(f)}})]
    assert names == ["Shared", "OnlyDefault", "OnlyShip", "OnlyTwo"]   # first-wins on "Shared"


def test_all_members_is_uncapped_beyond_max_roster(tmp_path):
    f = tmp_path / "crew.json"
    ships = {str(i): ShipRoster(members=(CrewMember(f"S{i}"),)) for i in range(20)}
    crew.save_roster_file(f, RosterFile(default=(), ships=ships))
    assert len(crew.all_members({"crew": {"file": str(f)}})) == 20    # > _MAX_ROSTER (12)


# ============================================================================================
# 5. Active-ship helpers + the runtime cfg stamp (never persists to overrides)
# ============================================================================================

class _FakeCtx:
    def __init__(self, snap):
        self._snap = snap

    def loadout_snapshot(self):
        return self._snap


class _Snap:
    def __init__(self, ship_id):
        self.ship_id = ship_id


def test_active_ship_id_reads_the_loadout_snapshot():
    assert crew.active_ship_id(_FakeCtx(_Snap(42))) == "42"
    assert crew.active_ship_id(_FakeCtx(_Snap(None))) is None
    assert crew.active_ship_id(_FakeCtx(None)) is None
    assert crew.active_ship_id(None) is None


def test_stamp_active_ship_sets_and_clears_the_runtime_key():
    cfg = {"crew": {}}
    crew.stamp_active_ship(cfg, _FakeCtx(_Snap(7)))
    assert cfg["crew"]["_active_ship_id"] == "7"
    crew.stamp_active_ship(cfg, _FakeCtx(_Snap(None)))      # no active ship -> key removed
    assert "_active_ship_id" not in cfg["crew"]


def test_stamp_writes_only_to_cfg_not_a_separate_overrides_dict():
    # The App holds cfg and overrides as SEPARATE dicts; stamping cfg must never touch overrides.
    cfg, overrides = {"crew": {}}, {"crew": {}}
    crew.stamp_active_ship(cfg, _FakeCtx(_Snap(3)))
    assert "_active_ship_id" in cfg["crew"]
    assert "_active_ship_id" not in overrides["crew"]        # overrides is untouched


def test_build_system_uses_the_active_ship_roster(tmp_path):
    from covas.llm import build_system
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(
        default=(CrewMember("Def"),),
        ships={"42": ShipRoster(members=(CrewMember("ShipMate"),))}))
    cfg = {"personality": {"enabled": False}, "crew": {"enabled": True, "file": str(f)}}
    assert "ShipMate" in build_system(cfg, "42")
    assert "Def" in build_system(cfg, None) and "ShipMate" not in build_system(cfg, None)


# ============================================================================================
# 6. Seat cap (§5) — read-time truncation, unknown-hull fallback, Default exempt
# ============================================================================================

def test_seats_for_hull_resolves_known_hulls():
    assert crew.seats_for_hull("sidewinder") == 1
    assert crew.seats_for_hull("krait_light") == 2      # Krait Phantom
    assert crew.seats_for_hull("cutter") == 4           # Imperial Cutter


def test_seats_for_hull_unknown_is_none():
    assert crew.seats_for_hull("not_a_real_hull_xyz") is None
    assert crew.seats_for_hull("") is None


def _seat_cfg(tmp_path, hull, n_members, limit):
    f = tmp_path / "crew.json"
    members = tuple(CrewMember(f"M{i}") for i in range(n_members))
    crew.save_roster_file(f, RosterFile(default=(CrewMember("Def"),),
                                        ships={"42": ShipRoster(hull=hull, members=members)}))
    return {"crew": {"file": str(f), "limit_to_seats": limit}}


def test_seat_cap_on_truncates_per_ship_roster_to_hull_seats(tmp_path):
    cfg = _seat_cfg(tmp_path, "sidewinder", n_members=4, limit=True)   # sidewinder seats 1
    assert len(crew.load_members(cfg, "42")) == 1


def test_seat_cap_off_leaves_the_roster_uncapped(tmp_path):
    cfg = _seat_cfg(tmp_path, "sidewinder", n_members=4, limit=False)
    assert len(crew.load_members(cfg, "42")) == 4


def test_seat_cap_unknown_hull_falls_back_to_generic_cap(tmp_path):
    cfg = _seat_cfg(tmp_path, "not_a_real_hull_xyz", n_members=4, limit=True)
    assert len(crew.load_members(cfg, "42")) == 4       # no spec -> not truncated (generic cap)


def test_seat_cap_does_not_touch_the_default_roster(tmp_path):
    f = tmp_path / "crew.json"
    default = tuple(CrewMember(f"D{i}") for i in range(5))
    crew.save_roster_file(f, RosterFile(default=default, ships={}))
    cfg = {"crew": {"file": str(f), "limit_to_seats": True}}
    assert len(crew.load_members(cfg, None)) == 5       # Default is never seat-capped


# ============================================================================================
# 7. LoadoutSnapshot.ship_id — the stable active-ship key
# ============================================================================================

def test_parse_loadout_captures_ship_id():
    snap = parse_loadout({"Ship": "krait_light", "ShipID": 42, "ShipName": "Persephone"})
    assert snap.ship_id == 42 and snap.ship == "krait_light"


def test_parse_loadout_ship_id_absent_is_none():
    assert parse_loadout({"Ship": "sidewinder"}).ship_id is None


# ============================================================================================
# 8. save_members legacy helper preserves per-ship rosters
# ============================================================================================

def test_save_members_preserves_existing_ship_rosters(tmp_path):
    f = tmp_path / "crew.json"
    crew.save_roster_file(f, RosterFile(default=(CrewMember("Old"),),
                                        ships={"42": ShipRoster(members=(CrewMember("Keep"),))}))
    crew.save_members(f, [CrewMember("NewDefault")])     # legacy default-only save
    back = crew.load_roster_file({"crew": {"file": str(f)}})
    assert [m.name for m in back.default] == ["NewDefault"]
    assert [m.name for m in back.ships["42"].members] == ["Keep"]    # ship roster preserved
