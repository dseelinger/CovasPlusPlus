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
from dataclasses import dataclass, replace
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


# ---- proportional text via Pillow / Segoe UI (crisp) + bitmap fallback (zero-dep) ---------
#
# The 5x7 bitmap above reads "1980s" in a headset — it's block-scaled and uppercase-only. The
# preferred path renders real anti-aliased **Segoe UI**, the same family the 2D HUD uses, so the
# two surfaces match and mixed-case human text reads naturally. Segoe UI ships on every Windows
# box (the only platform this app targets); if Pillow or the font is somehow absent we fall back
# to the bitmap, so the overlay never fails to draw.

_HEAD_SIZE, _DETAIL_SIZE, _CALLOUT_SIZE = 34, 26, 24  # per-row font size (px)
_PAD_PX = 14        # inner margin
_ACCENT_PX = 6      # top accent-bar height
_ROW_GAP_PX = 10    # gap between rows

_font_cache: dict = {}
_pil_ok: Optional[bool] = None  # tri-state: None untried, then True/False (avoid re-probing)


def _load_font(size: int, bold: bool):
    """A Pillow ImageFont for Segoe UI at ``size`` (bold optional), or ``None`` when Pillow or
    the font isn't available — the caller then uses the bitmap fallback. Cached; never raises."""
    global _pil_ok
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    try:
        from PIL import ImageFont
    except Exception:  # noqa: BLE001 — no Pillow -> bitmap fallback
        _pil_ok = False
        return None
    import os
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = (["segoeuib.ttf", "seguisb.ttf"] if bold else []) + ["segoeui.ttf"]
    for name in names:
        try:
            f = ImageFont.truetype(os.path.join(fonts, name), size)
            _font_cache[key] = f
            _pil_ok = True
            return f
        except Exception:  # noqa: BLE001 — try the next candidate font
            continue
    _pil_ok = False
    _font_cache[key] = None
    return None


def _snapshot_rows(snap: HudSnapshot) -> List[tuple]:
    """The rows to paint as ``(text, rgba, size_px, bold)``, in order — the SINGLE source shared
    by both renderers so they can't drift. State is always the headline; checklist / route /
    callout appear only when populated. Mixed-case human text (the bitmap path folds to caps)."""
    rows: List[tuple] = [(f"COVAS  {snap.voice_state}", _C_STATE, _HEAD_SIZE, True)]
    if snap.checklist:
        rows.append((f"STEP   {snap.checklist}", _C_CHECK, _DETAIL_SIZE, False))
    if snap.route:
        rows.append((f"ROUTE  {snap.route}", _C_ROUTE, _DETAIL_SIZE, False))
    if snap.callout:
        rows.append((f"“{snap.callout}”", _C_CALL, _CALLOUT_SIZE, False))
    return rows


def _fit_width(draw, text: str, font, max_px: int) -> str:
    """Trim ``text`` with a trailing ellipsis until it fits ``max_px`` so a long system name or
    callout can't overrun the panel. Proportional text fits far more than the old fixed-width
    bitmap, so this rarely fires."""
    if draw.textlength(text, font=font) <= max_px:
        return text
    ell = "…"
    while text and draw.textlength(text + ell, font=font) > max_px:
        text = text[:-1]
    return (text + ell) if text else ell


def content_height(snap: HudSnapshot, *, width: int = 768) -> int:
    """Panel height (px) that exactly fits the current rows, so the VR sink can size the overlay
    to its content instead of a fixed box that's mostly empty. Uses Pillow font metrics when
    available, else a bitmap-based estimate."""
    rows = _snapshot_rows(snap)
    total = _ACCENT_PX + _PAD_PX // 2
    for _text, _rgba, size, bold in rows:
        f = _load_font(size, bold)
        if f is None:  # bitmap fallback: fixed 5x7 glyphs at scale 3
            return _ACCENT_PX + _PAD_PX + len(rows) * ((_GLYPH_H + 2) * 3) + _PAD_PX
        asc, desc = f.getmetrics()
        total += asc + desc + _ROW_GAP_PX
    return total + _PAD_PX // 2


def _render_pillow(snap: HudSnapshot, width: int, height: int):
    """Render the rows with anti-aliased Segoe UI onto a fixed HxWx4 RGBA canvas, or ``None``
    when Pillow / the font is unavailable (caller falls back to the bitmap renderer)."""
    if _load_font(_DETAIL_SIZE, False) is None:
        return None
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (width, height), _BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, _ACCENT_PX], fill=_ACCENT)
    y = _ACCENT_PX + _PAD_PX // 2
    for text, rgba, size, bold in _snapshot_rows(snap):
        font = _load_font(size, bold)
        if font is None:
            return None
        asc, desc = font.getmetrics()
        if y + asc + desc > height:
            break  # out of vertical room on this canvas
        d.text((_PAD_PX, y), _fit_width(d, text, font, width - 2 * _PAD_PX), font=font, fill=rgba)
        y += asc + desc + _ROW_GAP_PX
    # np.array (not asarray) -> a WRITABLE, C-contiguous copy, which as_overlay_buffer's
    # from_buffer() requires. asarray can hand back a read-only view that from_buffer rejects.
    return np.array(img, dtype=np.uint8)


def _render_bitmap(snap: HudSnapshot, width: int, height: int, scale: int) -> "np.ndarray":
    """Fail-soft fallback: the original 5x7 bitmap renderer, used only when Pillow / Segoe UI is
    absent. Uppercase, fixed-width, chunky — but zero-dependency, so the overlay still draws."""
    buf = np.zeros((height, width, 4), dtype=np.uint8)
    buf[:, :] = _BG
    accent = min(6 * scale // 3 or 4, height)  # thin top accent bar, scaled a little
    buf[0:accent, :] = _ACCENT

    pad = 6 * scale // 3 or 8
    cell_w = (_GLYPH_W + 1) * scale
    line_h = (_GLYPH_H + 2) * scale                 # glyph height + inter-line gap
    max_chars = max(1, (width - 2 * pad) // cell_w)  # glyph cells that fit across the panel

    y = accent + pad
    for text, color, _size, _bold in _snapshot_rows(snap):
        if y + _GLYPH_H * scale > height:
            break                                    # ran out of vertical room; drop extras
        _draw_text(buf, _fold_ascii(text), pad, y, color, scale, max_chars)
        y += line_h
    return buf


def render_snapshot_rgba(snap: HudSnapshot, *, width: int = 768, height: int = 384,
                         scale: int = 3) -> "np.ndarray":
    """Rasterize a ``HudSnapshot`` to an HxWx4 uint8 **RGBA** buffer — the exact surface
    ``IVROverlay.setOverlayRaw`` uploads (RGBA from system memory, no DirectX context).

    Prefers crisp anti-aliased Segoe UI (``_render_pillow``); falls back to the zero-dependency
    5x7 bitmap (``_render_bitmap``) when Pillow / the font is absent. Either path returns exactly
    ``(height, width, 4)`` and is deterministic, so the default suite covers it offline. The 2D
    tkinter sink and this VR sink are two views over the same ``HudSnapshot``.
    """
    out = _render_pillow(snap, width, height)
    if out is not None:
        return out
    return _render_bitmap(snap, width, height, scale)


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
    become an OpenVR transform (``resolve_transform``) plus a curvature the view applies. Every
    field is voice-adjustable live (see the ``[hud].vr_*`` settings); defaults are the seated,
    slightly-below-eye-line spot that reads well at a glance."""
    mode: str = "world"        # "world" (cockpit-fixed) or "head" (locked to the view)
    width_m: float = 0.55      # physical width of the quad in metres
    forward_m: float = 1.30    # distance the panel sits in front of the anchor
    up_m: float = -0.12        # vertical offset (negative = below eye-line, glanceable)
    offset_x_m: float = 0.0    # lateral offset (positive = to the right)
    pitch_deg: float = 0.0     # tilt; positive = top leans TOWARD you (good when placed low)
    curvature: float = 0.0     # 0 = flat, 1 = full cylinder; a gentle ED-style wrap is ~0.05–0.1
    yaw_deg: float = 0.0       # heading; 0 = straight ahead. Set by "pin the HUD here" (look-to-
                               # place) to the direction you're facing, so the panel swings to your
                               # gaze. The forward/lateral offsets are applied in THIS yawed frame.

    @staticmethod
    def normalize(mode: object, width_m: object = 0.55, *, forward_m: object = 1.30,
                  up_m: object = -0.12, offset_x_m: object = 0.0, pitch_deg: object = 0.0,
                  curvature: object = 0.0, yaw_deg: object = 0.0) -> "VrPlacement":
        """Build a placement from raw config values, clamping each to a sane, comfortable range
        so a bad setting can never place the panel somewhere unusable (or raise)."""
        m = str(mode or "world").strip().lower()
        if m not in VR_PLACEMENTS:
            m = "world"

        def _clamp(v: object, default: float, lo: float, hi: float) -> float:
            try:
                return min(max(float(v), lo), hi)
            except (TypeError, ValueError):
                return default

        def _wrap(v: object, default: float) -> float:
            try:
                return ((float(v) + 180.0) % 360.0) - 180.0  # wrap to (−180, 180]
            except (TypeError, ValueError):
                return default

        return VrPlacement(
            mode=m,
            width_m=_clamp(width_m, 0.55, 0.15, 3.0),        # 15 cm .. 3 m
            forward_m=_clamp(forward_m, 1.30, 0.30, 5.0),    # 30 cm .. 5 m in front
            up_m=_clamp(up_m, -0.12, -2.0, 2.0),             # ±2 m vertical
            offset_x_m=_clamp(offset_x_m, 0.0, -2.0, 2.0),   # ±2 m lateral
            pitch_deg=_clamp(pitch_deg, 0.0, -60.0, 60.0),   # ±60° tilt
            curvature=_clamp(curvature, 0.0, 0.0, 1.0),      # flat .. full cylinder
            yaw_deg=_wrap(yaw_deg, 0.0),                       # heading, wrapped
        )


def _mul_3x4(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    """Compose two 3x4 rigid transforms (each an ``[R | t]`` with an implicit ``[0 0 0 1]``
    bottom row): returns ``a ∘ b``. Pure, so the placement math is unit-tested offline."""
    out = [[0.0, 0.0, 0.0, 0.0] for _ in range(3)]
    for i in range(3):
        for j in range(3):
            out[i][j] = sum(a[i][k] * b[k][j] for k in range(3))
        out[i][3] = sum(a[i][k] * b[k][3] for k in range(3)) + a[i][3]
    return out


def hmd_yaw_deg(matrix: List[List[float]]) -> float:
    """The heading (degrees) to give ``VrPlacement.yaw_deg`` so a panel placed by look-to-place
    sits along the HMD's horizontal gaze and faces back at the viewer. ``matrix`` is the HMD's
    3x4 device-to-absolute pose. Pure — unit-tested against known rotations."""
    import math
    return math.degrees(math.atan2(matrix[0][2], matrix[2][2]))


def resolve_transform(p: VrPlacement) -> List[List[float]]:
    """The overlay's 3x4 row-major transform: a heading rotation ``Ry(yaw)`` composed with the
    panel's local pose — an X-axis ``pitch`` tilt plus a translation to ``(offset_x, up,
    −forward)`` (lateral, vertical, and ``forward`` in front; −Z is forward in OpenVR). So the
    offsets act in the yawed frame: after "pin here" swings the panel to your gaze, "left" /
    "closer" still move it sensibly relative to that facing. Positive ``pitch_deg`` leans the
    top toward the viewer (a low panel angles up to face you). At ``yaw=pitch=0`` the rotation
    is identity. For ``world`` this is relative to the seated origin; for ``head`` relative to
    the HMD — the matrix is the same, only the binding call differs (see ``VrHudView``)."""
    import math
    py = math.radians(float(p.yaw_deg))
    cy, sy = math.cos(py), math.sin(py)
    px = math.radians(float(p.pitch_deg))
    c, s = math.cos(px), math.sin(px)
    ry = [[cy, 0.0, sy, 0.0], [0.0, 1.0, 0.0, 0.0], [-sy, 0.0, cy, 0.0]]
    local = [
        [1.0, 0.0, 0.0, float(p.offset_x_m)],
        [0.0, c,  -s,   float(p.up_m)],
        [0.0, s,   c,  -float(p.forward_m)],
    ]
    return _mul_3x4(ry, local)


def _steamvr_running() -> bool:
    """True if the SteamVR runtime (``vrserver.exe``) is already up. We gate ``openvr.init`` on
    this because initialising as a ``VRApplication_Overlay`` app **launches SteamVR** when it
    isn't running — unwanted for a Commander on VDXR/OpenComposite or flat desktop who merely
    left the VR HUD enabled (SteamVR isn't their compositor, and the overlay can't render there
    anyway). So the HUD only ever *attaches* to a SteamVR that's already running, never starts
    one. Windows-only (the app's platform); any failure returns False — fail soft = don't
    launch. Cheap: a one-shot process query at enable time, not in the poll loop."""
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq vrserver.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW — no console flash from the frozen app
        ).stdout.lower()
        return "vrserver.exe" in out
    except Exception:  # noqa: BLE001 — tasklist missing / odd platform -> assume not running
        return False


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
        # A new placement pushed from outside (voice/Settings) — the OpenVR thread picks it up on
        # its next poll and re-applies live, so repositioning never needs a re-toggle.
        self._pending_placement: Optional[VrPlacement] = None
        # "Pin the HUD here" (look-to-place): the request is served on the OpenVR thread (HMD
        # pose reads must stay there), which computes the new placement and signals the waiter.
        self._pin_request = False
        self._pin_event: Optional[threading.Event] = None
        self._pin_result: Optional[VrPlacement] = None

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

    def set_placement(self, placement: VrPlacement) -> None:
        """Push a new placement (position / distance / pitch / curvature / width). The OpenVR
        thread applies it on its next poll, so a Settings change or voice command repositions the
        live overlay with no re-toggle. Thread-safe; a no-op if the overlay isn't up."""
        with self._lock:
            self._pending_placement = placement

    def pin_here(self, timeout: float = 1.5) -> Optional[VrPlacement]:
        """Look-to-place: swing the panel to the direction you're currently facing (the HMD
        heading), centred on your gaze, keeping distance/height/tilt/curvature. Returns the new
        placement (so the caller can persist it) or ``None`` if the overlay/HMD pose isn't
        available. The HMD pose is read on the OpenVR thread; this blocks briefly for the result."""
        with self._lock:
            self._pin_request = True
            self._pin_result = None
            ev = self._pin_event = threading.Event()
        ev.wait(timeout=timeout)
        with self._lock:
            return self._pin_result

    # -- OpenVR thread -----------------------------------------------------------------
    def _run(self) -> None:
        try:
            import openvr  # lazy + optional: absent on a non-VR box / in CI -> fail soft
        except Exception as e:  # noqa: BLE001 — no openvr installed -> no VR overlay
            self._logline(f"openvr unavailable: {e}")
            self._ready.set()
            return
        # ATTACH-ONLY: init(VRApplication_Overlay) would LAUNCH SteamVR if it isn't running, so
        # gate on the runtime already being up. Enabling the VR HUD is then a no-op until the
        # Commander is actually in SteamVR — it never drags SteamVR up under VDXR/desktop.
        if not _steamvr_running():
            self._logline("SteamVR not running; VR overlay stays off (won't launch SteamVR).")
            self._ready.set()
            return
        try:
            # SteamVR is up — attach ALONGSIDE the VR game as an overlay app.
            openvr.init(openvr.VRApplication_Overlay)
        except Exception as e:  # noqa: BLE001 — attach failed despite SteamVR up -> fail soft
            self._logline(f"SteamVR not available ({e}); no VR overlay.")
            self._ready.set()
            return
        try:
            overlay = openvr.IVROverlay()
            handle = overlay.createOverlay(self.KEY, self.NAME)
            overlay.setOverlayAlpha(handle, 0.92)
            self._apply_placement(openvr, overlay, handle, self._placement)
            self._ok = True
            self._ready.set()
            self._loop(openvr, overlay, handle)
        except Exception as e:  # noqa: BLE001 — any overlay error -> tear down, never crash
            self._logline(f"VR overlay failed: {e}")
            self._ok = False
            self._ready.set()
        finally:
            try:
                openvr.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def _apply_placement(self, openvr, overlay, handle, p: "VrPlacement") -> None:
        """Apply an entire placement to the live overlay: physical width, the pose transform
        (position + pitch), and curvature. Used both at setup and for live re-apply, so a
        Settings/voice change repositions without recreating the overlay. Curvature is
        best-effort — an older runtime without ``setOverlayCurvature`` just stays flat."""
        self._placement = p
        overlay.setOverlayWidthInMeters(handle, float(p.width_m))
        m = resolve_transform(p)
        mat = openvr.HmdMatrix34_t()
        for r in range(3):
            for c in range(4):
                mat.m[r][c] = m[r][c]
        if p.mode == "head":
            overlay.setOverlayTransformTrackedDeviceRelative(
                handle, openvr.k_unTrackedDeviceIndex_Hmd, mat)
        else:
            overlay.setOverlayTransformAbsolute(
                handle, openvr.TrackingUniverseSeated, mat)
        try:
            overlay.setOverlayCurvature(handle, float(p.curvature))
        except Exception as e:  # noqa: BLE001 — old runtime w/o curvature -> flat, not fatal
            self._logline(f"VR curvature unavailable (flat): {e}")

    def _pin_to_gaze(self, openvr, overlay, handle) -> Optional["VrPlacement"]:
        """Read the HMD pose and swing the panel to that heading, centred on the gaze. Returns
        the new placement (also applied to the live overlay) or ``None`` if the HMD pose isn't
        valid yet. Runs on the OpenVR thread (all HMD reads do)."""
        system = openvr.VRSystem()
        poses = system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseSeated, 0, openvr.k_unMaxTrackedDeviceCount)
        hmd = poses[openvr.k_unTrackedDeviceIndex_Hmd]
        if not hmd.bPoseIsValid:
            return None
        m = hmd.mDeviceToAbsoluteTracking
        mat = [[float(m[r][c]) for c in range(4)] for r in range(3)]
        # New heading = where you're looking; recentre laterally so it lands on your gaze.
        pinned = replace(self._placement, yaw_deg=hmd_yaw_deg(mat), offset_x_m=0.0)
        self._apply_placement(openvr, overlay, handle, pinned)
        return pinned

    def _loop(self, openvr, overlay, handle) -> None:
        """Show/hide + repaint from the latest snapshot until closed. Re-uploads the RGBA
        buffer only when the snapshot changes, so a static HUD costs almost nothing."""
        last: Optional[HudSnapshot] = None
        shown = False
        while True:
            with self._lock:
                visible, closing = self._visible, self._closing
                pending, self._pending_placement = self._pending_placement, None
                pin, self._pin_request = self._pin_request, False
                pin_ev = self._pin_event
            if closing:
                break
            if pending is not None:
                try:
                    self._apply_placement(openvr, overlay, handle, pending)
                except Exception as e:  # noqa: BLE001 — a bad reposition must not kill the overlay
                    self._logline(f"VR reposition error: {e}")
            if pin:
                result = None
                try:
                    result = self._pin_to_gaze(openvr, overlay, handle)
                except Exception as e:  # noqa: BLE001 — a pin glitch must not kill the overlay
                    self._logline(f"VR pin error: {e}")
                with self._lock:
                    self._pin_result = result
                if pin_ev is not None:
                    pin_ev.set()
            try:
                if visible:
                    if not shown:
                        overlay.showOverlay(handle)
                        shown = True
                    snap = self._provider()
                    if snap != last:
                        # Size the texture to the rows so the panel hugs its content instead of
                        # floating in a fixed box that's ~70% empty. Width is fixed (physical
                        # metres are set once); height follows, so the overlay's physical height
                        # tracks the row count via the texture aspect.
                        h = content_height(snap, width=self.WIDTH_PX)
                        buf = render_snapshot_rgba(snap, width=self.WIDTH_PX, height=h)
                        raw = as_overlay_buffer(buf)
                        # Hold BOTH across the upload: `raw` aliases `buf`'s memory, so letting
                        # either go while SteamVR reads would free the pixels underneath it.
                        self._buf, self._raw = buf, raw
                        overlay.setOverlayRaw(handle, raw, self.WIDTH_PX, h, 4)
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
