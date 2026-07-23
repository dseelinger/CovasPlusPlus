"""Unit tests for stored-ships/modules parsing + EDContext/journal wiring (issue #67; offline).

Parses the recorded StoredShips / StoredModules fixtures and locks the structured shapes:
here vs. remote classification, the game's own transfer price/cost/time carried verbatim,
in-transit handling, module-symbol normalization, and the apply_journal_event wiring that
stashes each snapshot on EDContext.
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.ed.context import EDContext
from covas.ed.journal import apply_journal_event
from covas.ed.stored import (
    StoredModulesSnapshot,
    StoredShipsSnapshot,
    parse_stored_modules,
    parse_stored_ships,
)

_FIX = Path(__file__).parent / "fixtures"


def _ships() -> StoredShipsSnapshot:
    return parse_stored_ships(json.loads((_FIX / "journal_stored_ships.json").read_text("utf-8")))


def _modules() -> StoredModulesSnapshot:
    return parse_stored_modules(
        json.loads((_FIX / "journal_stored_modules.json").read_text("utf-8")))


# --- StoredShips ------------------------------------------------------------------------------

def test_stored_ships_snapshot_origin():
    snap = _ships()
    assert snap.station == "Jameson Memorial"
    assert snap.system == "Shinrarta Dezhra"
    assert snap.market_id == 128666762
    assert len(snap.ships) == 6


def test_ships_here_have_no_transfer():
    here = _ships().here_ships()
    assert {s.ship_type for s in here} == {"cutter", "diamondbackxl"}
    for s in here:
        assert s.here and s.transfer_price is None and s.transfer_time is None


def test_ships_remote_carry_journal_transfer_figures():
    remote = {s.ship_type: s for s in _ships().remote()}
    corvette = remote["federation_corvette"]
    assert corvette.here is False
    assert corvette.system == "Sol"
    assert corvette.transfer_price == 12250000   # verbatim from the journal
    assert corvette.transfer_time == 1560
    assert corvette.name == "Warhammer"


def test_ship_in_transit_flagged():
    krait = next(s for s in _ships().ships if s.ship_type == "krait_mkii")
    assert krait.in_transit is True
    assert krait.here is False


def test_ship_display_prefers_localised_then_curated_symbol():
    ships = {s.ship_type: s for s in _ships().ships}
    assert ships["cutter"].display == "Imperial Cutter"
    # Curated fallback when no localised string (parse a bare entry).
    bare = parse_stored_ships({"ShipsRemote": [{"ShipType": "type9", "StarSystem": "Sol"}]})
    assert bare.ships[0].display == "Type-9 Heavy"


# --- StoredModules ----------------------------------------------------------------------------

def test_stored_modules_here_vs_remote():
    snap = _modules()
    here = [m for m in snap.modules if m.here]
    remote = [m for m in snap.modules if not m.here and not m.in_transit]
    transit = [m for m in snap.modules if m.in_transit]
    assert len(here) == 2           # shield gen + fuel scoop at the current station
    assert len(remote) == 3         # power plant in Sol + two multi-cannons in LHS 3447
    assert len(transit) == 1


def test_module_symbol_normalized_for_naming():
    snap = _modules()
    names = {m.name for m in snap.modules}
    assert "int_shieldgenerator_size6_class5_strong" in names   # $...; stripped
    assert "hpt_multicannon_gimbal_medium" in names             # _name; stripped


def test_module_remote_transfer_and_engineering():
    snap = _modules()
    pp = next(m for m in snap.modules if "powerplant" in m.name)
    assert pp.here is False
    assert pp.system == "Sol"
    assert pp.transfer_cost == 540000       # verbatim from the journal
    assert pp.transfer_time == 1560
    assert pp.engineer_modifications == "PowerPlant_Armoured"
    assert pp.level == 5


# --- journal wiring ---------------------------------------------------------------------------

def test_apply_journal_event_stashes_stored_ships():
    ctx = EDContext()
    event = json.loads((_FIX / "journal_stored_ships.json").read_text("utf-8"))
    patch = apply_journal_event(ctx, event)
    assert patch == {}                       # no "current context" patch from StoredShips
    snap = ctx.stored_ships_snapshot()
    assert isinstance(snap, StoredShipsSnapshot) and len(snap.ships) == 6


def test_apply_journal_event_stashes_stored_modules():
    ctx = EDContext()
    event = json.loads((_FIX / "journal_stored_modules.json").read_text("utf-8"))
    apply_journal_event(ctx, event)
    snap = ctx.stored_modules_snapshot()
    assert isinstance(snap, StoredModulesSnapshot) and len(snap.modules) == 6


def test_context_defaults_none_until_seen():
    ctx = EDContext()
    assert ctx.stored_ships_snapshot() is None
    assert ctx.stored_modules_snapshot() is None
