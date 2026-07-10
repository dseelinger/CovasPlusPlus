"""Route callouts — proactive heads-ups while flying a plotted route (N4).

When Elite Dangerous monitoring is on, the journal watcher publishes `ed_event`s on the bus.
This capability reacts to the route-relevant ones and volunteers SHORT, DETERMINISTIC spoken
lines — is the next star scoopable, how many jumps remain, arrival at the destination —
WITHOUT a push-to-talk press:

  * `NavRoute` / `NavRouteClear` -> (re)load or drop the plotted route (from NavRoute.json).
  * `FSDTarget` -> the next system is locked in; announce whether its star is scoopable.
  * `FSDJump`   -> advance progress; every Nth jump announce jumps remaining, and announce
                   arrival at the final system.

Unlike generic proactive callouts these are FACTUAL, so they're spoken verbatim (no LLM cost
or embellishment) through the app's proactive line path — which means they inherit the same
guarantees: they only speak when Idle (never over the Commander), a PTT press cancels one, and
they honour the proactive mute. Everything is injected so the default test run is offline.

Off by default ([route].enabled = false).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..ed.route import RouteTracker, is_scoopable


@dataclass(frozen=True)
class RouteConfig:
    """Immutable snapshot of `[route]`. Off by default; the capability isn't registered unless
    `enabled`."""
    enabled: bool = False
    every_n: int = 5                 # announce jumps-remaining every Nth jump
    callout_scoopable: bool = True
    callout_jumps_remaining: bool = True
    callout_arrival: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> "RouteConfig":
        r = cfg.get("route", {}) or {}
        d = cls()
        return cls(
            enabled=bool(r.get("enabled", False)),
            every_n=max(1, int(r.get("every_n", d.every_n))),
            callout_scoopable=bool(r.get("callout_scoopable", True)),
            callout_jumps_remaining=bool(r.get("callout_jumps_remaining", True)),
            callout_arrival=bool(r.get("callout_arrival", True)),
        )


class RouteCalloutCapability:
    """Turns route bus events into deterministic proactive callouts.

    Injected seams (so the default test run is offline):
      * `speak_line(text) -> bool` — speak a fixed line through the app's proactive path;
        returns True only if it actually started (Idle, not mid-user-turn).
      * `load_navroute() -> dict | None` — read + parse the current NavRoute.json.
      * `is_muted() -> bool` — honour the shared proactive mute ('stop the callouts').
    """

    def __init__(
        self,
        config: RouteConfig,
        *,
        speak_line: Callable[[str], bool],
        load_navroute: Callable[[], Optional[dict]],
        is_muted: Optional[Callable[[], bool]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config
        self._speak = speak_line
        self._load_navroute = load_navroute
        self._is_muted = is_muted or (lambda: False)
        self._log = log
        self._tracker = RouteTracker()
        self._last_target: Optional[str] = None   # system of the last scoopable callout

    # -- capability interface ---------------------------------------------------------
    # No LLM tools and no help metadata: this capability is purely event-driven (ambient),
    # like the proactive callouts. It advertises nothing to the model.
    def tools(self) -> list[dict]:
        return []

    def prime(self) -> None:
        """Load an already-plotted route at startup (the NavRoute event that plotted it fired
        before we subscribed, so read the file directly once)."""
        data = self._load_navroute()
        if data:
            self._tracker.load(data)
            self._logline(f"primed: {self._tracker.jumps_remaining()} jumps to "
                          f"{self._tracker.destination}")

    def on_event(self, event: dict) -> None:
        """Bus hook (event-pump thread). Must never raise — a bad event must not take the
        pump down."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            name = event.get("event")
            if name == "NavRoute":
                self._on_navroute()
            elif name == "NavRouteClear":
                self._tracker.clear()
                self._last_target = None
            elif name == "FSDTarget":
                self._on_target(event)
            elif name == "FSDJump":
                self._on_jump(event)
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass

    # -- handlers ---------------------------------------------------------------------
    def _on_navroute(self) -> None:
        data = self._load_navroute()
        if not data:
            return
        self._tracker.load(data)
        self._last_target = None
        self._logline(f"route (re)plotted: {self._tracker.jumps_remaining()} jumps to "
                      f"{self._tracker.destination}")

    def _on_target(self, event: dict) -> None:
        if not self._cfg.callout_scoopable or not self._tracker.active:
            return
        target = event.get("Name")                       # FSDTarget uses 'Name', not 'StarSystem'
        if not target or target == self._last_target:     # already announced this target
            return
        # Prefer the class from the plotted route (NavRoute.json); fall back to the event's.
        step = self._tracker.step_for(target)
        star_class = step.star_class if step else event.get("StarClass")
        if not star_class:
            return
        self._last_target = target
        if is_scoopable(star_class):
            self._emit("Next star's scoopable.")
        else:
            self._emit("Heads up — the next star isn't scoopable. Top off your fuel if you're low.")

    def _on_jump(self, event: dict) -> None:
        system = event.get("StarSystem")
        if not system or not self._tracker.active:
            return
        self._tracker.on_jump(system)
        remaining = self._tracker.jumps_remaining()
        if remaining is None:
            return

        if remaining == 0:
            dest = self._tracker.destination
            if self._cfg.callout_arrival:
                self._emit(f"Arrived at {dest}. Route complete.")
            self._tracker.clear()
            self._last_target = None
            return

        made = self._tracker.jumps_made
        if (self._cfg.callout_jumps_remaining and made > 0
                and made % self._cfg.every_n == 0):
            unit = "jump" if remaining == 1 else "jumps"
            self._emit(f"{remaining} {unit} remaining to {self._tracker.destination}.")

    # -- speak (honours mute + the never-interrupt idle claim) ------------------------
    def _emit(self, line: str) -> None:
        if self._is_muted():
            self._logline(f"muted: {line}")
            return
        started = self._speak(line)
        self._logline(f"{'spoke' if started else 'skipped (busy)'}: {line}")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
