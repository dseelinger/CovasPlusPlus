"""Unit tests for the hired NPC-crew registry (issue #125) — offline, no device/network.

Covers the pure `fold` over each of the five journal events (incl. fire-removes and a
name-resurface via a wage event), the persisted `NpcCrewRegistry` load/save roundtrip and
corrupt-file degradation, the `hired()` datalist shaping, and the journal wiring through
`EDContext.apply_journal_event`.
"""
from __future__ import annotations

import json

from covas.ed.context import EDContext
from covas.ed.journal import apply_journal_event
from covas.ed.npc_crew import NpcCrewRegistry, combat_rank_name, fold


def _hire(**kw):
    e = {"event": "CrewHire", "timestamp": "2026-01-01T00:00:00Z",
         "Name": "Zeta", "CrewID": 1001, "Faction": "Fed", "Cost": 15000, "CombatRank": 5}
    e.update(kw)
    return e


# --- pure fold over each event ----------------------------------------------------------

def test_crew_hire_adds_entry_with_rank_and_faction():
    entries = fold({}, _hire())
    assert entries == {"1001": {"name": "Zeta", "last_seen": "2026-01-01T00:00:00Z",
                                "faction": "Fed", "combat_rank": 5}}


def test_crew_assign_upserts_and_refreshes_last_seen():
    entries = fold({}, _hire())
    entries = fold(entries, {"event": "CrewAssign", "timestamp": "2026-02-02T00:00:00Z",
                             "Name": "Zeta", "CrewID": 1001, "Role": "Active"})
    assert entries["1001"]["last_seen"] == "2026-02-02T00:00:00Z"
    # CrewAssign.Role (a game DUTY) is NOT stored as our crew role (non-goal).
    assert "role" not in entries["1001"]
    assert entries["1001"]["combat_rank"] == 5  # prior value preserved


def test_npc_crew_paid_wage_resurfaces_a_name():
    # A pilot known only from a much-later wage event still lands in the registry.
    entries = fold({}, {"event": "NpcCrewPaidWage", "timestamp": "2026-03-03T00:00:00Z",
                        "NpcCrewName": "Vega", "NpcCrewId": 2002, "Amount": 500})
    assert entries["2002"]["name"] == "Vega"
    assert entries["2002"]["last_seen"] == "2026-03-03T00:00:00Z"


def test_npc_crew_rank_updates_combat_rank():
    entries = fold({}, _hire())
    entries = fold(entries, {"event": "NpcCrewRank", "timestamp": "2026-04-04T00:00:00Z",
                             "NpcCrewName": "Zeta", "NpcCrewId": 1001, "RankCombat": 8})
    assert entries["1001"]["combat_rank"] == 8


def test_crew_fire_removes_by_crew_id():
    entries = fold({}, _hire())
    entries = fold(entries, {"event": "CrewFire", "timestamp": "2026-05-05T00:00:00Z",
                             "Name": "Zeta", "CrewID": 1001})
    assert entries == {}


def test_rehire_under_new_crew_id_is_a_fresh_entry():
    entries = fold({}, _hire())
    entries = fold(entries, {"event": "CrewFire", "timestamp": "t", "CrewID": 1001})
    entries = fold(entries, _hire(CrewID=1002))  # same name, new id
    assert set(entries) == {"1002"}


def test_fold_ignores_unhandled_and_multicrew_human_events():
    assert fold({}, {"event": "CrewMemberJoins", "Crew": "SomeCmdr"}) == {}
    assert fold({}, {"event": "FSDJump", "StarSystem": "Sol"}) == {}


def test_fold_ignores_event_missing_crew_id():
    assert fold({}, {"event": "CrewHire", "Name": "Nobody"}) == {}


def test_fold_never_stores_a_nameless_ghost():
    # A wage with no name and no prior entry contributes nothing.
    assert fold({}, {"event": "NpcCrewPaidWage", "NpcCrewId": 9, "Amount": 1}) == {}


# --- combat rank naming -----------------------------------------------------------------

def test_combat_rank_name_maps_ordinals_and_degrades():
    assert combat_rank_name(0) == "Harmless"
    assert combat_rank_name(8) == "Elite"
    assert combat_rank_name(99) == ""
    assert combat_rank_name(None) == ""
    assert combat_rank_name(True) == ""  # bool is not a rank


# --- persisted registry: load / save / corrupt ------------------------------------------

def test_registry_load_save_roundtrip(tmp_path):
    p = tmp_path / "npc_crew.json"
    reg = NpcCrewRegistry(path=p)
    assert reg.apply_event(_hire()) is True
    assert p.exists()
    reloaded = NpcCrewRegistry.load(p)
    assert reloaded.entries() == {"1001": {"name": "Zeta", "last_seen": "2026-01-01T00:00:00Z",
                                           "faction": "Fed", "combat_rank": 5}}


def test_apply_event_reports_no_change_for_noise(tmp_path):
    reg = NpcCrewRegistry(path=tmp_path / "r.json")
    assert reg.apply_event({"event": "FSDJump"}) is False


def test_registry_load_corrupt_file_degrades_to_empty(tmp_path):
    p = tmp_path / "npc_crew.json"
    p.write_text("{ not json", encoding="utf-8")
    reg = NpcCrewRegistry.load(p)   # must NOT raise
    assert reg.entries() == {}


def test_registry_load_non_dict_file_degrades_to_empty(tmp_path):
    p = tmp_path / "npc_crew.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert NpcCrewRegistry.load(p).entries() == {}


def test_registry_load_drops_malformed_rows(tmp_path):
    p = tmp_path / "npc_crew.json"
    p.write_text(json.dumps({"1": {"name": "Ok", "last_seen": "t"},
                             "2": {"no_name": True}, "3": "junk"}), encoding="utf-8")
    assert set(NpcCrewRegistry.load(p).entries()) == {"1"}


def test_registry_load_missing_path_is_empty():
    assert NpcCrewRegistry.load(None).entries() == {}


# --- hired() datalist shaping -----------------------------------------------------------

def test_hired_returns_name_and_rank_newest_first_deduped():
    reg = NpcCrewRegistry()
    reg.apply_event(_hire(CrewID=1, Name="Old", timestamp="2026-01-01T00:00:00Z"))
    reg.apply_event(_hire(CrewID=2, Name="New", timestamp="2026-09-09T00:00:00Z"))
    reg.apply_event(_hire(CrewID=3, Name="New", timestamp="2026-05-05T00:00:00Z"))  # dup name
    hired = reg.hired()
    assert [h["name"] for h in hired] == ["New", "Old"]
    assert hired[0]["combat_rank"] == 5


# --- journal wiring through EDContext ---------------------------------------------------

def test_apply_journal_event_folds_into_the_context_registry(tmp_path):
    ctx = EDContext()
    ctx.set_npc_crew_registry(NpcCrewRegistry(path=tmp_path / "npc_crew.json"))
    apply_journal_event(ctx, _hire())
    assert ctx.npc_crew_hired() == [{"name": "Zeta", "combat_rank": 5}]
    apply_journal_event(ctx, {"event": "CrewFire", "timestamp": "t", "CrewID": 1001})
    assert ctx.npc_crew_hired() == []


def test_apply_journal_event_no_registry_is_a_noop():
    ctx = EDContext()   # no registry installed
    # Must not raise, and hired() is empty.
    apply_journal_event(ctx, _hire())
    assert ctx.npc_crew_hired() == []
