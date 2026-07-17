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
