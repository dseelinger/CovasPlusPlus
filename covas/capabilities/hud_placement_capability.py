"""Voice repositioning for the in-headset VR HUD (issue #48).

The absolute placement settings (`[hud].vr_distance_m` etc.) are set via the normal
settings-by-voice path ("set the VR HUD distance to 1.5"). This capability adds the two things
that path can't express: **relative nudges** ("move the HUD left", "closer", "tilt it up") and
**look-to-place** ("pin the HUD here" — swing it to the direction you're facing). Both are the
natural way to place a panel you're looking at in a headset, hands still on the stick.

It also owns the on/off toggle ("turn the VR HUD on"/"off"): the model reaches for this one VR-HUD
tool for any VR-HUD request, so the enable switch lives here too (writing `[hud].vr_enabled`) —
without it the model tended to confabulate a non-existent in-game switch instead of flipping the
setting (issue #48 retest).

One tool, `adjust_vr_hud(action, amount?)`, keeps the per-turn token cost to a single schema. It
reads the current `[hud]` values, computes the new one, clamps it, and applies it through the
same `update_settings` path the Settings page uses — so a nudge PERSISTS and applies **live**
(the overlay moves within a poll, no re-toggle). `pin_here` asks the live overlay for the HMD
heading and writes it to `vr_yaw_deg`. Everything is injected (config getter / apply / pin), so
the default `pytest` run exercises it offline with fakes (DESIGN §9). Fail soft: any problem is a
spoken sentence, never a raise.
"""
from __future__ import annotations

from typing import Callable, Optional

from .base import HelpMeta

_TOOL = "adjust_vr_hud"

# Clamp ranges mirror VrPlacement.normalize, applied on WRITE so a repeated nudge can't walk a
# persisted value out of range (the view clamps on read too, but the config shouldn't drift).
_RANGES = {
    "vr_distance_m": (0.30, 5.0),
    "vr_offset_x_m": (-2.0, 2.0),
    "vr_offset_y_m": (-2.0, 2.0),
    "vr_pitch_deg": (-60.0, 60.0),
    "vr_curvature": (0.0, 1.0),
    "vr_width_m": (0.15, 3.0),
}
_DEFAULTS = {  # config defaults, for reading current values and for "reset"
    "vr_distance_m": 1.30, "vr_offset_x_m": 0.0, "vr_offset_y_m": -0.12,
    "vr_pitch_deg": 0.0, "vr_curvature": 0.1, "vr_width_m": 0.55, "vr_yaw_deg": 0.0,
}
_STEP_M, _STEP_DEG, _STEP_CURVE, _STEP_W = 0.10, 5.0, 0.02, 0.05  # default nudge sizes

_ACTIONS = (
    "on", "off",
    "left", "right", "up", "down", "closer", "farther", "forward", "back",
    "tilt_up", "tilt_down", "flatter", "rounder", "bigger", "smaller",
    "center", "pin_here", "reset",
)

_DESC = (
    "Turn the in-headset VR HUD overlay ON or OFF, or reposition it by a relative nudge or by "
    "look-to-place. ALWAYS call this — NOT any other tool — for 'turn the VR HUD on'/'off' "
    "(there is no separate in-game switch; this is the on/off control), 'move the HUD "
    "left/right/up/down', 'closer'/'farther' (or 'forward'/'back'), 'tilt it up/down', "
    "'flatter'/'more curved', 'bigger'/'smaller', 'centre the HUD', 'reset the HUD position', "
    "and 'pin the HUD here' (place it along where I'm looking — matching my gaze left/right AND "
    "up/down, and tilting it to face me). It applies live and remembers the "
    "new state/position. It affects only the VR overlay, not the 2D window. For an exact "
    "placement value ('set the distance to 1.5') use the settings command instead. `action` is "
    "the direction/verb; optional `amount` is centimetres for moves or degrees for tilt (omit "
    "for a comfortable default step)."
)


def _num(v: object, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(field: str, v: float) -> float:
    lo, hi = _RANGES[field]
    return min(max(v, lo), hi)


class HudPlacementCapability:
    """Relative-nudge + look-to-place voice control for the VR HUD overlay."""

    # Small, conversation-adjacent control — lives with the rest of core utility (help/settings/
    # HUD), so it survives at every optimization level except Bare.
    TIERING_GROUP = "core"

    def __init__(self, *, get_hud: Callable[[], dict],
                 apply_patch: Callable[[dict], None],
                 pin: Optional[Callable[[], object]] = None,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self._get_hud = get_hud          # () -> the live [hud] config sub-dict
        self._apply = apply_patch        # (patch) -> None : persist + live-apply (update_settings)
        self._pin = pin                  # () -> VrPlacement | None : capture HMD gaze
        self._log = log

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="VR HUD placement",
            group="companion HUD",
            one_liner=("I turn the in-headset VR HUD on and off and move it when you say things "
                       "like 'turn the VR HUD on', 'move it left', 'closer', 'tilt it up', or "
                       "'pin the HUD here'."),
            example="pin the HUD here",
        )

    def tools(self) -> list[dict]:
        return [{
            "name": _TOOL,
            "description": _DESC,
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(_ACTIONS),
                               "description": "Which way to move / what to do."},
                    "amount": {"type": "number",
                               "description": "Optional nudge size: centimetres for moves, "
                                              "degrees for tilt. Omit for a default step."},
                },
                "required": ["action"],
            },
        }]

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL:
            return f"Unknown tool: {name}"
        action = str((inp or {}).get("action", "")).strip().lower().replace(" ", "_")
        amount = (inp or {}).get("amount")
        pos = isinstance(amount, (int, float)) and amount > 0
        step_m = (amount / 100.0) if pos else _STEP_M   # amount is centimetres for moves
        step_deg = float(amount) if pos else _STEP_DEG  # amount is degrees for tilt

        # On/off toggle — the model reaches for THIS tool for any "VR HUD" request, so the
        # enable switch lives here too (writing [hud].vr_enabled through the same live-apply
        # path). Without it the model tended to confabulate an in-game switch (issue #48 retest).
        if action in ("on", "enable", "show"):
            self._apply({"hud": {"vr_enabled": True}})
            return "Turned the VR HUD on."
        if action in ("off", "disable", "hide"):
            self._apply({"hud": {"vr_enabled": False}})
            return "Turned the VR HUD off."

        hud = self._get_hud() or {}
        cur = {k: _num(hud.get(k), d) for k, d in _DEFAULTS.items()}

        if action in ("pin_here", "pin", "here"):
            placement = self._pin() if self._pin else None
            if placement is None:
                return "I couldn't pin the HUD — the VR overlay isn't running."
            # Persist the WHOLE placement, not just yaw. A later settings change triggers
            # _reconcile_hud -> _vr_hud_placement, which rebuilds the placement from config and
            # re-applies it — so any pinned field left out of [hud] is silently overwritten on
            # the next nudge/toggle. Map every VrPlacement field back to its vr_* config key (the
            # same mapping _vr_hud_placement reads) and clamp on write, so config and the live
            # overlay can't disagree. (#107)
            self._apply({"hud": {
                "vr_yaw_deg": float(getattr(placement, "yaw_deg", 0.0)),
                "vr_pitch_deg": _clamp("vr_pitch_deg", _num(getattr(placement, "pitch_deg", 0.0), 0.0)),
                "vr_offset_y_m": _clamp("vr_offset_y_m", _num(getattr(placement, "up_m", 0.0), 0.0)),
                "vr_distance_m": _clamp("vr_distance_m", _num(getattr(placement, "forward_m", 1.30), 1.30)),
                "vr_offset_x_m": _clamp("vr_offset_x_m", _num(getattr(placement, "offset_x_m", 0.0), 0.0)),
            }})
            return "Pinned the HUD to your view."

        if action == "reset":
            self._apply({"hud": dict(_DEFAULTS)})
            return "Reset the HUD to its default position."

        patch: dict = {}
        say = ""
        if action == "left":
            patch["vr_offset_x_m"] = _clamp("vr_offset_x_m", cur["vr_offset_x_m"] - step_m); say = "Moved the HUD left."
        elif action == "right":
            patch["vr_offset_x_m"] = _clamp("vr_offset_x_m", cur["vr_offset_x_m"] + step_m); say = "Moved the HUD right."
        elif action == "up":
            patch["vr_offset_y_m"] = _clamp("vr_offset_y_m", cur["vr_offset_y_m"] + step_m); say = "Raised the HUD."
        elif action == "down":
            patch["vr_offset_y_m"] = _clamp("vr_offset_y_m", cur["vr_offset_y_m"] - step_m); say = "Lowered the HUD."
        elif action in ("closer", "back", "nearer"):
            patch["vr_distance_m"] = _clamp("vr_distance_m", cur["vr_distance_m"] - step_m); say = "Brought the HUD closer."
        elif action in ("farther", "further", "forward"):
            patch["vr_distance_m"] = _clamp("vr_distance_m", cur["vr_distance_m"] + step_m); say = "Pushed the HUD farther out."
        elif action in ("tilt_up",):
            patch["vr_pitch_deg"] = _clamp("vr_pitch_deg", cur["vr_pitch_deg"] + step_deg); say = "Tilted the HUD up toward you."
        elif action in ("tilt_down",):
            patch["vr_pitch_deg"] = _clamp("vr_pitch_deg", cur["vr_pitch_deg"] - step_deg); say = "Tilted the HUD down."
        elif action in ("rounder", "more_curve", "curve"):
            patch["vr_curvature"] = _clamp("vr_curvature", cur["vr_curvature"] + _STEP_CURVE); say = "Curved the HUD a bit more."
        elif action in ("flatter", "less_curve", "flat"):
            patch["vr_curvature"] = _clamp("vr_curvature", cur["vr_curvature"] - _STEP_CURVE); say = "Flattened the HUD a bit."
        elif action in ("bigger", "wider", "larger"):
            patch["vr_width_m"] = _clamp("vr_width_m", cur["vr_width_m"] + _STEP_W); say = "Made the HUD bigger."
        elif action in ("smaller", "narrower"):
            patch["vr_width_m"] = _clamp("vr_width_m", cur["vr_width_m"] - _STEP_W); say = "Made the HUD smaller."
        elif action == "center":
            patch["vr_offset_x_m"] = 0.0; say = "Centred the HUD."
        else:
            return (f"I don't know how to move the HUD '{action}'. Try left, right, up, down, "
                    f"closer, farther, tilt up/down, flatter, rounder, bigger, smaller, centre, "
                    f"reset, or pin here.")

        self._apply({"hud": patch})
        if self._log is not None:
            self._log(f"nudge '{action}' -> {patch}")
        return say
