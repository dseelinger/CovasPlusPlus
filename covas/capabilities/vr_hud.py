"""VR companion HUD — the SAME simplified HUD (issue #47) as a true in-headset SteamVR
overlay (issue #48, epic #40).

Only the RENDERING SURFACE differs from the 2D HUD: both are VIEWS over the SAME pure
``HudModel`` adapter, which folds the EventBus + checklist + route into an immutable
``HudSnapshot`` of the four display fields (voice-loop state / current checklist step / route
progress / last proactive callout). This module adds a *second sink* that rasterizes that
snapshot to an RGBA buffer and hands it to SteamVR's ``IVROverlay``, so the panel floats in
the cockpit instead of sitting on the desktop — exactly the "one renderer, two sinks" shape
the #46 spike recommended (`docs/spikes/hud-spike-46.md`).

The design mirrors the 2D HUD's "pure core + guarded I/O" discipline so the default
``pytest`` run exercises it offline, headless, and WITHOUT a VR runtime:

  * ``render_snapshot_rgba`` — the PURE rasterizer. It paints a ``HudSnapshot`` onto an
    HxWx4 uint8 RGBA buffer using a built-in 5x7 bitmap font (**zero new dependency** — only
    numpy, already required for audio). Fully unit-testable: no ``openvr``, no GPU, no display.
  * ``VrPlacement`` / ``resolve_transform`` — PURE placement math turning a placement choice
    (head-locked follows the view, world-locked is cockpit-fixed) plus a physical size into an
    OpenVR 3x4 transform. Unit-testable; no runtime touched.
  * ``VrHudView`` — the thin sink. It **lazily** imports ``openvr`` and initialises SteamVR
    only when the VR HUD is enabled; ANY import / init / runtime failure returns ``None`` or
    no-ops, so a machine with no VR runtime (including all of CI) never touches ``openvr`` and
    the voice loop never crashes.
  * ``make_vr_view`` — the single guarded constructor. Returns ``None`` when ``openvr`` or
    SteamVR is absent, exactly how the 2D HUD's ``make_view`` returns ``None`` with no
    tkinter/display.

``openvr`` (the ``pyopenvr`` binding) is an **optional** dependency: the app and the default
test suite run fine without it installed. Off by default (``[hud].vr_enabled = false``); a
shown overlay repaints only when the snapshot changes, so it is cheap next to the game.
"""
from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

from .hud_capability import HudSnapshot

# ---- built-in 5x7 bitmap font (pure data, no dependency) ----------------------------------
#
# One glyph per entry: seven rows of five columns, '1' = lit pixel, rows separated by '/'.
# Uppercase-only by design — a VR overlay reads best in caps, and folding to caps keeps the
# glyph table small. Any character absent from the table renders as blank (a space-width gap),
# so arbitrary game/checklist text degrades gracefully rather than raising.
_FONT: dict[str, str] = {
    " ": "00000/00000/00000/00000/00000/00000/00000",
    "A": "01110/10001/10001/11111/10001/10001/10001",
    "B": "11110/10001/10001/11110/10001/10001/11110",
    "C": "01111/10000/10000/10000/10000/10000/01111",
    "D": "11110/10001/10001/10001/10001/10001/11110",
    "E": "11111/10000/10000/11110/10000/10000/11111",
    "F": "11111/10000/10000/11110/10000/10000/10000",
    "G": "01111/10000/10000/10111/10001/10001/01111",
    "H": "10001/10001/10001/11111/10001/10001/10001",
    "I": "11111/00100/00100/00100/00100/00100/11111",
    "J": "00111/00010/00010/00010/10010/10010/01100",
    "K": "10001/10010/10100/11000/10100/10010/10001",
    "L": "10000/10000/10000/10000/10000/10000/11111",
    "M": "10001/11011/10101/10101/10001/10001/10001",
    "N": "10001/10001/11001/10101/10011/10001/10001",
    "O": "01110/10001/10001/10001/10001/10001/01110",
    "P": "11110/10001/10001/11110/10000/10000/10000",
    "Q": "01110/10001/10001/10001/10101/10010/01101",
    "R": "11110/10001/10001/11110/10100/10010/10001",
    "S": "01111/10000/10000/01110/00001/00001/11110",
    "T": "11111/00100/00100/00100/00100/00100/00100",
    "U": "10001/10001/10001/10001/10001/10001/01110",
    "V": "10001/10001/10001/10001/10001/01010/00100",
    "W": "10001/10001/10001/10101/10101/11011/10001",
    "X": "10001/10001/01010/00100/01010/10001/10001",
    "Y": "10001/10001/01010/00100/00100/00100/00100",
    "Z": "11111/00001/00010/00100/01000/10000/11111",
    "0": "01110/10001/10011/10101/11001/10001/01110",
    "1": "00100/01100/00100/00100/00100/00100/11111",
    "2": "01110/10001/00001/00110/01000/10000/11111",
    "3": "11111/00010/00100/00010/00001/10001/01110",
    "4": "00010/00110/01010/10010/11111/00010/00010",
    "5": "11111/10000/11110/00001/00001/10001/01110",
    "6": "00110/01000/10000/11110/10001/10001/01110",
    "7": "11111/00001/00010/00100/01000/01000/01000",
    "8": "01110/10001/10001/01110/10001/10001/01110",
    "9": "01110/10001/10001/01111/00001/00010/01100",
    ".": "00000/00000/00000/00000/00000/00110/00110",
    ",": "00000/00000/00000/00000/00110/00110/01000",
    ":": "00000/00110/00110/00000/00110/00110/00000",
    "-": "00000/00000/00000/11111/00000/00000/00000",
    "/": "00001/00001/00010/00100/01000/10000/10000",
    "(": "00110/01000/10000/10000/10000/01000/00110",
    ")": "01100/00010/00001/00001/00001/00010/01100",
    '"': "01010/01010/01010/00000/00000/00000/00000",
    "'": "00100/00100/00100/00000/00000/00000/00000",
    "!": "00100/00100/00100/00100/00100/00000/00100",
    "?": "01110/10001/00001/00110/00100/00000/00100",
    "%": "11000/11001/00010/00100/01000/10011/00011",
    "+": "00000/00100/00100/11111/00100/00100/00000",
    "=": "00000/00000/11111/00000/11111/00000/00000",
    "_": "00000/00000/00000/00000/00000/00000/11111",
    ">": "10000/01000/00100/00010/00100/01000/10000",
    "<": "00001/00010/00100/01000/00100/00010/00001",
    "#": "01010/01010/11111/01010/11111/01010/01010",
    "*": "00000/00100/10101/01110/10101/00100/00000",
}

_GLYPH_W, _GLYPH_H = 5, 7  # cell size, before scaling
# Unicode decorations the snapshot/game text may carry, folded to ASCII the font covers.
_UNICODE_FOLD = {
    "·": "-",   # middle dot (route "  ·  next scoopable")
    "–": "-", "—": "-",           # en/em dash
    "‘": "'", "’": "'",           # curly single quotes
    "“": '"', "”": '"',           # curly double quotes
    "…": "...",                         # ellipsis
    "●": "*", "▸": ">", "⤳": ">",  # ●, ▸, ⤳ (2D HUD row markers)
}

# Colors (RGBA) — an ED-flavoured, glanceable palette matching the 2D panel.
_BG = (11, 15, 20, 220)          # near-black translucent panel
_ACCENT = (255, 113, 0, 255)     # ED-orange top bar
_C_STATE = (0, 229, 229, 255)    # cyan — the loop-state headline
_C_CHECK = (224, 224, 224, 255)
_C_ROUTE = (200, 200, 200, 255)
_C_CALL = (154, 160, 166, 255)


def _fold_ascii(text: str) -> str:
    """Uppercase ``text`` and fold the handful of unicode decorations to ASCII the font
    covers. Characters still outside the font are handled at draw time (blank cell), so this
    never raises on arbitrary game text."""
    for uni, ascii_ in _UNICODE_FOLD.items():
        text = text.replace(uni, ascii_)
    return text.upper()


def _draw_char(buf: "np.ndarray", ch: str, x: int, y: int, color, scale: int) -> None:
    """Blit one glyph at pixel (x, y) into ``buf`` (HxWx4), scaled by ``scale``. Off-canvas
    pixels are clipped. Unknown characters draw nothing (a blank cell)."""
    rows = _FONT.get(ch)
    if not rows:
        return
    h, w = buf.shape[0], buf.shape[1]
    for ry, row in enumerate(rows.split("/")):
        for rx, bit in enumerate(row):
            if bit != "1":
                continue
            px0, py0 = x + rx * scale, y + ry * scale
            px1, py1 = min(px0 + scale, w), min(py0 + scale, h)
            if px0 >= w or py0 >= h or px1 <= 0 or py1 <= 0:
                continue
            buf[max(0, py0):py1, max(0, px0):px1] = color


def _draw_text(buf: "np.ndarray", text: str, x: int, y: int, color, scale: int,
               max_chars: int) -> None:
    """Draw a single (already ASCII-folded) line, truncating with a trailing '..' when it
    would exceed ``max_chars`` cells so a long line can't overrun the panel."""
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 2)] + ".."
    cell = (_GLYPH_W + 1) * scale  # one blank column between glyphs
    for i, ch in enumerate(text):
        _draw_char(buf, ch, x + i * cell, y, color, scale)


def _snapshot_lines(snap: HudSnapshot) -> List[tuple]:
    """The (text, color) rows to paint, in order. Mirrors the 2D view's rows: the voice-loop
    state is always shown as the headline; the checklist / route / callout rows appear only
    when populated, each prefixed so it reads without the 2D glyphs."""
    lines: List[tuple] = [(f"COVAS  {snap.voice_state}", _C_STATE)]
    if snap.checklist:
        lines.append((f"STEP  {snap.checklist}", _C_CHECK))
    if snap.route:
        lines.append((f"ROUTE  {snap.route}", _C_ROUTE))
    if snap.callout:
        lines.append((f'"{snap.callout}"', _C_CALL))
    return lines


def render_snapshot_rgba(snap: HudSnapshot, *, width: int = 768, height: int = 384,
                         scale: int = 3) -> "np.ndarray":
    """Rasterize a ``HudSnapshot`` to an HxWx4 uint8 **RGBA** buffer — the exact surface
    ``IVROverlay.setOverlayRaw`` uploads (RGBA from system memory, no DirectX context).

    Pure and deterministic: same snapshot in, same pixels out, with only numpy involved — so
    the default suite covers it offline (shape, background, and that lit text pixels appear).
    The 2D tkinter sink and this VR sink are two views over the same ``HudSnapshot``; this is
    simply the pixel view of it.
    """
    buf = np.zeros((height, width, 4), dtype=np.uint8)
    buf[:, :] = _BG
    accent = min(6 * scale // 3 or 4, height)  # thin top accent bar, scaled a little
    buf[0:accent, :] = _ACCENT

    pad = 6 * scale // 3 or 8
    cell_w = (_GLYPH_W + 1) * scale
    line_h = (_GLYPH_H + 2) * scale                 # glyph height + inter-line gap
    max_chars = max(1, (width - 2 * pad) // cell_w)  # glyph cells that fit across the panel

    y = accent + pad
    for text, color in _snapshot_lines(snap):
        if y + _GLYPH_H * scale > height:
            break                                    # ran out of vertical room; drop extras
        _draw_text(buf, _fold_ascii(text), pad, y, color, scale, max_chars)
        y += line_h
    return buf


def as_overlay_buffer(buf: "np.ndarray") -> "ctypes.Array":
    """Wrap the RGBA buffer as the ctypes object ``IVROverlay.setOverlayRaw`` needs.

    pyopenvr's binding does ``fn(handle, byref(buffer), w, h, bpp)`` — it calls ``byref()`` on
    whatever we hand it. So the argument must be a ctypes object **whose own address is the
    pixel data**. Two traps, both of which type-check and then fail:

      * ``buf.ctypes.data`` is a plain ``int`` (an address). ``byref()`` rejects it outright:
        *"byref() argument 1 must be _ctypes._CData, not int"*. This shipped in #48 (copied
        from the spike POC, which its own header says was never run against SteamVR), so every
        repaint raised and the overlay — created and shown correctly — never received a pixel.
        An overlay with no pixels is indistinguishable from no overlay at all.
      * ``data_as(POINTER(c_ubyte))`` / ``c_void_p(addr)`` DO satisfy ``byref()``, which makes
        them look like fixes — but ``byref()`` then yields a pointer to the *pointer variable*,
        not to the pixels, and SteamVR reads garbage. Silently wrong is worse than raising.

    ``from_buffer`` is the one correct form: it aliases the numpy memory in place (no copy), so
    ``addressof(result) == buf.ctypes.data``. Both invariants are asserted in the tests.
    """
    return (ctypes.c_ubyte * buf.nbytes).from_buffer(buf)


# ---- placement (pure OpenVR transform math) -----------------------------------------------

# Placement modes the setting accepts. "world" parks the panel in front of the seated origin
# (cockpit-fixed); "head" locks it to the HMD so it follows the view. Unknown -> "world".
VR_PLACEMENTS = ("world", "head")


@dataclass(frozen=True)
class VrPlacement:
    """A comfortable, non-interactive placement for the overlay quad. Pure data — the numbers
    are turned into an OpenVR transform by ``resolve_transform`` and applied by the view."""
    mode: str = "world"       # "world" (cockpit-fixed) or "head" (locked to the view)
    width_m: float = 0.55     # physical width of the quad in metres
    forward_m: float = 1.30   # distance the panel sits in front of the anchor
    up_m: float = -0.12       # vertical offset (negative = slightly below eye-line, glanceable)

    @staticmethod
    def normalize(mode: object, width_m: object = 0.55) -> "VrPlacement":
        """Build a placement from raw config values, clamping to sane, comfortable ranges so a
        bad setting can never place the panel somewhere unusable (or raise)."""
        m = str(mode or "world").strip().lower()
        if m not in VR_PLACEMENTS:
            m = "world"
        try:
            w = float(width_m)
        except (TypeError, ValueError):
            w = 0.55
        w = min(max(w, 0.15), 3.0)  # 15 cm .. 3 m
        return VrPlacement(mode=m, width_m=w)


def resolve_transform(p: VrPlacement) -> List[List[float]]:
    """The overlay's 3x4 row-major transform (identity rotation + a translation): centred,
    ``up_m`` above/below the anchor, ``forward_m`` in front (−Z is forward in OpenVR). For
    ``world`` this is relative to the seated origin; for ``head`` it is relative to the HMD —
    the matrix is the same, only the binding call differs (see ``VrHudView``)."""
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, float(p.up_m)],
        [0.0, 0.0, 1.0, -float(p.forward_m)],
    ]


# ---- the SteamVR sink (guarded — never imports openvr in the default test run) -------------


class VrHudView:
    """A thin SteamVR ``IVROverlay`` sink that renders a ``HudSnapshot`` in the headset.

    Threading model mirrors the 2D ``HudView``: OpenVR init, the overlay handle, and every
    ``openvr`` call live on ONE dedicated daemon thread. The outside world only flips
    thread-safe ``show``/``hide``/``close`` flags; a poll on that thread reads them, and
    re-uploads the RGBA buffer (via ``setOverlayRaw``) only when the snapshot changes.

    Prefer ``make_vr_view()`` to construct one — it starts the thread and returns ``None`` if
    ``openvr`` is not installed or SteamVR is not running, which is how the capability stays
    fail-soft and how CI never touches a VR runtime.
    """

    POLL_S = 0.4           # repaint cadence — glanceable, not a game loop
    WIDTH_PX = 768         # overlay texture size (the raw RGBA buffer we upload)
    HEIGHT_PX = 384
    KEY = "covas.hud"      # unique overlay key within SteamVR
    NAME = "COVAS++ HUD"

    def __init__(self, snapshot_provider: Callable[[], HudSnapshot],
                 placement: Optional[VrPlacement] = None,
                 *, log: Optional[Callable[[str], None]] = None) -> None:
        self._provider = snapshot_provider
        self._placement = placement or VrPlacement()
        self._log = log
        self._lock = threading.Lock()
        self._visible = False
        self._closing = False
        self._ready = threading.Event()
        self._ok = False
        self._thread: Optional[threading.Thread] = None
        # Keep the uploaded pixels alive for OpenVR: `_raw` aliases `_buf`'s memory (see
        # `as_overlay_buffer`), so both must outlive the setOverlayRaw call.
        self._buf: Optional["np.ndarray"] = None
        self._raw: Optional["ctypes.Array"] = None

    # -- lifecycle ---------------------------------------------------------------------
    def start(self, timeout: float = 8.0) -> bool:
        """Spawn the OpenVR thread and wait until the overlay is up (or has failed). Returns
        True only if a live overlay exists — the caller treats False as "no VR surface here"."""
        self._thread = threading.Thread(target=self._run, name="hud-vr", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        return self._ok

    def show(self) -> None:
        with self._lock:
            self._visible = True

    def hide(self) -> None:
        with self._lock:
            self._visible = False

    def close(self) -> None:
        with self._lock:
            self._closing = True

    # -- OpenVR thread -----------------------------------------------------------------
    def _run(self) -> None:
        try:
            import openvr  # lazy + optional: absent on a non-VR box / in CI -> fail soft
        except Exception as e:  # noqa: BLE001 — no openvr installed -> no VR overlay
            self._logline(f"openvr unavailable: {e}")
            self._ready.set()
            return
        try:
            # VRApplication_Overlay runs ALONGSIDE a VR game; raises if SteamVR isn't running.
            openvr.init(openvr.VRApplication_Overlay)
        except Exception as e:  # noqa: BLE001 — SteamVR not running -> fail soft, 2D still works
            self._logline(f"SteamVR not available ({e}); no VR overlay.")
            self._ready.set()
            return
        try:
            overlay = openvr.IVROverlay()
            handle = overlay.createOverlay(self.KEY, self.NAME)
            overlay.setOverlayWidthInMeters(handle, float(self._placement.width_m))
            overlay.setOverlayAlpha(handle, 0.92)
            self._apply_transform(openvr, overlay, handle)
            self._ok = True
            self._ready.set()
            self._loop(overlay, handle)
        except Exception as e:  # noqa: BLE001 — any overlay error -> tear down, never crash
            self._logline(f"VR overlay failed: {e}")
            self._ok = False
            self._ready.set()
        finally:
            try:
                openvr.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def _apply_transform(self, openvr, overlay, handle) -> None:
        """Bind the placement transform. World-locked uses the seated origin
        (``setOverlayTransformAbsolute``); head-locked follows the HMD
        (``setOverlayTransformTrackedDeviceRelative`` on device 0)."""
        m = resolve_transform(self._placement)
        mat = openvr.HmdMatrix34_t()
        for r in range(3):
            for c in range(4):
                mat.m[r][c] = m[r][c]
        if self._placement.mode == "head":
            overlay.setOverlayTransformTrackedDeviceRelative(
                handle, openvr.k_unTrackedDeviceIndex_Hmd, mat)
        else:
            overlay.setOverlayTransformAbsolute(
                handle, openvr.TrackingUniverseSeated, mat)

    def _loop(self, overlay, handle) -> None:
        """Show/hide + repaint from the latest snapshot until closed. Re-uploads the RGBA
        buffer only when the snapshot changes, so a static HUD costs almost nothing."""
        last: Optional[HudSnapshot] = None
        shown = False
        while True:
            with self._lock:
                visible, closing = self._visible, self._closing
            if closing:
                break
            try:
                if visible:
                    if not shown:
                        overlay.showOverlay(handle)
                        shown = True
                    snap = self._provider()
                    if snap != last:
                        buf = render_snapshot_rgba(
                            snap, width=self.WIDTH_PX, height=self.HEIGHT_PX)
                        raw = as_overlay_buffer(buf)
                        # Hold BOTH across the upload: `raw` aliases `buf`'s memory, so letting
                        # either go while SteamVR reads would free the pixels underneath it.
                        self._buf, self._raw = buf, raw
                        overlay.setOverlayRaw(
                            handle, raw, self.WIDTH_PX, self.HEIGHT_PX, 4)
                        last = snap
                elif shown:
                    overlay.hideOverlay(handle)
                    shown = False
            except Exception as e:  # noqa: BLE001 — a repaint glitch must not kill the overlay
                self._logline(f"VR repaint error: {e}")
            time.sleep(self.POLL_S)
        try:
            overlay.destroyOverlay(handle)
        except Exception:  # noqa: BLE001
            pass

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def make_vr_view(snapshot_provider: Callable[[], HudSnapshot],
                 placement: Optional[VrPlacement] = None,
                 *, log: Optional[Callable[[str], None]] = None) -> Optional[VrHudView]:
    """Build and start a ``VrHudView``, or return ``None`` when ``openvr`` isn't installed or
    SteamVR isn't running (a non-VR box, CI). This is the single guarded entry point for the
    VR surface — the capability calls it only when the VR HUD is enabled, so the default test
    run never imports ``openvr`` or touches a runtime."""
    view = VrHudView(snapshot_provider, placement, log=log)
    if view.start():
        return view
    return None
