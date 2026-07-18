"""Plotted-route tracking for proactive route callouts (N4).

Elite Dangerous writes the plotted galaxy-map route to `NavRoute.json` (in the journal
directory) — the full jump list, each entry carrying the star's `StarClass`. The journal
then emits a bare `NavRoute` event when it's (re)plotted and `NavRouteClear` when cancelled;
progress along it is followed via `FSDJump`.

`RouteTracker` is the PURE state machine over that: load a route, advance on each jump,
answer "how many jumps left / what's the destination / is the next star scoopable /
hazardous / which star am I arriving at vs. the one after". No I/O, no threads — the
capability wires it to the bus and the `read_navroute` file read. Kept pure so the cadence,
scoopable/hazard logic, and route-position look-ahead are unit-testable offline (DESIGN §9).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

# Scoopable main-sequence classes — the "KGBFOAM" mnemonic. A ship's fuel scoop refuels only
# at these; anything else (L/T/Y dwarfs, D white dwarfs, N neutron stars, black holes, etc.)
# can't be scooped, so arriving low on fuel there can strand you.
SCOOPABLE_CLASSES = frozenset("KGBFOAM")
# Classes whose first letter is scoopable but which AREN'T (Herbig Ae/Be starts with 'A').
_NON_SCOOPABLE_EXACT = frozenset({"AEBE"})

# Hazardous classes (#147) — exclusion zones with damaging jets, and (like all non-KGBFOAM
# stars) not scoopable. Matched on the leading class letter, same as `is_scoopable`: Neutron
# stars ('N', 'NS', ...) and White Dwarfs ('D', 'DA', 'DB', 'DC', 'DAB', ...). Black holes ('H')
# are a real hazard too but rare on plotted routes — out of scope here, trivial follow-on.
_HAZARD_NAMES = {"N": "neutron star", "D": "white dwarf"}


def is_scoopable(star_class: str | None) -> bool:
    """Whether a star of `star_class` can be fuel-scooped (KGBFOAM). Matches on the leading
    class letter, with a small exclusion for Herbig Ae/Be. Empty/unknown -> not scoopable."""
    c = str(star_class or "").strip().upper()
    if not c or c in _NON_SCOOPABLE_EXACT:
        return False
    return c[0] in SCOOPABLE_CLASSES


def is_hazardous_star(star_class: str | None) -> str | None:
    """Whether a star of `star_class` is a jets/exclusion-zone hazard worth a heads-up before
    the Commander drops in. Returns a human-readable name ("neutron star" / "white dwarf") for
    the callout to speak, or None if it isn't one of those two. Matches on the leading class
    letter, mirroring `is_scoopable`. Empty/unknown -> not hazardous."""
    c = str(star_class or "").strip().upper()
    if not c:
        return None
    return _HAZARD_NAMES.get(c[0])


# --- plotted-jump distance (issue #149) -----------------------------------------------
# `StartJump(Hyperspace)` carries the destination system + star class but NO distance, and
# `FSDJump`'s `JumpDist` only lands on ARRIVAL (too late for a mid-jump remark). NavRoute.json's
# entries DO carry each system's `StarPos` [x,y,z] galactic coordinates, so the distance of the
# jump you're mid-way through is computable at StartJump time from the route coords: the straight
# line between where you're jumping FROM and the destination. These helpers are PURE + offline-unit-
# testable so the "longer than normal?" threshold gate is exercised without a journal or a game.


def route_coords(navroute: dict | None) -> dict[str, tuple[float, float, float]]:
    """Map of normalized system name -> `StarPos` (x, y, z) from a parsed NavRoute.json body.
    Entries without a name or a well-formed 3-number position are skipped. Pure + fail-soft."""
    out: dict[str, tuple[float, float, float]] = {}
    if not isinstance(navroute, dict):
        return out
    for entry in (navroute.get("Route") or []):
        if not isinstance(entry, dict):
            continue
        system = entry.get("StarSystem")
        pos = entry.get("StarPos")
        if not system or not isinstance(pos, (list, tuple)) or len(pos) != 3:
            continue
        try:
            xyz = (float(pos[0]), float(pos[1]), float(pos[2]))
        except (TypeError, ValueError):
            continue
        out[str(system).strip().lower()] = xyz
    return out


def jump_distance(coords: dict[str, tuple[float, float, float]],
                  from_system: str | None, to_system: str | None) -> float | None:
    """Straight-line distance in light-years between two systems, looked up in a `route_coords`
    map. None when either system is absent from the map (off-route / unplotted) — the caller then
    stays silent rather than guessing. Pure."""
    a = coords.get(str(from_system or "").strip().lower())
    b = coords.get(str(to_system or "").strip().lower())
    if a is None or b is None:
        return None
    return math.dist(a, b)


def is_long_jump(distance_ly: float | None, threshold_ly: float) -> bool:
    """Whether a jump of `distance_ly` counts as 'longer than normal' — at or beyond the configured
    threshold. Fires past the threshold, silent below (or when distance is unknown). PURE — this is
    the offline-testable gate the long-jump flavor remark hangs off."""
    if not isinstance(distance_ly, (int, float)) or isinstance(distance_ly, bool):
        return False
    return distance_ly >= float(threshold_ly)


def read_navroute(journal_dir: str | Path) -> dict | None:
    """Read + parse NavRoute.json from the journal directory. None on any failure (absent,
    unreadable, half-written) — a missing route just means no callouts."""
    try:
        p = Path(journal_dir) / "NavRoute.json"
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class RouteStep:
    system: str
    star_class: str


class RouteTracker:
    """The plotted route + current position. `Route[0]` in NavRoute.json is the origin system
    (where you plotted from), so the number of jumps to make is `len(route) - 1` and progress
    is the index of the last system arrived at."""

    def __init__(self) -> None:
        self._route: list[RouteStep] = []
        self._idx: int = 0   # index of the current position (0 = at origin, not yet jumped)

    def load(self, navroute: dict) -> None:
        """Replace the route from a parsed NavRoute.json body (a replot just calls this
        again). Resets progress to the origin."""
        steps: list[RouteStep] = []
        for entry in (navroute.get("Route") or []):
            if not isinstance(entry, dict):
                continue
            system = entry.get("StarSystem")
            if system:
                steps.append(RouteStep(str(system), str(entry.get("StarClass") or "")))
        self._route = steps
        self._idx = 0

    def clear(self) -> None:
        self._route = []
        self._idx = 0

    @property
    def active(self) -> bool:
        """A usable route is at least an origin plus one jump."""
        return len(self._route) > 1

    @property
    def destination(self) -> str | None:
        return self._route[-1].system if self._route else None

    @property
    def jumps_made(self) -> int:
        return self._idx

    def jumps_remaining(self) -> int | None:
        """Jumps left to the destination, or None with no route."""
        if not self._route:
            return None
        return max(0, len(self._route) - 1 - self._idx)

    def on_jump(self, system: str) -> None:
        """Advance progress to `system` if it's on the route (searching forward first so a
        repeated system name doesn't rewind us). An off-route jump leaves progress alone — a
        replot (NavRoute) is expected to follow."""
        s = str(system or "").strip().lower()
        if not s:
            return
        for i in range(self._idx, len(self._route)):
            if self._route[i].system.lower() == s:
                self._idx = i
                return
        for i, step in enumerate(self._route):
            if step.system.lower() == s:
                self._idx = i
                return

    def step_for(self, system: str) -> RouteStep | None:
        """The route step for a named system (its star class), or None if not on the route."""
        s = str(system or "").strip().lower()
        for step in self._route:
            if step.system.lower() == s:
                return step
        return None

    def lookahead(self) -> tuple[RouteStep | None, RouteStep | None]:
        """The star the Commander is ARRIVING at this jump (the next hop not yet arrived at)
        and the one AFTER it, purely by route position (`_idx` + the step list) — never by
        matching a possibly-ambiguous event target name (#148: `FSDTarget` locks one hop ahead
        of the pilot's actual next arrival, so trusting its `Name` for "next star" is wrong).

        Returns `(None, None)` with no active route; `(arriving, None)` when arriving is the
        final destination (nothing after it); both populated otherwise.
        """
        if not self.active:
            return None, None
        arriving = self._route[self._idx + 1] if self._idx + 1 < len(self._route) else None
        following = self._route[self._idx + 2] if self._idx + 2 < len(self._route) else None
        return arriving, following
