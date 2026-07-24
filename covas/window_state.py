"""Native-window geometry persistence for the PACKAGED app (run_covas_app.py / PyWebView).

The frozen build shows a real OS window; users expect it to reopen where and how they left it
(position, size, maximized). We persist that to `<data_dir>/window_state.json` and, on the way
back in, SANITIZE it against the currently-connected displays: a monitor gets unplugged, a
smaller panel becomes primary, or the resolution shrinks, and last session's coordinates would
put the window off-screen with no title bar to grab. `sanitize_geometry` is the pure, unit-tested
heart of this — it clamps size to the visible desktop and recenters anything that isn't reachable.

Only the PyWebView glue in run_covas_app.py touches real Screen/Window objects; everything here is
plain data, so the interesting rules stay testable with no display, no PyWebView, and no I/O. The
file I/O helpers are best-effort: geometry is a nicety, never worth crashing launch or exit for, so
they swallow their errors and the caller falls back to the fixed 1200x820 default.
"""
from __future__ import annotations

import json
import logging
import pathlib

from covas import config

log = logging.getLogger(__name__)

# The fixed default the app has always opened at (x/y None = "let the sanitizer center it").
DEFAULT = {"x": None, "y": None, "width": 1200, "height": 820, "maximized": False}
# The smallest window we'll ever restore — mirrors create_window(min_size=(900, 640)).
MIN_W, MIN_H = 900, 640
# A window counts as reachable only if this much of its width AND its title-bar band overlap a
# screen — enough of the top edge to grab and drag. Below this we treat it as off-screen.
MIN_VISIBLE = 120
# Height of the draggable title-bar band we require to be on-screen (top ~40px of the window).
TITLE_BAR = 40
# Used when the platform reports no screens at all (should not happen, but never trust it).
_FALLBACK_SCREEN = (0, 0, 1920, 1080)


def state_path(cfg: dict) -> pathlib.Path:
    """`<data_dir>/window_state.json`. cfg is accepted for a consistent config-passing API even
    though data_dir() is global — keeps callers uniform with the rest of covas' state files."""
    return config.data_dir() / "window_state.json"


def load(cfg: dict) -> dict | None:
    """Read + parse the saved geometry. Returns None on anything wrong (missing file, bad JSON,
    unreadable) — the caller treats None exactly like "no saved state" and uses the default."""
    try:
        raw = state_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return None
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 — corrupt/unreadable state is never worth failing launch
        log.debug("window_state load failed; using default geometry", exc_info=True)
        return None


def save(cfg: dict, geom: dict) -> None:
    """Best-effort write of the current geometry. Swallows every error: persisting the window
    position must never interfere with a clean shutdown."""
    try:
        path = state_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(geom, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 — geometry is a nicety, not worth crashing exit
        log.debug("window_state save failed; skipping", exc_info=True)


def _overlaps_visibly(x: int, y: int, w: int, h: int, screen: tuple[int, int, int, int]) -> bool:
    """Does the window's title-bar band overlap this screen by a grabbable margin? We check the
    intersection of the window's TOP band (its title bar) with the screen rect, and require at
    least MIN_VISIBLE px horizontally and any of the band vertically — so a window shoved off the
    top edge, or 90% off a side, correctly reads as unreachable."""
    sx, sy, sw, sh = screen
    # Horizontal overlap of the window with the screen.
    ix = max(0, min(x + w, sx + sw) - max(x, sx))
    # Vertical overlap of the window's title-bar band (top TITLE_BAR px) with the screen.
    band_bottom = y + min(h, TITLE_BAR)
    iy = max(0, min(band_bottom, sy + sh) - max(y, sy))
    return ix >= MIN_VISIBLE and iy > 0


def sanitize_geometry(saved: dict | None, screens: list, default: dict = DEFAULT) -> dict:
    """PURE: fold a saved geometry against the connected `screens` into one that is guaranteed
    on-screen. `screens` is a list of `(x, y, w, h)` tuples (screens[0] is primary). Rules:

      * falsy/missing width|height           -> a copy of `default` (centered by None x/y)
      * clamp width/height into [MIN, largest screen dimension]
      * not visibly reachable (or x/y None)  -> recenter the clamped window on the primary screen
      * `maximized` is preserved throughout

    Returns a NEW dict {x, y, width, height, maximized}; never mutates its inputs.
    """
    if not saved or saved.get("width") is None or saved.get("height") is None:
        return dict(default)

    scr = list(screens) if screens else [_FALLBACK_SCREEN]
    primary = scr[0]
    # Largest single-screen dimensions bound the clamp so we never restore a window bigger than
    # any one display (a leftover size from a since-removed 4K monitor).
    max_w = max(s[2] for s in scr)
    max_h = max(s[3] for s in scr)

    width = max(MIN_W, min(int(saved["width"]), max_w))
    height = max(MIN_H, min(int(saved["height"]), max_h))
    maximized = bool(saved.get("maximized", False))

    x = saved.get("x")
    y = saved.get("y")

    reachable = (
        x is not None
        and y is not None
        and any(_overlaps_visibly(int(x), int(y), width, height, s) for s in scr)
    )
    if not reachable:
        # Recenter the (clamped) window on the primary screen.
        px, py, pw, ph = primary
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)

    return {"x": int(x), "y": int(y), "width": width, "height": height, "maximized": maximized}
