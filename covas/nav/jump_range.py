"""Pure jump-range calculator (issue #139) — the standard ED FSD equation over a remembered loadout.

Given a ship's `LoadoutSnapshot` (its FSD, that FSD's engineering, whether a Guardian booster is
fitted) plus a total mass, this computes a single-jump range in light-years. It is deliberately
OFFLINE + PURE + fail-soft: no network, no journal, every helper total, so `pytest` covers it for
free (DESIGN §9).

## The equation

    base_ly     = optimal_mass / total_mass * (max_fuel / fuel_mul) ** (1 / fuel_power)
    jump_range  = base_ly + guardian_booster_bonus   (when a Guardian FSD booster is fitted)

`optimal_mass` / `max_fuel` come from the FSD's ENGINEERING modifiers when present (the journal writes
the engineered values straight into `Loadout`, so an Increased-Range / Mass-Manager / Deep-Charge
drive needs no re-derivation), falling back to the un-engineered `fsd_data` table. `fuel_mul` /
`fuel_power` always come from `fsd_data` (the journal never restates them).

## Total mass — the honest part

`total_mass = dry_mass + fuel + cargo`, where `dry_mass` is hull + every fitted module (no fuel, no
cargo). **The loadout snapshot and the bundled ship specs do NOT carry per-module masses**, so we
cannot sum module masses directly. Instead we CALIBRATE `dry_mass` from the game's OWN
`MaxJumpRange` (written into every `Loadout`): the game computed that figure with the real module
masses, so inverting the equation at the game's max-range basis recovers an effective dry mass that
already bakes in every module. The one assumption is the fuel basis of `MaxJumpRange` — the game's
maximum single jump burns `max_fuel` tonnes, so we take that as its mass basis (`_MAXRANGE_FUEL_BASIS`
= max_fuel). This is the standard Coriolis "max range" convention.

Only when `MaxJumpRange` is absent (a hand-edited store, a pre-#135 row) do we fall back to
`dry_mass = hull_mass` alone — a ROUGH estimate that ignores module masses (so it over-states range);
the result is flagged `calibrated=False` so the capability can say so rather than quoting false
precision. We NEVER substitute fuel *capacity* for the missing hull mass: fuel capacity is ~5–30×
smaller than a real dry mass, so using it would inflate the quoted range by that same factor. When
neither `MaxJumpRange` nor a hull mass is available there is no honest dry-mass basis, so we report
"unknown" (return None) rather than invent one.

`compute_jump_range` returns a `JumpRangeResult` (value + the load basis it used + whether it was
calibrated), or None when the FSD can't be identified at all, or when no dry-mass basis is available
(no `MaxJumpRange` and no `hull_mass`) — then the caller reports "unknown", never a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..ed.loadout import LoadoutSnapshot, ShipModule
from . import fsd_data

# int_hyperdrive_size5_class5, and the SCO variant int_hyperdrive_overcharge_size5_class5.
_FSD_ITEM = re.compile(r"int_hyperdrive(?:_overcharge)?_size(\d+)_class(\d+)", re.IGNORECASE)
_BOOSTER_ITEM = re.compile(r"int_guardianfsdbooster_size(\d+)", re.IGNORECASE)

# Engineering modifier labels the journal writes for a drive's engineered stats (exact journal
# strings). We read these off the FSD module rather than re-deriving from blueprint + grade + quality.
_MOD_OPT_MASS = "fsdoptimalmass"
_MOD_MAX_FUEL = "maxfuelperjump"

# The fuel mass basis of the game's `MaxJumpRange`: the maximal single jump burns `max_fuel` tonnes,
# so the mass the drive "sees" for that quoted figure is dry + max_fuel (the Coriolis convention).
# Used only to back out dry mass from the game's own number.


@dataclass(frozen=True)
class FsdFit:
    """The fitted frame-shift drive resolved to the four equation constants, engineering applied.
    `optimal_mass` / `max_fuel` reflect the journal's engineered values when the drive is engineered;
    `guardian_bonus_ly` is the flat bonus from a fitted Guardian FSD booster (0 when none)."""
    size: int
    rating: str
    optimal_mass: float
    max_fuel: float
    fuel_mul: float
    fuel_power: float
    guardian_bonus_ly: float = 0.0
    engineered: bool = False


@dataclass(frozen=True)
class JumpRangeResult:
    """A computed single-jump range. `value` is ly; `basis` is a short spoken description of the
    load it assumes ('full tank, no cargo'); `laden` marks a figure that includes cargo; `calibrated`
    is False for the rough hull-only fallback (module masses unknown), so the caller can hedge."""
    value: float
    basis: str
    laden: bool
    calibrated: bool
    total_mass: float


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _find_fsd_module(snap: LoadoutSnapshot) -> ShipModule | None:
    """The fitted frame-shift drive (matched on the raw, non-localised Item symbol), or None."""
    for m in snap.modules or ():
        if _FSD_ITEM.search(m.item or ""):
            return m
    return None


def _engineered_value(module: ShipModule, label: str) -> float | None:
    """The engineered value of a modifier by (case-insensitive) journal Label, or None when the
    module isn't engineered / doesn't carry that modifier."""
    eng = module.engineering
    if eng is None:
        return None
    for mod in eng.modifiers or ():
        if str(mod.label or "").strip().lower() == label and mod.value is not None:
            return float(mod.value)
    return None


def guardian_booster_bonus(snap: LoadoutSnapshot) -> float:
    """The Guardian FSD booster jump bonus (ly) for the largest booster fitted, else 0.0. (Only one
    booster benefits a jump; if two were somehow fitted the larger wins — a defensive max.)"""
    best = 0.0
    for m in snap.modules or ():
        hit = _BOOSTER_ITEM.search(m.item or "")
        if hit:
            best = max(best, fsd_data.guardian_booster_bonus(int(hit.group(1))))
    return best


def resolve_fsd(snap: LoadoutSnapshot) -> FsdFit | None:
    """Resolve the fitted FSD to its equation constants (engineering applied), or None when no FSD is
    fitted or its class/rating isn't in the reference table (an exotic drive with no baseline — the
    caller reports unknown rather than guessing). A drive we DO recognise but whose stats are wholly
    engineered still resolves: the engineered optimal_mass / max_fuel override the table values."""
    module = _find_fsd_module(snap)
    if module is None:
        return None
    hit = _FSD_ITEM.search(module.item)
    if hit is None:
        return None
    size = int(hit.group(1))
    rating = fsd_data.rating_for_class_digit(int(hit.group(2)))
    if rating is None:
        return None
    stats = fsd_data.fsd_stats(size, rating)
    if stats is None:
        return None
    opt = _engineered_value(module, _MOD_OPT_MASS)
    maxf = _engineered_value(module, _MOD_MAX_FUEL)
    return FsdFit(
        size=size,
        rating=rating,
        optimal_mass=opt if opt is not None else stats.optimal_mass,
        max_fuel=maxf if maxf is not None else stats.max_fuel,
        fuel_mul=stats.fuel_mul,
        fuel_power=stats.fuel_power,
        guardian_bonus_ly=guardian_booster_bonus(snap),
        engineered=module.engineered,
    )


def single_jump_range(fit: FsdFit, total_mass: float) -> float:
    """The pure FSD equation: a single-jump range (ly) for a drive `fit` at `total_mass` tonnes,
    Guardian booster bonus included. Total mass must be positive."""
    if total_mass <= 0 or fit.fuel_power <= 0 or fit.fuel_mul <= 0:
        return 0.0
    base = fit.optimal_mass / total_mass * (fit.max_fuel / fit.fuel_mul) ** (1.0 / fit.fuel_power)
    return base + fit.guardian_bonus_ly


def dry_mass_from_max_range(fit: FsdFit, max_jump_range: float) -> float | None:
    """Invert the equation at the game's own `MaxJumpRange` to recover the ship's DRY mass (hull +
    every module, no fuel/cargo). Returns None when the figure is unusable (<= the Guardian bonus, so
    the mass term would be non-positive). The game's max jump burns `max_fuel` tonnes, so the mass it
    used was dry + max_fuel — we solve for that then subtract the fuel back out."""
    base = float(max_jump_range) - fit.guardian_bonus_ly
    if base <= 0:
        return None
    # base = optimal_mass / mass * (max_fuel/fuel_mul)**(1/fuel_power)  ->  solve for mass, then dry.
    k = fit.optimal_mass * (fit.max_fuel / fit.fuel_mul) ** (1.0 / fit.fuel_power)
    mass_at_max = k / base
    dry = mass_at_max - fit.max_fuel
    return dry if dry > 0 else None


def _basis_text(fuel: float, cargo: float, fuel_capacity: float | None, laden: bool) -> str:
    """A short spoken description of the load the figure assumes."""
    if laden:
        cargo_part = f"{cargo:g}t of cargo" if cargo > 0 else "no cargo"
        return f"laden — {fuel:g}t fuel, {cargo_part}"
    tank = "a full tank" if fuel_capacity else f"{fuel:g}t fuel"
    return f"{tank}, no cargo"


def compute_jump_range(
    snap: LoadoutSnapshot,
    *,
    hull_mass: float | None = None,
    cargo: float | None = None,
    fuel: float | None = None,
    fuel_capacity: float | None = None,
) -> JumpRangeResult | None:
    """Compute a single-jump range for a remembered/live ship.

    Load basis:
      * `cargo`/`fuel` given (the CURRENT ship, live) -> a LADEN figure at that exact load.
      * both omitted (any OTHER ship, no live telemetry) -> the REFERENCE figure: full main tank,
        empty cargo — the usual quoted "jump range" basis, consistent across the fleet for ranking.

    `hull_mass` (from the bundled spec) is the fallback dry mass only when the game's `MaxJumpRange`
    is absent from the snapshot; normally dry mass is CALIBRATED from that figure (so real module
    masses are accounted for without per-module data). Returns None when no usable FSD is fitted, or
    when there is no dry-mass basis at all (no `MaxJumpRange` AND no `hull_mass`) — reporting
    "unknown" beats quoting a fabricated, inflated range.
    """
    fit = resolve_fsd(snap)
    if fit is None:
        return None

    cap = _num(fuel_capacity)
    if cap is None:
        cap = _num(getattr(snap, "fuel_capacity", None))

    laden = cargo is not None or fuel is not None
    cargo_t = max(0.0, _num(cargo) or 0.0)
    if laden:
        fuel_t = _num(fuel)
        if fuel_t is None:
            fuel_t = cap if cap is not None else fit.max_fuel
    else:
        # Reference load: a full tank (the quoted basis), or at least one jump's fuel.
        fuel_t = cap if cap is not None else fit.max_fuel
    fuel_t = max(0.0, fuel_t)

    # Dry mass: prefer calibrating from the game's own MaxJumpRange (captures module masses); fall
    # back to hull mass ALONE when that figure is missing (rough — ignores module masses, flagged so
    # the caller can hedge).
    calibrated = True
    dry = None
    game_max = _num(getattr(snap, "max_jump_range", None))
    if game_max and game_max > 0:
        dry = dry_mass_from_max_range(fit, game_max)
    if dry is None:
        # No MaxJumpRange to calibrate from. The only honest fallback is the hull mass alone. Fuel
        # *capacity* is NOT a dry-mass proxy — it is ~5–30× too small, and using it here inflated the
        # quoted range by the same factor (issue #164). Without a hull mass we have no basis for the
        # total mass, so report "unknown" (None) rather than invent a figure.
        hull = _num(hull_mass)
        if hull is None or hull <= 0:
            return None
        calibrated = False
        dry = hull

    total_mass = dry + fuel_t + cargo_t
    value = single_jump_range(fit, total_mass)
    return JumpRangeResult(
        value=value,
        basis=_basis_text(fuel_t, cargo_t, cap, laden),
        laden=laden,
        calibrated=calibrated,
        total_mass=total_mass,
    )
