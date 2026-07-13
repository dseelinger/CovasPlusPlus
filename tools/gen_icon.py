"""Generate a PLACEHOLDER COVAS++ app icon (covas/assets/icons/covas.ico), stdlib only.

This is a stand-in until issue #4 delivers the real branded art — a dark rounded tile with the
app's accent-orange status dot (the same motif the control panel uses). Multi-resolution .ico so
Windows picks the right size for the exe, shortcuts, and installer. Regenerate with:

    .venv\\Scripts\\python.exe tools/gen_icon.py

No third-party deps (no Pillow): PNG frames are hand-encoded (zlib) and packed into an ICO
container. When the real icon lands, drop it at covas/assets/icons/covas.ico and delete this.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "covas" / "assets" / "icons" / "covas.ico"
SIZES = (16, 32, 48, 64, 128, 256)
SS = 3  # supersampling factor for anti-aliasing

TILE = (18, 24, 33)      # #121821 — panel dark
DOT = (255, 122, 26)     # #ff7a1a — accent orange


def _inside_round_rect(x: float, y: float, lo: float, hi: float, rr: float) -> bool:
    """Is (x, y) inside the square [lo, hi] with corner radius rr?"""
    if x < lo or x > hi or y < lo or y > hi:
        return False
    cx = min(max(x, lo + rr), hi - rr)
    cy = min(max(y, lo + rr), hi - rr)
    return (x - cx) ** 2 + (y - cy) ** 2 <= rr * rr


def _render_rgba(size: int) -> bytes:
    """Draw the icon at `size` px with SSxSS supersampled anti-aliasing -> raw RGBA bytes."""
    margin = size * 0.06
    lo, hi = margin, size - margin
    rr = size * 0.22
    cx = cy = size / 2.0
    cr = size * 0.30
    out = bytearray(size * size * 4)
    inv = 1.0 / (SS * SS)
    for py in range(size):
        for px in range(size):
            r = g = b = 0.0
            covered = 0
            for sy in range(SS):
                for sx in range(SS):
                    fx = px + (sx + 0.5) / SS
                    fy = py + (sy + 0.5) / SS
                    if (fx - cx) ** 2 + (fy - cy) ** 2 <= cr * cr:
                        col = DOT
                    elif _inside_round_rect(fx, fy, lo, hi, rr):
                        col = TILE
                    else:
                        continue
                    r += col[0]
                    g += col[1]
                    b += col[2]
                    covered += 1
            i = (py * size + px) * 4
            if covered:
                out[i] = round(r / covered)
                out[i + 1] = round(g / covered)
                out[i + 2] = round(b / covered)
                out[i + 3] = round(255 * covered * inv)
            # else: leave transparent (0,0,0,0)
    return bytes(out)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _png(size: int, rgba: bytes) -> bytes:
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)   # 8-bit RGBA, no interlace
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)                                          # filter type 0 (none)
        raw.extend(rgba[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b""))


def main() -> None:
    frames = [(s, _png(s, _render_rgba(s))) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(frames))
    entries = bytearray()
    blob = bytearray()
    offset = 6 + 16 * len(frames)
    for size, png in frames:
        dim = 0 if size >= 256 else size                       # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blob += png
        offset += len(png)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(header + bytes(entries) + bytes(blob))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()
