"""Unit tests for the VR companion HUD (issue #48) — offline, free, HEADLESS (DESIGN §9).

The VR overlay is a SECOND view over the SAME `HudModel` adapter the 2D HUD (issue #47) uses —
only the rendering surface differs. So these exercise the two PURE pieces the VR sink adds — the
numpy RGBA rasterizer (`render_snapshot_rgba`) and the placement math (`VrPlacement` /
`resolve_transform`) — plus `HudCapability`'s reconciliation of the VR surface with a FAKE view.

Nothing here imports `openvr` or touches a VR runtime: the real `VrHudView` / `make_vr_view` are
never constructed, so the default `pytest` run stays hermetic and needs no headset. The
adapter-reuse itself (events -> `HudSnapshot`) is covered by test_hud_capability.py; here we
assert that same snapshot renders to pixels and that the capability drives the VR sink.
"""
from __future__ import annotations

import ctypes

import numpy as np
import pytest

from covas.capabilities.hud_capability import HudCapability, HudModel, HudSnapshot
from covas.capabilities.vr_hud import (
    VR_PLACEMENTS, VrPlacement, as_overlay_buffer, render_snapshot_rgba, resolve_transform,
)


# --- fakes -----------------------------------------------------------------

class FakeView:
    """Stands in for `VrHudView` (and `HudView`) — records show/hide/close, no openvr/tkinter."""

    def __init__(self):
        self.visible = None
        self.closed = False
        self.shows = 0
        self.hides = 0
        self.placements = []  # every set_placement() call, in order

    def show(self):
        self.visible = True
        self.shows += 1

    def hide(self):
        self.visible = False
        self.hides += 1

    def close(self):
        self.closed = True

    def set_placement(self, placement):
        self.placements.append(placement)


# --- RGBA rasterizer (pure) ------------------------------------------------

def test_render_returns_rgba_buffer_of_requested_shape():
    buf = render_snapshot_rgba(HudSnapshot(), width=256, height=128)
    assert buf.shape == (128, 256, 4)
    assert buf.dtype == np.uint8


def test_render_paints_the_translucent_panel_background():
    buf = render_snapshot_rgba(HudSnapshot(), width=128, height=96)
    # A body pixel below the accent bar carries the near-black translucent panel color.
    assert tuple(buf[80, 64]) == (11, 15, 20, 220)


def test_render_draws_an_accent_bar_at_the_top():
    buf = render_snapshot_rgba(HudSnapshot(), width=128, height=96)
    assert tuple(buf[0, 64]) == (255, 113, 0, 255)  # ED-orange top accent


def test_render_lights_text_pixels_for_the_state_headline():
    """The always-present state row must put lit (non-background) pixels on the panel."""
    buf = render_snapshot_rgba(HudSnapshot(voice_state="Speaking"), width=384, height=192)
    body = buf[12:, :, :3]  # skip the accent bar
    lit = ~np.all(body == np.array([11, 15, 20]), axis=-1)
    assert lit.any()


def test_more_populated_rows_light_more_pixels():
    """A snapshot with all four fields draws strictly more text than a bare one."""
    bare = render_snapshot_rgba(HudSnapshot(voice_state="Idle"), width=512, height=256)
    full = render_snapshot_rgba(
        HudSnapshot(voice_state="Idle", checklist="Scan the beacon (2/10 done)",
                    route="3 jumps to Colonia", callout="Arrived in Sol."),
        width=512, height=256)

    def lit_count(buf):
        body = buf[12:, :, :3]
        return int((~np.all(body == np.array([11, 15, 20]), axis=-1)).sum())

    assert lit_count(full) > lit_count(bare)


def test_render_survives_unicode_and_long_text_without_raising():
    # Curly quotes, middle dot, and 2D row glyphs must fold to ASCII; a very long line truncates.
    snap = HudSnapshot(voice_state="Thinking",
                       route="3 jumps to Colonia  ·  next scoopable",
                       callout="“" + "A" * 500 + "”")
    buf = render_snapshot_rgba(snap, width=320, height=160)
    assert buf.shape == (160, 320, 4)  # no overrun, no exception


def test_render_is_deterministic():
    snap = HudSnapshot(voice_state="Listening", checklist="Dock at Jameson (0/3 done)")
    a = render_snapshot_rgba(snap, width=200, height=120)
    b = render_snapshot_rgba(snap, width=200, height=120)
    assert np.array_equal(a, b)


# --- placement (pure math) -------------------------------------------------

def test_placement_normalize_defaults_to_world():
    p = VrPlacement.normalize(None)
    assert p.mode == "world" and p.mode in VR_PLACEMENTS


def test_placement_normalize_accepts_head_case_insensitively():
    assert VrPlacement.normalize("HEAD").mode == "head"


def test_placement_normalize_rejects_unknown_mode():
    assert VrPlacement.normalize("elsewhere").mode == "world"


def test_placement_normalize_clamps_width_to_sane_range():
    assert VrPlacement.normalize("world", 99.0).width_m == 3.0
    assert VrPlacement.normalize("world", 0.001).width_m == 0.15
    assert VrPlacement.normalize("world", "not-a-number").width_m == 0.55


def test_resolve_transform_is_identity_rotation_with_forward_translation():
    m = resolve_transform(VrPlacement(mode="world", forward_m=1.3, up_m=-0.12))
    assert len(m) == 3 and all(len(row) == 4 for row in m)
    # identity rotation
    assert [m[0][0], m[1][1], m[2][2]] == [1.0, 1.0, 1.0]
    # translation column: up in +Y, forward is -Z
    assert m[1][3] == -0.12
    assert m[2][3] == -1.3


# --- capability drives the VR surface (with a fake view) -------------------

def _cap(vr_enabled, vr_factory, *, hud_2d=False, log=None):
    return HudCapability(
        HudModel(),
        is_enabled=lambda: hud_2d,
        view_factory=lambda p: None,           # no 2D surface in these tests
        vr_is_enabled=lambda: vr_enabled() if callable(vr_enabled) else vr_enabled,
        vr_view_factory=vr_factory,
        log=log)


def test_vr_disabled_at_startup_never_builds_an_overlay():
    built = []
    _cap(False, lambda p: built.append(1) or FakeView())
    assert built == []  # created lazily, only on first enable — openvr never touched


def test_vr_enabled_at_startup_creates_and_shows_the_overlay():
    view = FakeView()
    _cap(True, lambda p: view)
    assert view.visible is True and view.shows == 1


def test_vr_reconcile_toggles_the_overlay():
    view = FakeView()
    enabled = {"v": False}
    cap = _cap(lambda: enabled["v"], lambda p: view)
    assert view.visible is None  # not created while disabled

    enabled["v"] = True
    cap.reconcile()
    assert view.visible is True

    enabled["v"] = False
    cap.reconcile()
    assert view.visible is False


def test_vr_factory_returning_none_is_fail_soft_and_not_retried():
    calls = {"n": 0}

    def factory(p):
        calls["n"] += 1
        return None

    logs = []
    cap = _cap(True, factory, log=logs.append)  # built once at construction
    cap.reconcile()                              # must not retry the failed factory
    assert calls["n"] == 1
    assert any("VR HUD" in m and "no VR runtime" in m for m in logs)


def test_transient_vr_failure_reattempts_but_permanent_latches():
    """#140/§3.8.1 lifecycle: a TRANSIENT build failure (SteamVR not up yet) must not latch, so a
    later reconcile re-attempts (start SteamVR after COVAS++ and the overlay still comes up); a
    PERMANENT one (openvr missing) latches and is not retried."""
    # Transient: predicate says "not permanent" -> the latch resets, factory runs again.
    calls = {"n": 0}
    cap = HudCapability(
        HudModel(), is_enabled=lambda: False, view_factory=lambda p: None,
        vr_is_enabled=lambda: True,
        vr_view_factory=lambda p: (calls.__setitem__("n", calls["n"] + 1), None)[1],
        vr_permanent=lambda: False)                 # transient
    assert calls["n"] == 1                           # first attempt at construction
    cap.reconcile()
    assert calls["n"] == 2                           # re-attempted (no latch)

    # Permanent: predicate says "permanent" -> latched, no retry.
    calls2 = {"n": 0}
    cap2 = HudCapability(
        HudModel(), is_enabled=lambda: False, view_factory=lambda p: None,
        vr_is_enabled=lambda: True,
        vr_view_factory=lambda p: (calls2.__setitem__("n", calls2["n"] + 1), None)[1],
        vr_permanent=lambda: True)                  # permanent
    assert calls2["n"] == 1
    cap2.reconcile()
    assert calls2["n"] == 1                           # NOT retried (latched)


def test_transient_reattempt_succeeds_once_the_surface_is_available():
    """The recovery the bug is about: SteamVR comes up late, so the second attempt SUCCEEDS with
    no restart of COVAS++."""
    view = FakeView()
    ready = {"up": False}

    def factory(p):
        return view if ready["up"] else None

    cap = HudCapability(
        HudModel(), is_enabled=lambda: False, view_factory=lambda p: None,
        vr_is_enabled=lambda: True, vr_view_factory=factory,
        vr_permanent=lambda: False)                 # transient while down
    assert cap._vr_view is None                      # not up yet
    ready["up"] = True                               # "SteamVR started"
    cap.reconcile()
    assert cap._vr_view is view and view.visible is True


def test_probe_vr_reason_branches(monkeypatch):
    """The typed-reason probe (#140): openvr absent -> PERMANENT 'openvr-missing'; openvr present
    but SteamVR down -> TRANSIENT 'steamvr-not-running'; both present -> None (attachable). Robust
    to whether openvr happens to be installed on the test box."""
    import covas.capabilities.vr_hud as vr
    try:
        import openvr  # noqa: F401
        have_openvr = True
    except Exception:  # noqa: BLE001
        have_openvr = False

    monkeypatch.setattr(vr, "_steamvr_running", lambda: False)
    assert vr.probe_vr_reason() == ("steamvr-not-running" if have_openvr else "openvr-missing")
    monkeypatch.setattr(vr, "_steamvr_running", lambda: True)
    assert vr.probe_vr_reason() == (None if have_openvr else "openvr-missing")


def test_action_grounding_guardrail_is_in_the_system_prompt():
    """#143/§3.8.1 truthfulness: the no-invented-ACTIONS rule ships in the static system prompt
    (even with personality + crew off) and is cache-safe, so the model can't confirm a HUD change
    it never made."""
    from covas.llm import build_system, _ACTION_GROUNDING_GUARDRAIL

    bare = build_system({"personality": {"enabled": False}, "crew": {"enabled": False}})
    assert bare is not None and _ACTION_GROUNDING_GUARDRAIL in bare
    low = _ACTION_GROUNDING_GUARDRAIL.lower()
    assert "tool" in low and ("don't invent actions" in low or "invent actions" in low)
    assert build_system({}) == build_system({})   # static -> cache-safe


def test_no_vr_factory_means_no_vr_surface_and_no_crash():
    # The default HudCapability (2D only) has vr_view_factory=None: reconcile must not blow up
    # and must never claim a VR surface.
    cap = HudCapability(HudModel(), is_enabled=lambda: False)
    cap.reconcile()  # no VR factory wired — no-op for the VR surface
    assert cap._vr_view is None


def test_shutdown_closes_the_vr_overlay():
    view = FakeView()
    cap = _cap(True, lambda p: view)
    cap.shutdown()
    assert view.closed is True


def test_the_two_surfaces_are_independent():
    """A headless 2D surface (factory -> None) must not stop the VR surface from showing."""
    vr_view = FakeView()
    cap = HudCapability(
        HudModel(),
        is_enabled=lambda: True,
        view_factory=lambda p: None,       # 2D headless
        vr_is_enabled=lambda: True,
        vr_view_factory=lambda p: vr_view)
    assert vr_view.visible is True and vr_view.shows == 1


# --- the upload buffer (regression: #48 shipped an int here) ---------------
#
# These cover the ONE step the rest of this file can't: handing pixels to SteamVR. The pure
# rasterizer and placement math were always tested, and both were always fine — yet the overlay
# never showed a pixel on real hardware, because `setOverlayRaw` was passed `buf.ctypes.data`
# (a plain int). pyopenvr calls `byref()` on that argument, which rejects ints outright, so
# EVERY repaint raised into the fail-soft guard and logged "VR repaint error". The overlay was
# created and shown correctly and stayed invisible.
#
# The trick is that a fake overlay recording its arguments would NOT have caught it: the int
# arrives fine. Only calling `byref()` — exactly as the real binding does — reproduces it.

def _pyopenvr_would_do(buffer):
    """Mimic pyopenvr's binding: `fn(handle, byref(buffer), w, h, bpp)` with a c_void_p
    argtype. Any argument the real call would reject must be rejected here too."""
    ctypes.c_void_p.from_param(ctypes.byref(buffer))


def test_overlay_buffer_survives_pyopenvrs_byref():
    """The exact call that failed on hardware: byref() over what we pass setOverlayRaw."""
    buf = render_snapshot_rgba(HudSnapshot(voice_state="IDLE"), width=64, height=32)
    _pyopenvr_would_do(as_overlay_buffer(buf))  # raised TypeError before the fix


def test_overlay_buffer_points_at_the_pixels_not_at_a_pointer():
    """Guards the plausible-but-wrong fix. `data_as(POINTER(...))` and `c_void_p(addr)` both
    satisfy byref() — and then hand SteamVR the address OF THE POINTER, not the pixels. Only a
    buffer whose own address IS the image data is correct."""
    buf = render_snapshot_rgba(HudSnapshot(voice_state="IDLE"), width=64, height=32)
    assert ctypes.addressof(as_overlay_buffer(buf)) == buf.ctypes.data


def test_overlay_buffer_aliases_without_copying():
    """from_buffer must alias the numpy memory in place — a copy would upload stale pixels
    (and silently waste 1 MB per repaint)."""
    buf = render_snapshot_rgba(HudSnapshot(voice_state="IDLE"), width=64, height=32)
    raw = as_overlay_buffer(buf)
    buf[0, 0] = (7, 7, 7, 7)
    assert list(raw[:4]) == [7, 7, 7, 7]


def test_raw_address_int_is_rejected_by_byref():
    """Documents the actual defect: the shipped form can't survive the binding at all."""
    buf = render_snapshot_rgba(HudSnapshot(voice_state="IDLE"), width=64, height=32)
    with pytest.raises(TypeError):
        _pyopenvr_would_do(buf.ctypes.data)  # what #48 passed


# --- placement: position / distance / pitch / curvature (live-adjustable) ---

def test_normalize_clamps_all_placement_fields():
    """Every field clamps to its comfortable range so a bad setting can't place the panel
    unusably or raise."""
    p = VrPlacement.normalize("world", 99.0, forward_m=99.0, up_m=99.0,
                              offset_x_m=-99.0, pitch_deg=999.0, curvature=9.0)
    assert p.width_m == 3.0 and p.forward_m == 5.0 and p.up_m == 2.0
    assert p.offset_x_m == -2.0 and p.pitch_deg == 60.0 and p.curvature == 1.0


def test_normalize_defaults_new_fields_on_bad_input():
    p = VrPlacement.normalize("world", forward_m="x", pitch_deg=None, curvature="nope")
    assert p.forward_m == 1.30 and p.pitch_deg == 0.0 and p.curvature == 0.0


def test_resolve_transform_applies_lateral_and_distance_offsets():
    m = resolve_transform(VrPlacement(offset_x_m=0.3, up_m=-0.1, forward_m=1.5))
    assert m[0][3] == 0.3          # lateral (+X = right)
    assert m[1][3] == -0.1         # vertical
    assert m[2][3] == -1.5         # forward is -Z


def test_resolve_transform_pitch_rotates_about_x():
    """A nonzero pitch makes the rotation non-identity, tilting the panel about its X axis."""
    flat = resolve_transform(VrPlacement(pitch_deg=0.0))
    tilted = resolve_transform(VrPlacement(pitch_deg=30.0))
    assert [flat[0][0], flat[1][1], flat[2][2]] == [1.0, 1.0, 1.0]   # identity at 0
    assert tilted[1][1] != 1.0                                       # rotated about X
    # X row is untouched by an X-axis rotation
    assert tilted[0][0] == 1.0 and tilted[0][1] == 0.0


def test_resolve_transform_positive_pitch_leans_top_toward_viewer():
    """Direction guard for the ONE pitch convention (#142, §3.8.1): a positive `pitch_deg` must
    lean the panel's TOP toward the viewer. The panel's local +Y (top edge) maps to world
    R·(0,1,0) = (0, cos, −sin); the −sin Z-component is the corrected (hardware-confirmed)
    sign — so a look-down pin (which sets pitch_deg > 0) reads head-on rather than tipping away.
    Locks the sign so it can't silently regress back to top-AWAY."""
    import math
    m = resolve_transform(VrPlacement(pitch_deg=30.0, yaw_deg=0.0))
    top_edge_z = m[2][1]                      # world Z of the panel's local +Y (top) axis
    assert top_edge_z < 0.0                   # corrected: top leans toward the viewer
    assert abs(top_edge_z - (-math.sin(math.radians(30.0)))) < 1e-9
    # And it is a pure tilt reversal from the pre-fix rendering: the sign is simply flipped.
    assert m[1][1] == math.cos(math.radians(30.0))


def test_look_down_pin_fixture_yields_low_panel_tilted_toward_viewer():
    """End-to-end direction for look-to-place (#142): a look-DOWN gaze drops the panel low AND
    tilts its top toward you. Mirrors `_pin_to_gaze`'s pure math on a known look-down pose, so it
    needs no VR runtime."""
    import math
    from covas.capabilities.vr_hud import hmd_pitch_deg, hmd_yaw_deg

    def rx(deg):  # HMD pitched by +deg about X (looking up); forward = -Z column
        r = math.radians(deg)
        c, s = math.cos(r), math.sin(r)
        return [[1.0, 0.0, 0.0, 0.0], [0.0, c, -s, 0.0], [0.0, s, c, 0.0]]

    pose = rx(-30.0)                                     # looking DOWN 30°
    e = math.radians(hmd_pitch_deg(pose))               # gaze elevation < 0
    d = 1.3
    pinned = VrPlacement(yaw_deg=hmd_yaw_deg(pose), forward_m=d * math.cos(e),
                         up_m=d * math.sin(e), pitch_deg=-math.degrees(e), offset_x_m=0.0)
    assert pinned.up_m < 0.0                             # panel dropped low
    assert pinned.pitch_deg > 0.0                        # positive tilt...
    assert resolve_transform(pinned)[2][1] < 0.0         # ...renders top-toward-you


def test_resolve_transform_offset_moves_centre_at_yaw_zero_and_pinned_yaw():
    """Lateral offset slides the panel centre view-relatively at yaw 0 AND after a pin (#144):
    the offset acts in the YAWED frame, so it's never a dead knob — just view-relative."""
    import math
    at0 = resolve_transform(VrPlacement(offset_x_m=0.5, forward_m=1.3, yaw_deg=0.0))
    assert at0[0][3] == 0.5                              # straight to world +X at yaw 0
    yaw = 90.0
    yawed = resolve_transform(VrPlacement(offset_x_m=0.5, forward_m=1.3, yaw_deg=yaw))
    base = resolve_transform(VrPlacement(offset_x_m=0.0, forward_m=1.3, yaw_deg=yaw))
    # A non-zero offset still moves the centre at a pinned yaw (here along world Z at yaw 90).
    assert (yawed[0][3], yawed[2][3]) != (base[0][3], base[2][3])
    assert abs(yawed[2][3] - base[2][3]) > 0.4          # ~0.5 m of lateral slide, now along Z


def test_set_vr_placement_relays_to_the_live_view():
    """HudCapability.set_vr_placement forwards to a live VR view; the app calls this after any
    settings change so distance / pitch / curvature apply without a re-toggle."""
    vr_view = FakeView()
    cap = HudCapability(
        HudModel(), is_enabled=lambda: False,
        view_factory=lambda p: None,
        vr_is_enabled=lambda: True, vr_view_factory=lambda p: vr_view)
    p = VrPlacement.normalize("world", 0.6, curvature=0.1)
    cap.set_vr_placement(p)
    assert vr_view.placements == [p]


def test_set_vr_placement_is_safe_with_no_vr_view():
    """No VR surface up (or a view without set_placement) -> no-op, never raises."""
    cap = HudCapability(
        HudModel(), is_enabled=lambda: False,
        view_factory=lambda p: None,
        vr_is_enabled=lambda: False, vr_view_factory=lambda p: None)
    cap.set_vr_placement(VrPlacement())  # must not raise


# --- yaw / look-to-place math ----------------------------------------------

def test_resolve_transform_yaw_is_identity_at_zero():
    m = resolve_transform(VrPlacement(yaw_deg=0.0, pitch_deg=0.0, forward_m=1.3, up_m=-0.12))
    assert [m[0][0], m[1][1], m[2][2]] == [1.0, 1.0, 1.0]
    assert m[1][3] == -0.12 and m[2][3] == -1.3


def test_resolve_transform_yaw_rotates_about_y():
    """A 90° heading swings the forward translation from −Z onto the X axis (Ry rotation)."""
    m = resolve_transform(VrPlacement(yaw_deg=90.0, forward_m=1.3))
    assert abs(m[0][3] - (-1.3)) < 1e-9   # forward now lies along X
    assert abs(m[2][3]) < 1e-9
    assert abs(m[1][1] - 1.0) < 1e-9      # Y row untouched by a Y-axis rotation


def test_hmd_yaw_deg_from_known_matrices():
    from covas.capabilities.vr_hud import hmd_yaw_deg
    ident = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]        # looking straight ahead
    ry90 = [[0, 0, 1, 0], [0, 1, 0, 0], [-1, 0, 0, 0]]        # turned 90°
    assert abs(hmd_yaw_deg(ident) - 0.0) < 1e-9
    assert abs(hmd_yaw_deg(ry90) - 90.0) < 1e-9


def test_hmd_pitch_deg_from_known_matrices():
    """Elevation from the HMD pose: level -> 0, looking up -> positive, down -> negative (#107)."""
    import math
    from covas.capabilities.vr_hud import hmd_pitch_deg
    ident = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]        # level gaze
    assert abs(hmd_pitch_deg(ident) - 0.0) < 1e-9

    def rx(deg):  # HMD pitched by +deg about X (looking up); forward = -Z column
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0]]

    assert abs(hmd_pitch_deg(rx(30.0)) - 30.0) < 1e-9         # looking up -> positive
    assert abs(hmd_pitch_deg(rx(-30.0)) - (-30.0)) < 1e-9     # looking down -> negative
    assert abs(hmd_pitch_deg(rx(90.0)) - 90.0) < 1e-9         # straight up
    # A pose nudged a hair past ±1 by rounding must clamp, not raise.
    assert abs(hmd_pitch_deg([[1, 0, 0, 0], [0, 0, -1.0000001, 0], [0, 1, 0, 0]]) - 90.0) < 1e-6


def test_normalize_wraps_yaw_to_signed_range():
    assert VrPlacement.normalize("world", yaw_deg=270).yaw_deg == -90.0
    assert VrPlacement.normalize("world", yaw_deg="x").yaw_deg == 0.0
