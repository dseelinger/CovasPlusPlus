"""Long-hyperspace flavor remark (issue #149) — pass the tunnel time on a longer-than-normal jump.

Hyperspace is dead air. When a plotted jump is unusually long, this fills it with ONE short,
LLM-varied, in-character remark — the Commander's own examples: *"I wonder if a Thargoid is in our
future,"* *"I hope we don't run into any orange sidewinders."* (Long jumps are the folkloric setup
for a Thargoid **hyperdiction**, so it's perfect flavor.) It rides the SAME proactive machinery as
every other callout — cheap tier, never over a user turn, honours the proactive enable/mute — but is
**pure atmosphere** (`fact_bearing=False`): it asserts NO game facts, so there's nothing to ground.

A REACTOR-only capability: it exposes no LLM tools (no `tools()`, no HelpMeta — like the route
callouts). It watches the bus for `StartJump` with `JumpType == "Hyperspace"` — the journal
publishes every event on the bus, so this reaches `on_event` even though `journal.py` doesn't
context-handle StartJump — and gates on the **plotted jump distance** computed from the route's
`StarPos` coordinates (`ed/route.py`), firing only past a configurable threshold so ordinary jumps
stay quiet. Mid-jump timing is the point: StartJump fires as the tunnel opens (unlike `FSDJump`'s
`JumpDist`, which only lands on arrival — too late), so distance-gating at StartJump is what lets
the line land while there's still dead air to fill.

TODO (#146): when the speech queue lands (Wave 3), a real callout/warning (e.g. a hazard heads-up)
should PREEMPT this flavor line — it's the lowest-priority thing to say. That preemption belongs in
the queue, not here; this capability just offers the line via the normal never-interrupt path.
"""
from __future__ import annotations

import time
from typing import Callable

from ..ed.route import is_long_jump, jump_distance, route_coords


def build_long_jump_prompt(distance_ly: float | None = None) -> str:
    """The user-message prompt for the long-jump flavor line. Distinct from the generic
    `build_prompt`: this asks for PURE atmosphere and asserts no game facts (`fact_bearing=False`).
    The (cached) personality system prompt keeps the companion in character; this just sets the
    scene and asks for one short, non-repeating remark. The distance, if known, is offered only as
    MOOD ('a long haul') — the line must not quote it as a figure."""
    lines = [
        "You are speaking UNPROMPTED — the Commander did not ask anything. You are part-way "
        "through a LONGER-THAN-NORMAL hyperspace jump: the witchspace tunnel, a stretch of dead "
        "air with nothing to do.",
    ]
    if isinstance(distance_ly, (int, float)) and not isinstance(distance_ly, bool):
        lines.append("(For your own sense of mood only, not to quote: it's a long haul.)")
    lines.append(
        "Make ONE short, light, in-character remark to pass the time about the long jump — you "
        "may hint at Thargoid or hyperdiction folklore (the old spacer superstition that long "
        "jumps invite trouble). Keep it speculative and playful. Assert NO facts — do not claim "
        "anything is actually out there, name no real place or number. Never repeat a line you've "
        "used before; vary it every time. Under 20 words. Do not ask a question or expect a reply."
    )
    return "\n".join(lines)


class LongJumpCapability:
    """Reacts to `StartJump(Hyperspace)` bus events and, when the plotted jump is long enough and
    the policy allows, asks the app to speak a one-off flavor line via the proactive path.

    `speak` is the app callback `(event, prompt) -> bool`: it originates the spoken line on the
    cheap tier with the given prompt OVERRIDE (pure flavor, no place/visit enrichment) and returns
    True only if it actually started — i.e. the app was idle. The cooldown is armed only on True, so
    a line skipped because the Commander was talking isn't silently swallowed by the cooldown.
    """

    def __init__(
        self,
        policy,
        speak: Callable[[dict, str], bool],
        *,
        load_navroute: Callable[[], dict | None],
        current_system: Callable[[], str | None],
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.policy = policy
        self._speak = speak
        self._load_navroute = load_navroute
        self._current_system = current_system
        self._clock = clock
        self._log = log

    def on_event(self, event: dict) -> None:
        """Bus hook (dispatched by the app's event pump). Must never raise — it runs on the shared
        event-pump thread. Fail-soft: any error, or a jump we can't measure, is simply silent."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            if event.get("event") != "StartJump":
                return
            if str(event.get("JumpType") or "") != "Hyperspace":
                return
            dest = event.get("StarSystem")
            origin = self._current_system()   # still the FROM system — FSDJump hasn't landed yet
            if not dest or not origin:
                return
            coords = route_coords(self._load_navroute())
            dist = jump_distance(coords, origin, dest)
            if dist is None:
                return   # off-route / unplotted — can't gate, so stay silent
            if not is_long_jump(dist, self.policy.cfg.long_jump_ly):
                return
            now = self._clock()
            ok, reason = self.policy.should_long_jump(now)
            if not ok:
                return
            started = self._speak(event, build_long_jump_prompt(dist))
            if started:
                self.policy.mark_long_jump(now)
                if self._log is not None:
                    self._log(f"{reason} — {dist:.0f} ly to {dest}")
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass
