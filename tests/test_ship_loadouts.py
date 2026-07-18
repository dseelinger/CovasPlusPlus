"""Unit tests for the per-ship loadout memory (issue #135) — offline, no device/network.

Covers the `LoadoutSnapshot` (de)serialization round-trip (modules + engineering + modifiers), the
persisted `ShipLoadoutStore` capture/get/switch-retains/save-load/corrupt-file degradation, and the
journal wiring through `EDContext.capture_loadout` (switching ships keeps the prior ship's build).
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.ed.context import EDContext
from covas.ed.journal import apply_journal_event
from covas.ed.loadout import Engineering, LoadoutSnapshot, Modifier, ShipModule, parse_loadout
from covas.ed.ship_loadouts import (ShipLoadoutStore, snapshot_from_dict, snapshot_to_dict)

_CORSAIR = Path(__file__).parent / "fixtures" / "journal_loadout_corsair.json"


def _corsair_snapshot() -> LoadoutSnapshot:
    return parse_loadout(json.loads(_CORSAIR.read_text(encoding="utf-8")))


def _mini(ship_id: int, ship: str = "python") -> LoadoutSnapshot:
    """A tiny hand-built snapshot with one engineered + one stock module (a modifier included)."""
    return LoadoutSnapshot(
        ship=ship, ship_name="Void Runner", ship_ident="VR-01", ship_id=ship_id,
        max_jump_range=42.5, cargo_capacity=128, fuel_capacity=32.0,
        timestamp="2026-01-01T00:00:00Z",
        modules=(
            ShipModule(slot="FrameShiftDrive", item="int_hyperdrive_size5_class5",
                       engineering=Engineering(
                           blueprint="FSD_LongRange", level=5, quality=1.0, engineer="Farseer",
                           experimental="special_fsd_heavy",
                           modifiers=(Modifier(label="FSDOptimalMass", value=1234.0,
                                               original=1050.0, less_is_good=False),))),
            ShipModule(slot="PowerDistributor", item="int_powerdistributor_size4_class5"),
        ),
    )


# --- (de)serialization round-trip ------------------------------------------------------------

def test_snapshot_roundtrip_preserves_everything():
    snap = _corsair_snapshot()
    rebuilt = snapshot_from_dict(snapshot_to_dict(snap))
    # Frozen dataclasses compare by value, so an exact round-trip is a single equality.
    assert rebuilt == snap
    assert len(rebuilt.engineered_modules()) == len(snap.engineered_modules())


def test_snapshot_roundtrip_keeps_engineering_and_modifiers():
    snap = _mini(46)
    d = snapshot_to_dict(snap)
    # The serialized form is plain JSON-able data (survives a JSON encode/decode).
    d2 = json.loads(json.dumps(d))
    rebuilt = snapshot_from_dict(d2)
    fsd = next(m for m in rebuilt.modules if m.slot == "FrameShiftDrive")
    assert fsd.engineering is not None
    assert fsd.engineering.blueprint == "FSD_LongRange" and fsd.engineering.level == 5
    assert fsd.engineering.modifiers[0].label == "FSDOptimalMass"
    assert rebuilt == snap


def test_from_dict_is_total_and_fail_soft():
    # A garbled module (no item) is dropped; a garbled engineering block becomes None; the
    # snapshot still loads rather than raising.
    raw = {"ship": "python", "ship_id": 7, "modules": [
        {"slot": "A", "item": "int_engine_size5_class5",
         "engineering": {"blueprint": ""}},      # blank blueprint -> engineering None
        {"slot": "B"},                            # no item -> dropped
        "not-a-dict",                             # junk -> dropped
    ]}
    snap = snapshot_from_dict(raw)
    assert snap is not None
    assert len(snap.modules) == 1
    assert snap.modules[0].engineering is None
    assert snapshot_from_dict("nonsense") is None


# --- the store: capture / get / switch retains -----------------------------------------------

def test_capture_and_get_by_ship_id():
    store = ShipLoadoutStore()
    assert store.capture(_mini(46)) is True
    got = store.get(46)
    assert got is not None and got.ship_id == 46
    assert got == _mini(46)
    assert store.get("46") == store.get(46)     # int and str keys are the same identity


def test_capture_second_ship_retains_the_first():
    store = ShipLoadoutStore()
    store.capture(_mini(46, ship="python"))
    store.capture(_mini(99, ship="anaconda"))    # "switch ships"
    assert store.get(46).ship == "python"        # prior ship's config survives the switch
    assert store.get(99).ship == "anaconda"
    assert sorted(store.ship_ids()) == ["46", "99"]


def test_capture_none_or_idless_is_noop():
    store = ShipLoadoutStore()
    assert store.capture(None) is False
    assert store.capture(LoadoutSnapshot(ship="python", ship_id=None)) is False
    assert store.ship_ids() == []


def test_capture_unchanged_build_does_not_rewrite():
    store = ShipLoadoutStore()
    assert store.capture(_mini(46)) is True
    assert store.capture(_mini(46)) is False     # identical build -> no change, no re-persist


def test_get_unknown_ship_is_none():
    store = ShipLoadoutStore()
    store.capture(_mini(46))
    assert store.get(1234) is None


# --- persistence: save / load / corrupt ------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path: Path):
    p = tmp_path / "ship_loadouts.json"
    store = ShipLoadoutStore(path=p)
    store.capture(_mini(12))                      # distinct id (the corsair fixture is ShipID 46)
    store.capture(_corsair_snapshot())
    assert p.exists()
    reloaded = ShipLoadoutStore.load(p)
    assert sorted(reloaded.ship_ids()) == sorted(store.ship_ids())
    assert reloaded.get(12) == _mini(12)
    assert reloaded.get(_corsair_snapshot().ship_id) == _corsair_snapshot()


def test_load_missing_file_is_empty(tmp_path: Path):
    store = ShipLoadoutStore.load(tmp_path / "nope.json")
    assert store.ship_ids() == []


def test_load_corrupt_file_degrades_to_empty(tmp_path: Path):
    p = tmp_path / "ship_loadouts.json"
    p.write_text("{ this is not json", encoding="utf-8")
    store = ShipLoadoutStore.load(p)         # must not raise
    assert store.ship_ids() == []


def test_load_drops_malformed_rows(tmp_path: Path):
    p = tmp_path / "ship_loadouts.json"
    p.write_text(json.dumps({
        "46": snapshot_to_dict(_mini(46)),
        "not-an-id": snapshot_to_dict(_mini(7)),  # bad key -> dropped
        "88": "junk",                              # bad value -> dropped
    }), encoding="utf-8")
    store = ShipLoadoutStore.load(p)
    assert store.ship_ids() == ["46"]


# --- journal wiring through EDContext --------------------------------------------------------

def test_journal_loadout_is_captured_per_ship():
    ctx = EDContext()
    ctx.set_ship_loadout_store(ShipLoadoutStore())
    corsair = json.loads(_CORSAIR.read_text(encoding="utf-8"))
    apply_journal_event(ctx, corsair)
    sid = parse_loadout(corsair).ship_id
    remembered = ctx.ship_loadout(sid)
    assert remembered is not None and remembered.ship == "corsair"
    assert str(sid) in ctx.remembered_ship_ids()


def test_switching_ships_via_journal_keeps_prior_config():
    ctx = EDContext()
    ctx.set_ship_loadout_store(ShipLoadoutStore())
    # Board ship 46 (python), then ship 99 (anaconda) — two synthetic Loadout events.
    ev_a = {"event": "Loadout", "Ship": "python", "ShipID": 46, "ShipName": "Void Runner",
            "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_size5_class5",
                         "Engineering": {"BlueprintName": "FSD_LongRange", "Level": 5}}]}
    ev_b = {"event": "Loadout", "Ship": "anaconda", "ShipID": 99, "ShipName": "The Ark",
            "Modules": [{"Slot": "MainEngines", "Item": "int_engine_size7_class5"}]}
    apply_journal_event(ctx, ev_a)
    apply_journal_event(ctx, ev_b)
    # The live current-ship loadout is the anaconda, but the python's build is still remembered.
    assert ctx.loadout_snapshot().ship == "anaconda"
    assert ctx.ship_loadout(46).ship == "python"
    assert ctx.ship_loadout(46).engineered_modules()[0].engineering.blueprint == "FSD_LongRange"
    assert ctx.ship_loadout(99).ship == "anaconda"


def test_capture_noop_without_a_store():
    ctx = EDContext()   # no store installed
    assert ctx.capture_loadout(_mini(46)) is False
    assert ctx.ship_loadout(46) is None
    assert ctx.remembered_ship_ids() == []
