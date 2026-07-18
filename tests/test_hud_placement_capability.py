"""Unit tests for HudPlacementCapability (issue #48) — offline, free, no VR runtime.

The nudge/pin capability is pure over injected I/O: a config getter, an apply(patch) sink, and a
pin() that stands in for the HMD-gaze capture. So we assert the arithmetic (direction, step,
amount, clamp), that look-to-place persists the captured heading and recentres, and that a
missing VR overlay degrades to a spoken sentence.
"""
from __future__ import annotations

from covas.capabilities.hud_placement_capability import HudPlacementCapability
from covas.capabilities.vr_hud import VrPlacement


def _cap(pin=None):
    """A capability over a mutable [hud] dict that records + applies each patch (so successive
    nudges accumulate, exactly like the real persist-then-reload path)."""
    hud = {"vr_distance_m": 1.30, "vr_offset_x_m": 0.0, "vr_offset_y_m": -0.12,
           "vr_pitch_deg": 0.0, "vr_curvature": 0.1, "vr_width_m": 0.55, "vr_yaw_deg": 0.0}
    applied = []

    def apply(patch):
        applied.append(patch)
        hud.update(patch.get("hud", {}))

    cap = HudPlacementCapability(get_hud=lambda: hud, apply_patch=apply, pin=pin)
    return cap, hud, applied


def _run(cap, action, **kw):
    return cap.run_tool("adjust_vr_hud", {"action": action, **kw})


def test_left_right_move_lateral_offset():
    cap, hud, _ = _cap()
    _run(cap, "left")
    assert round(hud["vr_offset_x_m"], 3) == -0.10
    _run(cap, "right")
    assert round(hud["vr_offset_x_m"], 3) == 0.0


def test_up_down_move_vertical_offset():
    cap, hud, _ = _cap()
    _run(cap, "up")
    assert round(hud["vr_offset_y_m"], 3) == -0.02   # -0.12 + 0.10
    _run(cap, "down")
    assert round(hud["vr_offset_y_m"], 3) == -0.12


def test_closer_farther_and_forward_back_aliases_move_distance():
    cap, hud, _ = _cap()
    _run(cap, "closer")
    assert round(hud["vr_distance_m"], 3) == 1.20
    _run(cap, "farther")
    assert round(hud["vr_distance_m"], 3) == 1.30
    _run(cap, "back")       # alias of closer
    assert round(hud["vr_distance_m"], 3) == 1.20
    _run(cap, "forward")    # alias of farther
    assert round(hud["vr_distance_m"], 3) == 1.30


def test_amount_is_centimetres_for_moves():
    cap, hud, _ = _cap()
    _run(cap, "closer", amount=25)
    assert round(hud["vr_distance_m"], 3) == 1.05   # 1.30 − 0.25 m


def test_tilt_curve_size_nudges():
    cap, hud, _ = _cap()
    _run(cap, "tilt_up")
    assert hud["vr_pitch_deg"] == 5.0
    _run(cap, "rounder")
    assert round(hud["vr_curvature"], 3) == 0.12
    _run(cap, "bigger")
    assert round(hud["vr_width_m"], 3) == 0.60


def test_smaller_reduces_width():
    # The "smaller" direction was untested (only "bigger" was) — a VR retest reported it not
    # shrinking, so pin the symmetric arithmetic down here (issue #48 retest).
    cap, hud, _ = _cap()
    _run(cap, "smaller")
    assert round(hud["vr_width_m"], 3) == 0.50   # 0.55 − 0.05
    _run(cap, "bigger")
    assert round(hud["vr_width_m"], 3) == 0.55   # back up, same step


def test_width_clamps_at_floor():
    cap, hud, _ = _cap()
    for _ in range(50):
        _run(cap, "smaller")
    assert hud["vr_width_m"] == 0.15   # never below the clamp floor


def test_on_off_toggle_vr_enabled():
    # The model reaches for this VR-HUD tool for "turn the VR HUD on/off"; the toggle must write
    # [hud].vr_enabled through the same apply path, not confabulate an in-game switch.
    cap, hud, applied = _cap()
    msg = _run(cap, "on")
    assert "on" in msg.lower()
    assert applied[-1] == {"hud": {"vr_enabled": True}}
    assert hud["vr_enabled"] is True
    msg = _run(cap, "off")
    assert "off" in msg.lower()
    assert applied[-1] == {"hud": {"vr_enabled": False}}
    assert hud["vr_enabled"] is False


def test_distance_clamps_at_floor():
    cap, hud, _ = _cap()
    for _ in range(50):
        _run(cap, "closer")
    assert hud["vr_distance_m"] == 0.30   # never below the clamp floor


def test_pin_here_persists_heading_and_recentres():
    cap, hud, _ = _cap(pin=lambda: VrPlacement(yaw_deg=42.0))
    hud["vr_offset_x_m"] = 0.5            # pre-existing lateral offset
    msg = _run(cap, "pin_here")
    assert "Pinned" in msg
    assert hud["vr_yaw_deg"] == 42.0      # captured gaze heading
    assert hud["vr_offset_x_m"] == 0.0    # recentred on the gaze


def test_pin_here_persists_the_full_placement_not_just_yaw():
    """A pin that captures pitch/height/distance must write ALL of them to [hud] — else the next
    settings change rebuilds the placement from config and silently drops them (#107)."""
    # _pin_to_gaze returns offset_x_m=0.0 (recentred on the gaze); the capability persists it.
    cap, hud, _ = _cap(pin=lambda: VrPlacement(
        yaw_deg=20.0, pitch_deg=15.0, up_m=0.4, forward_m=1.1, offset_x_m=0.0))
    hud["vr_offset_x_m"] = 0.5            # pre-existing lateral offset
    _run(cap, "pin_here")
    assert hud["vr_yaw_deg"] == 20.0
    assert hud["vr_pitch_deg"] == 15.0
    assert round(hud["vr_offset_y_m"], 3) == 0.4
    assert round(hud["vr_distance_m"], 3) == 1.1
    assert hud["vr_offset_x_m"] == 0.0    # recentred on the gaze


def test_pin_here_clamps_extreme_captured_values_on_write():
    """A near-vertical gaze can return out-of-range pitch/height; the persisted config clamps."""
    cap, hud, _ = _cap(pin=lambda: VrPlacement(pitch_deg=200.0, up_m=9.0))
    _run(cap, "pin_here")
    assert hud["vr_pitch_deg"] == 60.0    # ±60° clamp
    assert hud["vr_offset_y_m"] == 2.0    # ±2 m clamp


def test_pin_here_is_soft_when_no_vr_overlay():
    cap, hud, applied = _cap(pin=lambda: None)
    msg = _run(cap, "pin_here")
    assert "couldn't pin" in msg.lower()
    assert applied == []                  # nothing persisted when there's nothing to pin


def test_reset_restores_defaults():
    cap, hud, _ = _cap()
    _run(cap, "left"); _run(cap, "tilt_up"); _run(cap, "closer")
    _run(cap, "reset")
    assert hud["vr_distance_m"] == 1.30 and hud["vr_offset_x_m"] == 0.0
    assert hud["vr_pitch_deg"] == 0.0 and hud["vr_curvature"] == 0.1


def test_unknown_action_lists_the_valid_ones():
    cap, _, applied = _cap()
    msg = _run(cap, "sideways")
    assert "don't know how" in msg.lower() and "pin here" in msg.lower()
    assert applied == []                  # nothing applied on an unknown action


def test_the_single_tool_is_advertised_with_an_action_enum():
    cap, _, _ = _cap()
    tools = cap.tools()
    assert len(tools) == 1 and tools[0]["name"] == "adjust_vr_hud"
    enum = tools[0]["input_schema"]["properties"]["action"]["enum"]
    assert "pin_here" in enum and "left" in enum and "closer" in enum
    assert "on" in enum and "off" in enum   # the on/off toggle lives on this tool too
