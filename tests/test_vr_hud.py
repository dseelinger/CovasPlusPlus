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

    def show(self):
        self.visible = True
        self.shows += 1

    def hide(self):
        self.visible = False
        self.hides += 1

    def close(self):
        self.closed = True


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
