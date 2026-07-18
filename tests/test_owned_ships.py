"""Unit tests for the owned-ships registry (issue #134) — offline, no device/network.

Covers the pure `fold` over the four Shipyard ownership events (new adds + goes active, sell
removes, part-exchange buy removes, swap marks active), the snapshot reconcilers from Loadout /
StoredShips (upsert + locations, never remove, corrections survive), manual CRUD, the persisted
`OwnedShipsRegistry` load/save roundtrip + corrupt-file degradation, the journal wiring through
`EDContext.apply_journal_event`, and the voice capability's list/add/remove.
"""
from __future__ import annotations

import json

from covas.capabilities.owned_ships_capability import OwnedShipsCapability
from covas.ed.context import EDContext
from covas.ed.journal import apply_journal_event
from covas.ed.loadout import LoadoutSnapshot
from covas.ed.owned_ships import (OwnedShipsRegistry, fold, match_ships,
                                  reconcile_loadout, reconcile_stored)
from covas.ed.stored import StoredShip, StoredShipsSnapshot


# --- pure fold over each Shipyard event -------------------------------------------------

def test_shipyard_new_adds_record_and_marks_active():
    entries = fold({}, {"event": "ShipyardNew", "timestamp": "2026-01-01T00:00:00Z",
                        "ShipType": "python", "NewShipID": 42})
    assert entries["42"]["ship_type"] == "python"
    assert entries["42"]["active"] is True
    assert entries["42"]["manual"] is False
    assert entries["42"]["last_seen"] == "2026-01-01T00:00:00Z"


def test_shipyard_new_makes_only_the_new_ship_active():
    entries = {"7": {"ship_type": "sidewinder", "active": True}}
    entries = fold(entries, {"event": "ShipyardNew", "ShipType": "python", "NewShipID": 42})
    assert entries["42"]["active"] is True
    assert entries["7"]["active"] is False


def test_shipyard_sell_removes_by_sell_ship_id():
    entries = {"7": {"ship_type": "cobramkiii"}}
    entries = fold(entries, {"event": "ShipyardSell", "ShipType": "cobramkiii", "SellShipID": 7})
    assert entries == {}


def test_shipyard_buy_part_exchange_removes_the_traded_in_ship():
    # ShipyardBuy carries the SOLD ship (SellShipID) but NOT the new ShipID — it only removes.
    entries = {"7": {"ship_type": "cobramkiii"}}
    entries = fold(entries, {"event": "ShipyardBuy", "ShipType": "python",
                             "SellShipType": "cobramkiii", "SellShipID": 7})
    assert entries == {}


def test_shipyard_buy_without_sale_adds_nothing():
    entries = fold({}, {"event": "ShipyardBuy", "ShipType": "python", "ShipPrice": 56000000})
    assert entries == {}   # the record is born on the following ShipyardNew


def test_shipyard_swap_marks_ship_active():
    entries = {"7": {"ship_type": "cobramkiii", "active": True},
               "42": {"ship_type": "python", "active": False}}
    entries = fold(entries, {"event": "ShipyardSwap", "ShipType": "python", "ShipID": 42,
                             "StoreOldShip": "cobramkiii", "StoreShipID": 7})
    assert entries["42"]["active"] is True
    assert entries["7"]["active"] is False


def test_shipyard_swap_into_unseen_ship_records_it():
    entries = fold({}, {"event": "ShipyardSwap", "ShipType": "krait_mkii", "ShipID": 99})
    assert entries["99"]["ship_type"] == "krait_mkii"
    assert entries["99"]["active"] is True


def test_fold_ignores_unrelated_and_malformed_events():
    assert fold({}, {"event": "FSDJump", "StarSystem": "Sol"}) == {}
    assert fold({}, {"event": "ShipyardNew", "ShipType": "python"}) == {}   # no NewShipID
    assert fold({}, {"event": "ShipyardSell"}) == {}                        # no SellShipID


# --- reconcile from Loadout (active ship) ------------------------------------------------

def _loadout(**kw):
    base = dict(ship="python", ship_id=42, ship_name="Void Runner", ship_ident="VR-01",
                timestamp="2026-02-02T00:00:00Z")
    base.update(kw)
    return LoadoutSnapshot(**base)


def test_reconcile_loadout_adds_active_ship_with_labels():
    entries = reconcile_loadout({}, _loadout())
    rec = entries["42"]
    assert rec["ship_type"] == "python"
    assert rec["name"] == "Void Runner"
    assert rec["ident"] == "VR-01"
    assert rec["active"] is True


def test_reconcile_loadout_none_is_noop():
    assert reconcile_loadout({}, None) == {}


def test_reconcile_loadout_does_not_clobber_a_manual_name():
    # A hand-typed correction survives the next Loadout event.
    entries = {"42": {"ship_type": "python", "name": "My Baby", "manual": True}}
    entries = reconcile_loadout(entries, _loadout(ship_name="Void Runner"))
    assert entries["42"]["name"] == "My Baby"      # manual name preserved
    assert entries["42"]["active"] is True         # but the active FACT still updates


# --- reconcile from StoredShips (locations, never remove) --------------------------------

def _stored(*ships, station="Jameson Memorial", system="Shinrarta Dezhra"):
    return StoredShipsSnapshot(station=station, system=system,
                               timestamp="2026-03-03T00:00:00Z", ships=tuple(ships))


def test_reconcile_stored_upserts_and_sets_locations():
    snap = _stored(
        StoredShip(ship_type="cobramkiii", ship_id=7, here=True),
        StoredShip(ship_type="anaconda", ship_id=8, here=False, system="Sol"),
    )
    entries = reconcile_stored({}, snap)
    assert entries["7"]["station"] == "Jameson Memorial"
    assert entries["7"]["system"] == "Shinrarta Dezhra"
    assert entries["8"]["system"] == "Sol"
    assert entries["8"]["station"] is None


def test_reconcile_stored_never_removes_a_manual_add():
    # A manually-added ship isn't deleted by a StoredShips snapshot that doesn't list it.
    entries = {"-1": {"ship_type": "python", "manual": True, "active": False}}
    entries = reconcile_stored(entries, _stored(
        StoredShip(ship_type="cobramkiii", ship_id=7, here=True)))
    assert "-1" in entries                        # survived
    assert entries["-1"]["ship_type"] == "python"


def test_reconcile_stored_preserves_a_manual_name():
    entries = {"7": {"ship_type": "cobramkiii", "name": "Old Faithful", "manual": True}}
    entries = reconcile_stored(entries, _stored(
        StoredShip(ship_type="cobramkiii", ship_id=7, name="Autoname", here=True)))
    assert entries["7"]["name"] == "Old Faithful"


# --- manual CRUD ------------------------------------------------------------------------

def test_add_manual_ship_mints_synthetic_negative_id():
    reg = OwnedShipsRegistry()
    rec = reg.add("Python", name="Void Runner")
    assert rec is not None
    assert int(rec["ship_id"]) < 0        # synthetic, can't collide with a real ShipID
    assert rec["ship_type"] == "python"
    assert rec["name"] == "Void Runner"
    assert rec["manual"] is True


def test_add_blank_type_is_rejected():
    assert OwnedShipsRegistry().add("   ") is None


def test_remove_by_ship_id():
    reg = OwnedShipsRegistry({"42": {"ship_type": "python"}})
    assert reg.remove(42) is True
    assert reg.entries() == {}
    assert reg.remove(42) is False        # already gone


def test_remove_matching_single_hit_removes():
    reg = OwnedShipsRegistry({"7": {"ship_type": "cobramkiii"}})
    removed, matches = reg.remove_matching("cobra")
    assert removed is True
    assert len(matches) == 1
    assert reg.entries() == {}


def test_remove_matching_ambiguous_removes_nothing():
    reg = OwnedShipsRegistry({"1": {"ship_type": "cobramkiii"},
                              "2": {"ship_type": "cobramkiv"}})
    removed, matches = reg.remove_matching("cobra")
    assert removed is False
    assert len(matches) == 2
    assert set(reg.entries()) == {"1", "2"}   # both untouched


def test_match_ships_by_name_and_ident():
    entries = {"42": {"ship_type": "python", "name": "Void Runner", "ident": "VR-01"}}
    assert match_ships(entries, "void runner")[0][0] == "42"
    assert match_ships(entries, "VR-01")[0][0] == "42"
    assert match_ships(entries, "") == []


# --- owned() ordering -------------------------------------------------------------------

def test_owned_lists_active_first_then_newest():
    reg = OwnedShipsRegistry({
        "1": {"ship_type": "sidewinder", "active": False, "last_seen": "2026-01-01T00:00:00Z"},
        "2": {"ship_type": "python", "active": True, "last_seen": "2026-02-02T00:00:00Z"},
        "3": {"ship_type": "anaconda", "active": False, "last_seen": "2026-09-09T00:00:00Z"},
    })
    order = [r["ship_id"] for r in reg.owned()]
    assert order[0] == "2"          # active first
    assert order[1:] == ["3", "1"]  # then newest last_seen


# --- persisted registry: load / save / corrupt -----------------------------------------

def test_registry_load_save_roundtrip(tmp_path):
    p = tmp_path / "owned_ships.json"
    reg = OwnedShipsRegistry(path=p)
    assert reg.apply_event({"event": "ShipyardNew", "timestamp": "t",
                            "ShipType": "python", "NewShipID": 42}) is True
    assert p.exists()
    reloaded = OwnedShipsRegistry.load(p)
    assert reloaded.entries()["42"]["ship_type"] == "python"
    assert reloaded.entries()["42"]["active"] is True


def test_apply_event_reports_no_change_for_noise(tmp_path):
    reg = OwnedShipsRegistry(path=tmp_path / "r.json")
    assert reg.apply_event({"event": "FSDJump"}) is False


def test_registry_load_corrupt_file_degrades_to_empty(tmp_path):
    p = tmp_path / "owned_ships.json"
    p.write_text("{ not json", encoding="utf-8")
    assert OwnedShipsRegistry.load(p).entries() == {}   # must NOT raise


def test_registry_load_non_dict_file_degrades_to_empty(tmp_path):
    p = tmp_path / "owned_ships.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert OwnedShipsRegistry.load(p).entries() == {}


def test_registry_load_drops_malformed_rows(tmp_path):
    p = tmp_path / "owned_ships.json"
    p.write_text(json.dumps({"1": {"ship_type": "python"},
                             "2": {"no_type": True}, "3": "junk"}), encoding="utf-8")
    assert set(OwnedShipsRegistry.load(p).entries()) == {"1"}


def test_registry_load_missing_path_is_empty():
    assert OwnedShipsRegistry.load(None).entries() == {}


# --- journal wiring through EDContext ---------------------------------------------------

def test_apply_journal_event_folds_shipyard_into_context_registry(tmp_path):
    ctx = EDContext()
    ctx.set_owned_ships_registry(OwnedShipsRegistry(path=tmp_path / "owned_ships.json"))
    apply_journal_event(ctx, {"event": "ShipyardNew", "timestamp": "t",
                              "ShipType": "python", "NewShipID": 42})
    owned = ctx.owned_ships()
    assert [r["ship_type"] for r in owned] == ["python"]
    apply_journal_event(ctx, {"event": "ShipyardSell", "SellShipID": 42})
    assert ctx.owned_ships() == []


def test_apply_journal_loadout_reconciles_active_ship(tmp_path):
    ctx = EDContext()
    ctx.set_owned_ships_registry(OwnedShipsRegistry(path=tmp_path / "s.json"))
    apply_journal_event(ctx, {"event": "Loadout", "Ship": "python", "ShipID": 42,
                              "ShipName": "Void Runner", "timestamp": "t"})
    owned = ctx.owned_ships()
    assert owned[0]["active"] is True
    assert owned[0]["name"] == "Void Runner"


def test_apply_journal_stored_reconcile_does_not_clobber_manual(tmp_path):
    # Corrections survive the next journal event: a manual add, then a StoredShips snapshot
    # that predates it, keeps the manual ship AND its name.
    ctx = EDContext()
    ctx.set_owned_ships_registry(OwnedShipsRegistry(path=tmp_path / "s.json"))
    added = ctx.add_owned_ship("Python", name="Void Runner")
    assert added is not None
    apply_journal_event(ctx, {"event": "StoredShips", "StationName": "Jameson",
                              "StarSystem": "Shinrarta", "ShipsHere": [
                                  {"ShipType": "cobramkiii", "ShipID": 7}]})
    types = sorted(r["ship_type"] for r in ctx.owned_ships())
    assert types == ["cobramkiii", "python"]        # manual python survived


def test_apply_journal_event_no_registry_is_a_noop():
    ctx = EDContext()   # no registry installed
    apply_journal_event(ctx, {"event": "ShipyardNew", "ShipType": "python", "NewShipID": 1})
    assert ctx.owned_ships() == []


# --- voice capability -------------------------------------------------------------------

def _capability(reg: OwnedShipsRegistry) -> OwnedShipsCapability:
    return OwnedShipsCapability(
        get_owned=reg.owned,
        add_ship=lambda st, **kw: reg.add(st, **kw),
        remove_ship=reg.remove_matching,
    )


def test_capability_lists_owned_ships():
    reg = OwnedShipsRegistry({"42": {"ship_type": "python", "active": True}})
    out = _capability(reg).run_tool("list_owned_ships", {})
    assert "own 1 ship" in out
    assert "Python" in out


def test_capability_list_empty():
    out = _capability(OwnedShipsRegistry()).run_tool("list_owned_ships", {})
    assert "haven't recorded" in out


def test_capability_add_ship():
    reg = OwnedShipsRegistry()
    out = _capability(reg).run_tool("add_owned_ship", {"ship_type": "Python",
                                                       "name": "Void Runner"})
    assert "Added" in out and "Void Runner" in out
    assert any(r["ship_type"] == "python" for r in reg.owned())


def test_capability_remove_ship():
    reg = OwnedShipsRegistry({"7": {"ship_type": "cobramkiii"}})
    out = _capability(reg).run_tool("remove_owned_ship", {"ship": "cobra"})
    assert "Removed" in out
    assert reg.owned() == []


def test_capability_remove_ambiguous_asks():
    reg = OwnedShipsRegistry({"1": {"ship_type": "cobramkiii"},
                              "2": {"ship_type": "cobramkiv"}})
    out = _capability(reg).run_tool("remove_owned_ship", {"ship": "cobra"})
    assert "More than one" in out
    assert len(reg.owned()) == 2


def test_capability_remove_unknown_lists_owned():
    reg = OwnedShipsRegistry({"42": {"ship_type": "python"}})
    out = _capability(reg).run_tool("remove_owned_ship", {"ship": "cutter"})
    assert "don't see" in out and "Python" in out
