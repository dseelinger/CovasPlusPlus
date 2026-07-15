"""THROWAWAY spike PoC for issue #55 — screenshot capture of the ED window.

NOT part of the shipped `covas/` package. Adds NO runtime dependency (mss/Pillow
are dev-only here). This exists so Doug can eyeball, on his own machine, whether a
plain GDI grab of a *borderless* Elite Dangerous window (or the VR mirror) is
legible enough to hand to a vision-LLM. It has been byte-compiled only and has
NOT been run against a live game.

Why this shape (see docs/spikes/vision-spike-55.md):
  * On-demand ONLY — one grab per invocation, never a loop. A vision *loop* is the
    thing the spike explicitly rejects on cost grounds (~$5-$50/hour).
  * Downscale to ~1568px long edge before encode — that's the cheap-tier image-token
    sweet spot for Anthropic (~1600 tokens); we don't need HUD-pixel fidelity.
  * mss is the PoC path (pure ctypes, PyInstaller-clean). Windows Graphics Capture
    (WGC) is the recommended SHIPPING path for fullscreen-exclusive + VR-mirror
    robustness, but it pulls WinRT bindings and is out of scope for a throwaway.

Run (dev only):
    pip install mss pillow            # NOT added to requirements.txt
    python docs/spikes/screenshot_poc.py                 # primary monitor
    python docs/spikes/screenshot_poc.py --monitor 2     # second monitor
    python docs/spikes/screenshot_poc.py --long-edge 2576 # high-res (costlier)

Output: a JPEG under the scratch/ dir plus the estimated Anthropic image-token
count, so the cost math in the findings doc can be sanity-checked against a real
frame. FULLSCREEN-EXCLUSIVE ED will likely produce a BLACK frame via GDI — run ED
borderless/windowed for the PoC, or use WGC for the real thing.
"""
from __future__ import annotations

import argparse
import base64
import io
import sys
import time
from pathlib import Path


def _estimate_anthropic_image_tokens(width: int, height: int) -> int:
    """Anthropic's documented rule of thumb: tokens ~= (w * h) / 750.
    Used only to sanity-check the cost model against a real captured frame."""
    return round((width * height) / 750)


def capture(monitor_index: int, long_edge: int) -> tuple[bytes, int, int, int]:
    """Grab one frame, downscale so the long edge <= `long_edge`, return
    (jpeg_bytes, width, height, est_tokens). Single grab — never a loop."""
    try:
        import mss  # dev-only dep; not in requirements.txt
        from PIL import Image
    except ImportError as exc:  # fail soft with a clear message, mirroring the app
        print(f"[poc] missing dev dep: {exc}. `pip install mss pillow`", file=sys.stderr)
        raise SystemExit(2)

    with mss.mss() as sct:
        # sct.monitors[0] is the virtual "all monitors" bounding box; 1..N are real.
        mons = sct.monitors
        if monitor_index < 0 or monitor_index >= len(mons):
            print(f"[poc] monitor {monitor_index} out of range (have {len(mons)-1} "
                  f"real monitors; 0 = all)", file=sys.stderr)
            raise SystemExit(2)
        t0 = time.perf_counter()
        shot = sct.grab(mons[monitor_index])
        grab_ms = (time.perf_counter() - t0) * 1000.0

    img = Image.frombytes("RGB", shot.size, shot.rgb)
    w0, h0 = img.size
    # Downscale to the cheap-tier token target. Only shrink, never upscale.
    scale = min(1.0, long_edge / max(w0, h0))
    if scale < 1.0:
        img = img.resize((round(w0 * scale), round(h0 * scale)), Image.LANCZOS)
    w, h = img.size

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    jpeg = buf.getvalue()

    print(f"[poc] grabbed monitor {monitor_index} in {grab_ms:.1f} ms: "
          f"{w0}x{h0} -> {w}x{h}, {len(jpeg)/1024:.0f} KB JPEG")
    return jpeg, w, h, _estimate_anthropic_image_tokens(w, h)


def main() -> None:
    ap = argparse.ArgumentParser(description="Throwaway ED screenshot PoC (#55).")
    ap.add_argument("--monitor", type=int, default=1,
                    help="monitor index (1 = primary, 0 = all monitors)")
    ap.add_argument("--long-edge", type=int, default=1568,
                    help="downscale target for the long edge (1568 cheap, 2576 hi-res)")
    args = ap.parse_args()

    jpeg, w, h, tokens = capture(args.monitor, args.long_edge)

    out_dir = Path(__file__).resolve().parent / "scratch"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"ed_capture_{int(time.time())}.jpg"
    out_path.write_bytes(jpeg)

    # The exact content block a vision turn would attach (after the cached prefix).
    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    print(f"[poc] wrote {out_path}")
    print(f"[poc] frame {w}x{h}  ~{tokens} Anthropic image tokens  "
          f"(~${tokens * 1.0 / 1_000_000:.4f} at Haiku input $1/Mtok)")
    print(f"[poc] base64 length: {len(b64)} chars "
          f"(this is what rides in the image content block)")
    print("[poc] If the frame is BLACK, ED was likely fullscreen-exclusive — "
          "run it borderless/windowed, or use Windows Graphics Capture for ship.")


if __name__ == "__main__":
    main()
