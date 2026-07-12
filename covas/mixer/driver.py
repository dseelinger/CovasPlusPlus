"""Cue driver (C3) — ties live game state to the mixer through the registry + governor.

On a state change (a bus `ed_event`) or an explicit tick, the driver:
  1. folds the event into the EligibilityEngine,
  2. asks the CueRegistry which cues are eligible for the current state,
  3. lets the CueGovernor pick ONE within the anti-over-talking budget (deterministic rotation),
  4. hands it to a `play` callback that routes it to the mixer/bus.

The `play` callback is the app's integration point (like ProactiveCapability's `speak`): it
renders the chosen cue onto its bus (a chatter phrasing, an SFX sample, a music context — C5–C8
provide those) and returns True only if it actually started, so the governor arms the cooldown
only on a real play. The driver builds NO audio itself and is off by default (the governor's
`enabled` gates everything). It never raises into the event pump.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .cues import Cue, CueRegistry
from .eligibility import EligibilityEngine
from .governor import CueGovernor


class CueDriver:
    def __init__(
        self,
        registry: CueRegistry,
        engine: EligibilityEngine,
        governor: CueGovernor,
        play: Callable[[Cue], bool],
        *,
        context=None,  # noqa: ANN001 — optional EDContext, read for the live fuel percentage
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.registry = registry
        self.engine = engine
        self.governor = governor
        self._play = play
        self._context = context
        self._clock = clock
        self._log = log

    def on_event(self, event: dict) -> None:
        """Bus hook (dispatched by the app's event pump). Fold the event, then tick. Must never
        raise — it shares the event-pump thread with everything else."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            self.engine.note_event(event)
            self.tick()
        except Exception:  # noqa: BLE001 — a bad event must not take down the pump
            pass

    def _fuel_pct(self) -> float | None:
        if self._context is None:
            return None
        getter = getattr(self._context, "fuel_pct", None)
        return getter() if callable(getter) else None

    def tick(self) -> Cue | None:
        """Compute the live eligibility set, pick an allowed cue, and play it. Returns the cue
        that played (or None). Safe to call on a timer as well as on state changes."""
        now = self._clock()
        states = self.engine.states(fuel_pct=self._fuel_pct())
        eligible = self.registry.eligible(states)
        cue = self.governor.select(eligible, now)
        if cue is None:
            return None
        if bool(self._play(cue)):
            self.governor.mark_fired(cue, now)
            if self._log is not None:
                self._log(f"cue {cue.name} -> {cue.bus}")
            return cue
        return None
