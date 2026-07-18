"""Unit tests for the jump-range calculator + FSD reference data (issue #139; offline, DESIGN §9).

Locks the pure FSD equation against hand-computed figures (laden/unladen, with/without the Guardian
booster), the engineered-optimal-mass override read off the journal Modifiers, and the MaxJumpRange
calibration round-trip (recovering dry mass without per-module masses). All pure — no journal, no
network.
"""
from __future__ import annotations

import math

from covas.ed.loadout import Engineering, LoadoutSnapshot, Modifier, ShipModule
from covas.nav import fsd_data
from covas.nav.jump_range import (compute_jump_range, dry_mass_from_max_range, resolve_fsd,
                                  single_jump_range)


def _fsd(item: str = "int_hyperdrive_size6_class5", *, eng: Engineering | None = None) -> ShipModule:
    return ShipModule(slot="FrameShiftDrive", item=item, engineering=eng)


def _snap(*modules, **kw) -> LoadoutSnapshot:
    return LoadoutSnapshot(ship=kw.pop("ship", "anaconda"), modules=tuple(modules), **kw)


# ---- FSD reference data --------------------------------------------------------------------

def test_fsd_stats_lookup_and_rating_mapping():
    assert fsd_data.rating_for_class_digit(5) == "A"
    assert fsd_data.rating_for_class_digit(1) == "E"
    assert fsd_data.rating_for_class_digit(9) is None
    s = fsd_data.fsd_stats(6, "A")
    assert s is not None
    assert s.optimal_mass == 1800.0 and s.max_fuel == 8.0
    assert s.fuel_mul == 0.012 and s.fuel_power == 2.60
    assert fsd_data.fsd_stats(99, "A") is None  # out of range -> None, never a guess


def test_guardian_booster_bonus_table():
    assert fsd_data.guardian_booster_bonus(5) == 10.5
    assert fsd_data.guardian_booster_bonus(1) == 4.0
    assert fsd_data.guardian_booster_bonus(9) == 0.0


# ---- the pure equation ---------------------------------------------------------------------

def test_single_jump_range_matches_hand_computation():
    fit = resolve_fsd(_snap(_fsd()))
    assert fit is not None
    # base = 1800/900 * (8.0/0.012)**(1/2.6)
    expected = 1800.0 / 900.0 * (8.0 / 0.012) ** (1.0 / 2.6)
    assert math.isclose(single_jump_range(fit, 900.0), expected, rel_tol=1e-9)
    assert abs(single_jump_range(fit, 900.0) - 24.386) < 0.01  # independent literal


def test_heavier_ship_jumps_shorter():
    fit = resolve_fsd(_snap(_fsd()))
    assert single_jump_range(fit, 1200.0) < single_jump_range(fit, 600.0)


def test_guardian_booster_adds_flat_bonus():
    plain = resolve_fsd(_snap(_fsd()))
    boosted = resolve_fsd(_snap(_fsd(), ShipModule(slot="Slot01", item="int_guardianfsdbooster_size5")))
    assert boosted.guardian_bonus_ly == 10.5
    assert math.isclose(single_jump_range(boosted, 900.0),
                        single_jump_range(plain, 900.0) + 10.5, rel_tol=1e-9)


def test_engineered_optimal_mass_overrides_table():
    eng = Engineering(blueprint="FSD_LongRange", level=5,
                      modifiers=(Modifier(label="FSDOptimalMass", value=2600.0, original=1800.0),))
    fit = resolve_fsd(_snap(_fsd(eng=eng)))
    assert fit.optimal_mass == 2600.0 and fit.engineered is True
    # A bigger optimal mass -> a longer jump at the same total mass.
    plain = resolve_fsd(_snap(_fsd()))
    assert single_jump_range(fit, 900.0) > single_jump_range(plain, 900.0)


def test_deep_charge_max_fuel_override():
    eng = Engineering(blueprint="FSD_LongRange", level=1,
                      modifiers=(Modifier(label="MaxFuelPerJump", value=9.0, original=8.0),))
    fit = resolve_fsd(_snap(_fsd(eng=eng)))
    assert fit.max_fuel == 9.0


def test_no_fsd_resolves_none():
    assert resolve_fsd(_snap(ShipModule(slot="Cargo", item="int_cargorack_size6_class1"))) is None
    assert compute_jump_range(_snap(ShipModule(slot="Cargo", item="int_cargorack_size6_class1"))) is None


# ---- calibration from the game's MaxJumpRange ----------------------------------------------

def test_dry_mass_calibration_round_trips():
    fit = resolve_fsd(_snap(_fsd()))
    # The game's max range is computed at dry + max_fuel; inverting must recover the dry mass.
    r_game = single_jump_range(fit, 500.0 + fit.max_fuel)
    assert math.isclose(dry_mass_from_max_range(fit, r_game), 500.0, rel_tol=1e-6)


def test_dry_mass_calibration_unusable_below_booster_bonus():
    fit = resolve_fsd(_snap(_fsd(), ShipModule(slot="Slot01", item="int_guardianfsdbooster_size5")))
    # A max range at or below the flat booster bonus leaves no mass term -> None (never negative mass).
    assert dry_mass_from_max_range(fit, 10.0) is None


# ---- full compute (basis selection + calibrated flag) --------------------------------------

def test_reference_vs_laden_basis():
    fit = resolve_fsd(_snap(_fsd()))
    r_game = single_jump_range(fit, 500.0 + fit.max_fuel)
    snap = _snap(_fsd(), max_jump_range=r_game, fuel_capacity=32)
    ref = compute_jump_range(snap)
    laden = compute_jump_range(snap, cargo=100.0, fuel=32.0, fuel_capacity=32)
    assert ref.laden is False and ref.calibrated is True
    assert laden.laden is True
    assert laden.value < ref.value            # cargo shortens the jump
    assert "no cargo" in ref.basis and "100t of cargo" in laden.basis


def test_hull_only_fallback_flagged_rough_when_no_game_range():
    # No MaxJumpRange in the snapshot -> hull-only dry mass, flagged not-calibrated (rough).
    snap = _snap(_fsd(), fuel_capacity=32)  # max_jump_range absent
    res = compute_jump_range(snap, hull_mass=400.0)
    assert res is not None and res.calibrated is False and res.value > 0
