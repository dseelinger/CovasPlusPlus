"""Frame-Shift-Drive reference constants (issue #139) — the numbers the jump-range calculator needs.

POINT-IN-TIME reference, community/EDCD-sourced (the same coriolis-data lineage `ship_spec_data.py`
bakes from). ED's FSD stats are stable across balance passes, but treat this as a snapshot: if a
future FSD rebalance lands, regenerate against EDCD/coriolis-data rather than trusting these forever.

Per FSD class (size 2-8) and rating (A-E) we keep the four constants the single-jump equation uses:

    optimal_mass   (t)  — the mass at which the drive hits its rated range
    max_fuel       (t)  — the most fuel one jump can burn (caps the jump distance)
    fuel_mul            — the drive's linear fuel constant (by rating)
    fuel_power         — the drive's power constant (by class/size)

The equation (see `jump_range.py`):

    distance_ly = optimal_mass / total_mass * (max_fuel / fuel_mul) ** (1 / fuel_power)

Engineering (Increased Range, Mass Manager, Deep Charge) changes `optimal_mass` / `max_fuel`; the
journal writes the ENGINEERED values straight into the `Loadout` Modifiers, so the calculator prefers
those over this table — this table supplies the un-engineered baseline and the two rating/size
constants (`fuel_mul`, `fuel_power`) the journal does NOT restate.

Also here: the Guardian FSD Booster's FLAT jump bonus per size (added to the computed range when the
booster module is fitted). These are the published in-game bonuses.

Everything is pure data + tiny pure lookups — no I/O, offline, unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass

# Rating letter <-> the journal's class digit (int_hyperdrive_sizeN_class{1..5}). Class 5 = A (best).
_CLASS_DIGIT_TO_RATING = {5: "A", 4: "B", 3: "C", 2: "D", 1: "E"}

# fuel_power is a function of the drive's SIZE (class); fuel_mul a function of its RATING. Kept as the
# two source axes (rather than restating them on every row) so the table reads the way the data is
# actually structured and a transcription slip is obvious.
_FUEL_POWER_BY_SIZE = {2: 2.00, 3: 2.15, 4: 2.30, 5: 2.45, 6: 2.60, 7: 2.75, 8: 2.90}
_FUEL_MUL_BY_RATING = {"A": 0.012, "B": 0.010, "C": 0.008, "D": 0.010, "E": 0.011}

# optimal_mass (t) per (size, rating). The rated mass — heavier ships jump shorter.
_OPT_MASS = {
    2: {"E": 48.0,   "D": 54.0,   "C": 60.0,   "B": 75.0,   "A": 90.0},
    3: {"E": 80.0,   "D": 90.0,   "C": 100.0,  "B": 125.0,  "A": 150.0},
    4: {"E": 280.0,  "D": 315.0,  "C": 350.0,  "B": 438.0,  "A": 525.0},
    5: {"E": 560.0,  "D": 630.0,  "C": 700.0,  "B": 875.0,  "A": 1050.0},
    6: {"E": 960.0,  "D": 1080.0, "C": 1200.0, "B": 1500.0, "A": 1800.0},
    7: {"E": 1440.0, "D": 1620.0, "C": 1800.0, "B": 2250.0, "A": 2700.0},
    8: {"E": 2160.0, "D": 2430.0, "C": 2700.0, "B": 3375.0, "A": 4050.0},
}

# max_fuel (t) per jump per (size, rating).
_MAX_FUEL = {
    2: {"E": 0.60,  "D": 0.60,  "C": 0.60,  "B": 0.80,  "A": 0.90},
    3: {"E": 1.20,  "D": 1.20,  "C": 1.20,  "B": 1.50,  "A": 1.80},
    4: {"E": 2.00,  "D": 2.00,  "C": 2.00,  "B": 2.50,  "A": 3.00},
    5: {"E": 3.30,  "D": 3.30,  "C": 3.30,  "B": 4.10,  "A": 5.00},
    6: {"E": 5.30,  "D": 5.30,  "C": 5.30,  "B": 6.60,  "A": 8.00},
    7: {"E": 8.50,  "D": 8.50,  "C": 8.50,  "B": 10.60, "A": 12.80},
    8: {"E": 12.60, "D": 12.60, "C": 12.60, "B": 15.80, "A": 16.00},
}

# Guardian FSD Booster (int_guardianfsdbooster_size{1..5}) — a FLAT jump-range bonus in ly, added
# after the mass equation. Published in-game values.
_GUARDIAN_BOOSTER_LY = {1: 4.00, 2: 6.00, 3: 7.75, 4: 9.25, 5: 10.50}


@dataclass(frozen=True)
class FsdStats:
    """The four constants for one drive class+rating. `optimal_mass`/`max_fuel` are the un-engineered
    baseline (the journal overrides them with engineered values); `fuel_mul`/`fuel_power` are the
    rating/size constants the journal never restates, so they always come from here."""
    size: int
    rating: str
    optimal_mass: float
    max_fuel: float
    fuel_mul: float
    fuel_power: float


def rating_for_class_digit(digit: int) -> str | None:
    """The rating letter (A-E) for a journal class digit (5..1), or None for an out-of-range value."""
    return _CLASS_DIGIT_TO_RATING.get(int(digit)) if isinstance(digit, (int, float)) else None


def fsd_stats(size: int, rating: str) -> FsdStats | None:
    """The reference `FsdStats` for a drive of `size` (2-8) and `rating` ('A'-'E'), or None when the
    combination isn't in the table (an unknown/exotic drive — the caller then defers to the journal's
    own figures or reports the range as unknown rather than guessing)."""
    r = str(rating or "").strip().upper()
    opt = _OPT_MASS.get(size, {}).get(r)
    mf = _MAX_FUEL.get(size, {}).get(r)
    fp = _FUEL_POWER_BY_SIZE.get(size)
    fm = _FUEL_MUL_BY_RATING.get(r)
    if opt is None or mf is None or fp is None or fm is None:
        return None
    return FsdStats(size=size, rating=r, optimal_mass=opt, max_fuel=mf, fuel_mul=fm, fuel_power=fp)


def guardian_booster_bonus(size: int) -> float:
    """The flat jump-range bonus (ly) a Guardian FSD Booster of `size` (1-5) adds, or 0.0 for an
    unknown size."""
    return float(_GUARDIAN_BOOSTER_LY.get(int(size), 0.0)) if isinstance(size, (int, float)) else 0.0
