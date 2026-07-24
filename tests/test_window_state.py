"""Unit tests for native-window geometry sanitizing (packaged app) — offline, free.

`sanitize_geometry` is pure: no PyWebView, no display, no I/O. These lock the rules that keep a
restored window on-screen when the display setup changed since it was last closed (DESIGN §9).
"""
from __future__ import annotations

from covas.window_state import DEFAULT, MIN_H, MIN_W, sanitize_geometry

# A single ordinary 1080p primary monitor.
ONE = [(0, 0, 1920, 1080)]
# Primary + a second monitor to its right.
TWO = [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]


def test_none_saved_returns_default():
    out = sanitize_geometry(None, ONE)
    assert out == DEFAULT
    assert out is not DEFAULT  # a copy, not the shared constant


def test_empty_dict_returns_default():
    assert sanitize_geometry({}, ONE) == DEFAULT


def test_missing_dimensions_returns_default():
    assert sanitize_geometry({"x": 10, "y": 10}, ONE) == DEFAULT


def test_valid_on_screen_geometry_unchanged():
    saved = {"x": 100, "y": 80, "width": 1200, "height": 820, "maximized": False}
    assert sanitize_geometry(saved, ONE) == saved


def test_oversized_clamped_to_screen():
    saved = {"x": 0, "y": 0, "width": 5000, "height": 4000, "maximized": False}
    out = sanitize_geometry(saved, ONE)
    assert out["width"] == 1920 and out["height"] == 1080


def test_undersized_clamped_up_to_minimum():
    saved = {"x": 100, "y": 100, "width": 300, "height": 200, "maximized": False}
    out = sanitize_geometry(saved, ONE)
    assert out["width"] == MIN_W and out["height"] == MIN_H


def test_far_negative_offscreen_recentered_on_primary():
    saved = {"x": -3000, "y": -3000, "width": 1200, "height": 820, "maximized": False}
    out = sanitize_geometry(saved, ONE)
    # Centered on the 1920x1080 primary.
    assert out["x"] == (1920 - 1200) // 2
    assert out["y"] == (1080 - 820) // 2


def test_beyond_shrunk_single_screen_recentered():
    # Window was placed on a wide desktop; now only a small 1280x720 screen remains.
    small = [(0, 0, 1280, 720)]
    saved = {"x": 2500, "y": 300, "width": 1200, "height": 700, "maximized": False}
    out = sanitize_geometry(saved, small)
    assert out["x"] == (1280 - 1200) // 2
    assert out["y"] == (720 - 700) // 2


def test_title_bar_off_top_recentered():
    # Body overlaps the screen but the title bar sits above y=0 -> not grabbable -> recenter.
    saved = {"x": 200, "y": -100, "width": 1200, "height": 820, "maximized": False}
    out = sanitize_geometry(saved, ONE)
    assert out["y"] == (1080 - 820) // 2
    assert out["x"] == (1920 - 1200) // 2


def test_mostly_offside_recentered():
    # Only ~50px of width remains on-screen (< MIN_VISIBLE) -> unreachable -> recenter.
    saved = {"x": 1870, "y": 100, "width": 1200, "height": 820, "maximized": False}
    out = sanitize_geometry(saved, ONE)
    assert out["x"] == (1920 - 1200) // 2


def test_maximized_flag_preserved_when_recentering():
    saved = {"x": -5000, "y": -5000, "width": 1200, "height": 820, "maximized": True}
    out = sanitize_geometry(saved, ONE)
    assert out["maximized"] is True


def test_maximized_flag_preserved_when_unchanged():
    saved = {"x": 100, "y": 80, "width": 1200, "height": 820, "maximized": True}
    out = sanitize_geometry(saved, ONE)
    assert out["maximized"] is True and out["x"] == 100


def test_empty_screens_uses_1080p_fallback():
    saved = {"x": -9000, "y": -9000, "width": 1200, "height": 820, "maximized": False}
    out = sanitize_geometry(saved, [])
    # Recentered on the 1920x1080 fallback.
    assert out["x"] == (1920 - 1200) // 2
    assert out["y"] == (1080 - 820) // 2


def test_valid_on_second_monitor_that_still_exists_unchanged():
    saved = {"x": 2100, "y": 100, "width": 1200, "height": 820, "maximized": False}
    assert sanitize_geometry(saved, TWO) == saved


def test_valid_on_second_monitor_that_vanished_recentered():
    # Geometry was on the now-removed right monitor; only the primary remains -> recenter on it.
    saved = {"x": 2100, "y": 100, "width": 1200, "height": 820, "maximized": False}
    out = sanitize_geometry(saved, ONE)
    assert out["x"] == (1920 - 1200) // 2
    assert out["y"] == (1080 - 820) // 2


def test_returns_new_dict_not_mutating_input():
    saved = {"x": -3000, "y": -3000, "width": 1200, "height": 820, "maximized": False}
    out = sanitize_geometry(saved, ONE)
    assert out is not saved
    assert saved["x"] == -3000  # input untouched
