"""Sequenced-macro batch (issue #33) — status-checked timed sequences.

The Tier-1 batches (#30–#35) each press a *single* key. This batch is the first to use the
**sequence** framework (`keybinds/sequence.py`): a macro whose `steps` are a small scripted
mix of press / hold / wait and — crucially — Status.json checks *between* the keys, so the
sequence verifies game state instead of firing blind (DESIGN §6, "macros over single keys").

Worked macro: **launch** — lift off the pad and depart, the design's own launch example
(throttle up → clear the pad → boost → retract gear). It exercises every step kind:

  * `require_status(landing_gear, True)` — precondition: only launch from the pad. Right after
    you press *undock*, ED hovers you above the pad with the gear DOWN; if the gear isn't down
    you're not on a pad and the sequence refuses rather than firing keys into open flight.
  * `hold(UpThrustButton, 1.2s)` — on keyboard, vertical thrust is a *held* key; hold it to
    rise clear of the pad. This is the hold-primitive the design calls out ("hold to charge").
  * `press(SetSpeed50)` + `wait` + `press(UseBoostJuice)` — throttle to half and boost out.
  * `press(LandingGearToggle)` then `await_status(landing_gear, False)` — retract the gear and
    then *verify from Status.json that it actually came up*, rather than assuming the keypress
    landed. This is the inter-step state check the framework exists for.

It's `confirm_required=True` (consequential — it moves the ship) and, like every batch macro,
**NOT in the default allowlist**: a Commander opts it in by name via `[keybinds].allowlist`.
The LLM only SELECTS this named macro; it never assembles the step list itself.
"""
from __future__ import annotations

from ...ed.modes import MODE_MAINSHIP
from ..registry import Macro, register
from ..sequence import AWAIT_STATUS, HOLD, PRESS, REQUIRE_STATUS, WAIT, Step

register(Macro(
    name="launch",
    tool="launch_from_pad",
    action="",   # unused — this is a sequence macro (see steps)
    arm_phrase=("lift off the pad and depart — throttle up, rise off the pad, boost clear, and "
                "retract the gear once you're actually off the pad"),
    done_phrase="Launched — off the pad and gear retracted.",
    modes=frozenset({MODE_MAINSHIP}),   # a main-ship departure
    confirm_required=True,              # consequential (moves the ship) — arm-and-confirm
    steps=(
        # Precondition: gear down means you're on/just off the pad (post-undock). Refuse otherwise.
        Step(REQUIRE_STATUS, status_key="landing_gear", expect=True,
             describe="your landing gear isn't down — run this right after you undock, while "
                      "you're still hovering over the pad"),
        Step(PRESS, action="SetSpeed50"),               # throttle to half
        Step(HOLD, action="UpThrustButton", seconds=1.2),  # hold vertical thrust to clear the pad
        Step(WAIT, seconds=0.5),                        # let it settle before boosting
        Step(PRESS, action="UseBoostJuice"),            # boost clear of the station
        Step(PRESS, action="LandingGearToggle"),        # retract the gear
        # Verify from Status.json that the gear actually retracted — don't assume the keypress landed.
        Step(AWAIT_STATUS, status_key="landing_gear", expect=False, seconds=4.0, poll=0.5,
             describe="couldn't confirm the landing gear retracted — check it manually"),
    ),
))
