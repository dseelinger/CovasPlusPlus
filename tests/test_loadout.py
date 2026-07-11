"""Unit tests for the ship-loadout snapshot + symbol naming (N9; offline, DESIGN §9).

A RECORDED journal `Loadout` event (tests/fixtures/journal_loadout_corsair.json — a real
Corsair with an engineered power distributor and a stock SCO frame shift drive, ident
sanitized) drives the parse tests; the naming tests lock the curated symbol->spoken-name
tables and their structural fallbacks. No network, no real journal directory.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.ed.context import EDContext
from covas.ed.journal import apply_journal_event
from covas.ed.loadout import LoadoutSnapshot, parse_loadout
from covas.ed.module_names import (blueprint_name, experimental_name, find_modules,
                                   modifier_label, module_name, slot_name)

_FIXTURE = Path(__file__).parent / "fixtures" / "journal_loadout_corsair.json"


def _event() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def snap() -> LoadoutSnapshot:
    return parse_loadout(_event())


# --- parsing the recorded event -------------------------------------------------------------

def test_parse_captures_the_whole_ship(snap):
    assert snap.ship == "corsair"
    assert snap.ship_name is None                  # the real event had a blank " " name
    assert snap.ship_ident == "AB-12C"
    assert snap.cargo_capacity == 128
    assert snap.fuel_capacity == pytest.approx(32.0)
    assert snap.max_jump_range == pytest.approx(33.03, abs=0.01)
    assert len(snap.modules) == 28                 # every fitted module, cosmetic ones too


def test_parse_captures_engineering_in_full(snap):
    pd = next(m for m in snap.modules if m.slot == "PowerDistributor")
    eng = pd.engineering
    assert eng is not None and pd.engineered
    assert eng.blueprint == "PowerDistributor_HighFrequency"
    assert eng.level == 5 and eng.quality == pytest.approx(1.0)
    assert eng.engineer == "The Dweller"
    assert eng.experimental == "special_powerdistributor_fast"
    assert eng.experimental_localised == "Super Conduits"
    assert len(eng.modifiers) == 6
    recharge = next(mod for mod in eng.modifiers if mod.label == "WeaponsRecharge")
    assert recharge.value == pytest.approx(9.1988)
    assert recharge.original == pytest.approx(6.1)
    assert recharge.pct_change() == pytest.approx(50.8, abs=0.1)


def test_unengineered_module_has_no_engineering(snap):
    fsd = next(m for m in snap.modules if m.slot == "FrameShiftDrive")
    assert fsd.item == "int_hyperdrive_overcharge_size5_class5"
    assert fsd.engineering is None and not fsd.engineered
    assert snap.engineered_modules() == tuple(
        m for m in snap.modules if m.slot == "PowerDistributor")


def test_parse_tolerates_junk():
    # A module missing Slot/Item is dropped; malformed Engineering becomes None; an empty
    # event still parses. The watcher must never choke on a journal quirk.
    e = {"event": "Loadout", "Ship": "corsair",
         "Modules": [{"Item": "int_engine_size5_class5"},           # no Slot -> dropped
                     {"Slot": "PowerPlant", "Item": "int_powerplant_size5_class5",
                      "Engineering": "not a dict"}]}
    s = parse_loadout(e)
    assert len(s.modules) == 1 and s.modules[0].engineering is None
    assert parse_loadout({}).modules == ()


# --- journal wiring: Loadout lands on EDContext ----------------------------------------------

def test_apply_journal_event_stores_the_snapshot():
    ctx = EDContext()
    assert ctx.loadout_snapshot() is None
    patch = apply_journal_event(ctx, _event())
    assert patch.get("ship") == "Corsair"          # the existing context patch still applies
    stored = ctx.loadout_snapshot()
    assert stored is not None and len(stored.modules) == 28
    # A later, different Loadout REPLACES it wholesale (each event is complete).
    apply_journal_event(ctx, {"event": "Loadout", "Ship": "sidewinder", "Modules": [
        {"Slot": "PowerPlant", "Item": "int_powerplant_size2_class1"}]})
    assert len(ctx.loadout_snapshot().modules) == 1
    # Non-Loadout events leave it alone.
    apply_journal_event(ctx, {"event": "FSDJump", "StarSystem": "Sol"})
    assert len(ctx.loadout_snapshot().modules) == 1


# --- module symbol -> spoken name -------------------------------------------------------------

def test_internal_module_names():
    assert module_name("int_powerplant_size7_class5") == "7A Power Plant"
    assert module_name("int_hyperdrive_size5_class5") == "5A Frame Shift Drive"
    assert module_name("int_hyperdrive_overcharge_size5_class5") == "5A Frame Shift Drive (SCO)"
    assert module_name("int_engine_size7_class5") == "7A Thrusters"
    assert module_name("int_dronecontrol_collection_size3_class5") \
        == "3A Collector Limpet Controller"
    assert module_name("int_guardianfsdbooster_size5") == "5 Guardian FSD Booster"
    assert module_name("int_supercruiseassist") == "Supercruise Assist"
    assert module_name("int_dockingcomputer_advanced") == "Advanced Docking Computer"


def test_hardpoint_and_utility_names():
    assert module_name("hpt_multicannon_gimbal_medium") == "medium gimballed Multi-Cannon"
    assert module_name("hpt_railgun_fixed_medium") == "medium fixed Rail Gun"
    assert module_name("hpt_shieldbooster_size0_class5") == "0A Shield Booster"
    assert module_name("hpt_chafflauncher_tiny") == "Chaff Launcher"
    assert module_name("hpt_crimescanner_size0_class5") == "0A Kill Warrant Scanner"


def test_armour_and_misc_names():
    assert module_name("corsair_armour_grade1") == "Lightweight Alloy"
    assert module_name("federation_corvette_armour_reactive") == "Reactive Surface Composite"
    assert module_name("modularcargobaydoor") == "cargo hatch"
    assert module_name("voicepack_verity") == "COVAS voice pack"


def test_unknown_symbols_fall_back_readably():
    # Never a raw symbol in speech — decompose what's structural, prettify the rest.
    assert module_name("int_widgetizer_size2_class1") == "2E Widgetizer"
    assert module_name("some_future_thing") == "Some Future Thing"


def test_blueprint_names():
    assert blueprint_name("FSD_LongRange") == "Increased Range"
    assert blueprint_name("PowerDistributor_HighFrequency") == "Charge Enhanced"
    assert blueprint_name("Engine_Dirty") == "Dirty Drive Tuning"
    assert blueprint_name("Weapon_Overcharged") == "Overcharged"
    assert blueprint_name("Widget_SuperShiny") == "Super Shiny"      # camel-split fallback


def test_experimental_names_prefer_the_journal_localised_string():
    assert experimental_name("special_fsd_heavy") == "Mass Manager"
    assert experimental_name("special_unknown_thing", "Fancy Pants") == "Fancy Pants"
    assert experimental_name("special_unknown_thing") == "Unknown Thing"
    assert experimental_name(None) is None


def test_slot_and_modifier_labels():
    assert slot_name("MainEngines") == "thrusters"
    assert slot_name("FrameShiftDrive") == "frame shift drive"
    assert slot_name("Radar") == "sensors"
    assert slot_name("Slot04_Size5") == "optional slot 4, size 5"
    assert slot_name("TinyHardpoint2") == "utility mount 2"
    assert slot_name("MediumHardpoint1") == "medium hardpoint 1"
    assert modifier_label("WeaponsRecharge") == "weapons recharge"


# --- finding modules by spoken name -----------------------------------------------------------

def test_find_modules_by_alias_and_free_match(snap):
    assert [m.slot for m in find_modules(snap, "FSD")] == ["FrameShiftDrive"]
    assert [m.slot for m in find_modules(snap, "thrusters")] == ["MainEngines"]
    assert [m.slot for m in find_modules(snap, "distributor")] == ["PowerDistributor"]
    assert len(find_modules(snap, "multi-cannon")) == 3
    assert [m.item for m in find_modules(snap, "shields")] \
        == ["int_shieldgenerator_size6_class5"]
    assert [m.item for m in find_modules(snap, "fuel scoop")] \
        == ["int_fuelscoop_size5_class5"]
    assert find_modules(snap, "zabbleflux") == []
