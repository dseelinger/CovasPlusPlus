"""Route callouts — proactive heads-ups while flying a plotted route (N4, #147, #148).

When Elite Dangerous monitoring is on, the journal watcher publishes `ed_event`s on the bus.
This capability reacts to the route-relevant ones and volunteers SHORT, DETERMINISTIC spoken
lines — is the arriving star scoopable (and the one after it), is it a hazard, how many jumps
remain, arrival at the destination — WITHOUT a push-to-talk press:

  * `NavRoute` / `NavRouteClear` -> (re)load or drop the plotted route (from NavRoute.json).
  * `FSDTarget` -> a new target locked in; announce the arriving star's hazard/scoopable
                   status (and the FOLLOWING star's, when that's the useful thing to know).
  * `FSDJump`   -> advance progress; every Nth jump announce jumps remaining, and announce
                   arrival at the final system.

`FSDTarget` locks the route's NEXT waypoint around the time the Commander is actually in
transit to / arriving at the CURRENT one (#148) — so the callout never trusts the event's
`Name` to say which star is "next". Instead it anchors wording to `RouteTracker.lookahead()`,
which reports the arriving star vs. the one after purely by route position.

Unlike generic proactive callouts these are FACTUAL, so they're spoken verbatim (no LLM cost
or embellishment) through the app's proactive line path — which means they inherit the same
guarantees: they only speak when Idle (never over the Commander), a PTT press cancels one, and
they honour the proactive mute. Speaking during hyperspace is fine/desired — nothing here
delays for timing, only for the Idle/mute gates above. Everything is injected so the default
test run is offline.

Off by default ([route].enabled = false).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..ed.route import RouteTracker, is_hazardous_star, is_scoopable

# Hazard warning lines (#147) — named per `is_hazardous_star`'s return value. These SUPERSEDE
# the plain "not scoopable" line for the same star (N/D are also non-scoopable; the warning
# already implies "no fuel here"), so a hazardous star never gets a redundant back-to-back pair.
_HAZARD_LINES = {
    "neutron star": ("Heads up, Commander — next jump's a neutron star. "
                      "Mind the exclusion zone, and no fuel there."),
    "white dwarf": "Careful — a white dwarf next. Watch the jets; you can't scoop it.",
}


@dataclass(frozen=True)
class RouteConfig:
    """Immutable snapshot of `[route]`. Off by default; the capability isn't registered unless
    `enabled`."""
    enabled: bool = False
    every_n: int = 5                 # announce jumps-remaining every Nth jump
    callout_scoopable: bool = True
    callout_jumps_remaining: bool = True
    callout_arrival: bool = True
    callout_hazard: bool = True      # neutron star / white dwarf heads-up (#147)

    @classmethod
    def from_cfg(cls, cfg: dict) -> RouteConfig:
        r = cfg.get("route", {}) or {}
        d = cls()
        return cls(
            enabled=bool(r.get("enabled", False)),
            every_n=max(1, int(r.get("every_n", d.every_n))),
            callout_scoopable=bool(r.get("callout_scoopable", True)),
            callout_jumps_remaining=bool(r.get("callout_jumps_remaining", True)),
            callout_arrival=bool(r.get("callout_arrival", True)),
            callout_hazard=bool(r.get("callout_hazard", True)),
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
        load_navroute: Callable[[], dict | None],
        is_muted: Callable[[], bool] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._speak = speak_line
        self._load_navroute = load_navroute
        self._is_muted = is_muted or (lambda: False)
        self._log = log
        self._tracker = RouteTracker()
        self._last_target: str | None = None   # system of the last scoopable callout

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
        if not self._tracker.active:
            return
        target = event.get("Name")                       # FSDTarget uses 'Name', not 'StarSystem'
        if not target or target == self._last_target:     # already announced this target
            return
        self._last_target = target

        if self._tracker.step_for(target) is None:
            # Off-route detour (manually targeted, not on the plotted route) — no route
            # position to anchor "arriving"/"following" to, so fall back to a single-star line
            # about the event's own class, as before #148.
            star_class = event.get("StarClass")
            if star_class:
                self._announce_star(star_class)
            return

        # On-route: never trust WHICH star this event names (#148 — FSDTarget locks one hop
        # ahead of the pilot's actual next arrival). Anchor to the tracker's own position instead.
        arriving, following = self._tracker.lookahead()
        if arriving is None:
            return
        self._announce_star(arriving.star_class,
                             following.star_class if following else None)

    def _announce_star(self, arriving_class: str, following_class: str | None = None) -> None:
        """Speak the hazard/scoopable callout for the arriving star (and the following one,
        when that's the useful two-star statement). Hazard supersedes scoopable for the same
        star — never a redundant "not scoopable" right after naming the hazard."""
        hazard = is_hazardous_star(arriving_class) if self._cfg.callout_hazard else None
        if hazard:
            self._emit(_HAZARD_LINES[hazard])
            return
        if not self._cfg.callout_scoopable:
            return
        if not is_scoopable(arriving_class):
            self._emit("Heads up — the star you're jumping to isn't scoopable.")
        elif following_class is not None and not is_scoopable(following_class):
            self._emit("This star's scoopable — but the one after isn't, so top off here "
                        "before you jump on.")
        else:
            self._emit("Next star's scoopable.")

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
