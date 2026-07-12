"""Eligibility engine (C3) — live Elite Dangerous state -> the token set the cue registry queries.

Cues declare `eligible_states` from a shared vocabulary (below); this module emits that same
vocabulary from real game state, so a cue is eligible exactly when one of its states is live.
Two token sources, both pure + fixture-testable:

  * Status.json `Flags` (docked, supercruise, hardpoints, danger, interdiction, low fuel,
    scooping/near-star, jump tunnel, SRV/fighter…) — recomputed from the latest snapshot;
  * the journal's arrival events (FSDJump/Location/CarrierJump) `Population` field — STICKY
    (you stay in a populated system until the next jump), giving populated / unpopulated /
    deep-space.

`EligibilityEngine` folds both (plus a derived fuel state) into `states()`. It never blocks or
speaks — it only answers "what's true right now" for the driver.
"""
from __future__ import annotations

from typing import Iterable

from ..ed.status import decode_flags

# ---- the canonical eligibility-state vocabulary -------------------------------------------
# Cue authors declare eligible_states from these; the engine emits them. One list = the shared
# contract that keeps cues and the driver from drifting.
DOCKED = "docked"
LANDED = "landed"                # on a planet surface
SUPERCRUISE = "supercruise"
NORMAL_SPACE = "normal_space"    # in the ship, out in space (not docked/landed/jump/supercruise)
HYPERSPACE = "hyperspace"        # the witchspace jump tunnel (Status FsdJump flag)
HARDPOINTS = "hardpoints"
IN_DANGER = "in_danger"
INTERDICTED = "interdicted"
OVERHEATING = "overheating"
SCOOPING_FUEL = "scooping_fuel"
NEAR_STAR = "near_star"          # close enough to scoop (implied by ScoopingFuel)
LOW_FUEL = "low_fuel"            # < 25%
FUEL_CRITICAL = "fuel_critical"  # < 10%
IN_SHIP = "in_ship"
IN_SRV = "in_srv"
IN_FIGHTER = "in_fighter"
POPULATED = "populated"          # arrived in an inhabited system (Population > 0)
UNPOPULATED = "unpopulated"      # arrived in an uninhabited system (Population == 0)
DEEP_SPACE = "deep_space"        # coarse proxy for "out in the black" = currently unpopulated

STATES: frozenset[str] = frozenset({
    DOCKED, LANDED, SUPERCRUISE, NORMAL_SPACE, HYPERSPACE, HARDPOINTS, IN_DANGER, INTERDICTED,
    OVERHEATING, SCOOPING_FUEL, NEAR_STAR, LOW_FUEL, FUEL_CRITICAL, IN_SHIP, IN_SRV, IN_FIGHTER,
    POPULATED, UNPOPULATED, DEEP_SPACE,
})

_LOCATION_EVENTS = {"FSDJump", "Location", "CarrierJump"}


def flag_states(flags: int) -> set[str]:
    """Eligibility tokens decoded from a Status.json Flags bitfield. Pure."""
    d = decode_flags(flags)
    out: set[str] = set()
    if d["Docked"]:
        out.add(DOCKED)
    if d["Landed"]:
        out.add(LANDED)
    if d["Supercruise"]:
        out.add(SUPERCRUISE)
    if d["FsdJump"]:
        out.add(HYPERSPACE)
    if d["HardpointsDeployed"]:
        out.add(HARDPOINTS)
    if d["IsInDanger"]:
        out.add(IN_DANGER)
    if d["BeingInterdicted"]:
        out.add(INTERDICTED)
    if d["Overheating"]:
        out.add(OVERHEATING)
    if d["ScoopingFuel"]:
        out.update({SCOOPING_FUEL, NEAR_STAR})
    if d["LowFuel"]:
        out.add(LOW_FUEL)
    if d["InMainShip"]:
        out.add(IN_SHIP)
    if d["InSRV"]:
        out.add(IN_SRV)
    if d["InFighter"]:
        out.add(IN_FIGHTER)
    # "Normal space" = flying the ship out in the open — not docked/landed, not in the jump
    # tunnel, not in supercruise. The bread-and-butter state most chatter lives in.
    if d["InMainShip"] and not (d["Docked"] or d["Landed"] or d["Supercruise"] or d["FsdJump"]):
        out.add(NORMAL_SPACE)
    return out


def fuel_states(fuel_pct: float | None) -> set[str]:
    """Fuel-derived tokens from the main-tank percentage (EDContext derives it from journal
    capacity + Status fuel). Pure; empty when fuel is unknown."""
    out: set[str] = set()
    if isinstance(fuel_pct, (int, float)) and not isinstance(fuel_pct, bool):
        if fuel_pct < 25.0:
            out.add(LOW_FUEL)
        if fuel_pct < 10.0:
            out.add(FUEL_CRITICAL)
    return out


def journal_states(event: dict) -> set[str] | None:
    """Sticky population tokens from a journal arrival event, or None when the event carries no
    population info (so the current sticky state is left untouched). Pure."""
    if not isinstance(event, dict):
        return None
    if event.get("event") not in _LOCATION_EVENTS or "Population" not in event:
        return None
    pop = event.get("Population")
    out: set[str] = set()
    if isinstance(pop, (int, float)) and not isinstance(pop, bool):
        if pop > 0:
            out.add(POPULATED)
        else:
            out.update({UNPOPULATED, DEEP_SPACE})
    return out


class EligibilityEngine:
    """Accumulates the latest game state and answers `states()`. Flag-derived tokens are
    recomputed from the last Status snapshot (transient); population tokens are STICKY until the
    next arrival event. Not thread-guarded — the driver calls it from one event-pump thread."""

    def __init__(self) -> None:
        self._flags: int | None = None
        self._journal: set[str] = set()

    def note_flags(self, flags: int) -> None:
        if isinstance(flags, int) and not isinstance(flags, bool):
            self._flags = int(flags)

    def note_status(self, status: dict) -> None:
        """Fold a Status.json snapshot (reads its Flags)."""
        if isinstance(status, dict):
            self.note_flags(status.get("Flags"))

    def note_journal(self, event: dict) -> None:
        toks = journal_states(event)
        if toks is not None:
            self._journal = set(toks)

    def note_event(self, event: dict) -> None:
        """Fold a bus `ed_event`, whichever shape: a status transition carries the current
        `flags` int; a journal arrival carries `Population`. Handles both."""
        if not isinstance(event, dict):
            return
        f = event.get("flags")
        if isinstance(f, int) and not isinstance(f, bool):
            self._flags = f
        self.note_journal(event)

    def states(self, *, fuel_pct: float | None = None) -> frozenset[str]:
        """The current eligibility set: flag tokens + sticky population tokens + fuel tokens."""
        out: set[str] = set(self._journal)
        if self._flags is not None:
            out |= flag_states(self._flags)
        out |= fuel_states(fuel_pct)
        return frozenset(out)


def unknown_states(tokens: Iterable[str]) -> set[str]:
    """Any tokens NOT in the canonical vocabulary — a lint helper so a test can assert a cue's
    eligible_states are real (a typo'd state would otherwise just never fire, silently)."""
    return {str(t) for t in tokens} - STATES
