"""Plotted-route tracking for proactive route callouts (N4).

Elite Dangerous writes the plotted galaxy-map route to `NavRoute.json` (in the journal
directory) — the full jump list, each entry carrying the star's `StarClass`. The journal
then emits a bare `NavRoute` event when it's (re)plotted and `NavRouteClear` when cancelled;
progress along it is followed via `FSDJump`.

`RouteTracker` is the PURE state machine over that: load a route, advance on each jump,
answer "how many jumps left / what's the destination / is the next star scoopable". No I/O,
no threads — the capability wires it to the bus and the `read_navroute` file read. Kept pure
so the cadence and scoopable logic are unit-testable offline (DESIGN §9).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Scoopable main-sequence classes — the "KGBFOAM" mnemonic. A ship's fuel scoop refuels only
# at these; anything else (L/T/Y dwarfs, D white dwarfs, N neutron stars, black holes, etc.)
# can't be scooped, so arriving low on fuel there can strand you.
SCOOPABLE_CLASSES = frozenset("KGBFOAM")
# Classes whose first letter is scoopable but which AREN'T (Herbig Ae/Be starts with 'A').
_NON_SCOOPABLE_EXACT = frozenset({"AEBE"})


def is_scoopable(star_class: str | None) -> bool:
    """Whether a star of `star_class` can be fuel-scooped (KGBFOAM). Matches on the leading
    class letter, with a small exclusion for Herbig Ae/Be. Empty/unknown -> not scoopable."""
    c = str(star_class or "").strip().upper()
    if not c or c in _NON_SCOOPABLE_EXACT:
        return False
    return c[0] in SCOOPABLE_CLASSES


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
