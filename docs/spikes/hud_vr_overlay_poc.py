"""THROWAWAY spike CODE SKETCH (issue #46) — SteamVR in-headset overlay.

NOT shipped code, and NOT run in the spike environment (no VR hardware, no SteamVR here).
Lives under docs/spikes/, outside covas/, on purpose. This is the minimal path for Doug to
de-risk the VR sub-issue of epic #40 on his machine.

It stands up a SteamVR overlay from a RAW RGBA BUFFER — the key finding of the spike:
`IVROverlay.setOverlayRaw` uploads pixels straight from system memory, so NO DirectX/OpenGL
context is needed and the whole thing is doable in pure Python.

------------------------------------------------------------------------------------------
DO NOT add `openvr` to requirements.txt for this spike. To try it, in a SCRATCH venv:

    pip install openvr numpy
    # start SteamVR, then:
    python docs\\spikes\\hud_vr_overlay_poc.py

Expected: a colored test panel floats ~1.5 m in front of you in the headset. Stop SteamVR
and re-run -> it fails soft ("SteamVR not available"), no traceback. Then launch Elite
Dangerous in VR (SteamVR) and run this alongside to confirm it composites over the cockpit.
------------------------------------------------------------------------------------------

Why this path (see docs/spikes/hud-spike-46.md for the full writeup):
  * ED renders VR natively through OpenVR/SteamVR -> a SteamVR overlay composites over it
    directly, no wrapper.
  * `pyopenvr` (pip install openvr, BSD-3) is a mature ctypes binding exposing IVROverlay.
  * `XR_EXTX_overlay` (the "true OpenXR overlay") is unsupported by every shipping runtime,
    so it is NOT an option today.

The real feature would feed the SAME offscreen RGBA buffer that the 2D renderer produces
(one renderer, two sinks), keep the overlay handle on a HudCapability, and fail soft exactly
like the guard below when SteamVR is absent.
"""

from __future__ import annotations

import ctypes
import time

# NOTE: `openvr` is intentionally NOT a project dependency. Import lazily so this file
# byte-compiles and imports cleanly in the spike environment (where openvr is absent).
try:
    import numpy as np
except Exception:  # pragma: no cover - spike-only guard
    np = None


HUD_W, HUD_H = 512, 320  # overlay texture size in pixels


def _render_demo_rgba():
    """Build a demo HUD as an HxWx4 uint8 RGBA buffer.

    This stands in for the real HudRenderer. In the shipped feature this is the exact same
    buffer the 2D tkinter/Pillow sink draws — rendered once, consumed twice. For the sketch
    we just paint a panel + an accent bar so there is something legible to place in-headset.
    A real renderer would use Pillow's ImageDraw (a candidate new dep) or render an offscreen
    widget/HTML view to pixels; text rasterization is the one piece this sketch omits.
    """
    assert np is not None, "pip install numpy"
    buf = np.zeros((HUD_H, HUD_W, 4), dtype=np.uint8)
    # Near-black translucent panel.
    buf[:, :] = (11, 15, 20, 220)
    # ED-orange top accent bar.
    buf[0:8, :] = (255, 113, 0, 255)
    # A crude fuel bar so placement/scale is judgeable in the headset.
    buf[120:150, 24:24 + 200] = (95, 211, 138, 255)
    return buf


def _run_overlay():
    import openvr  # lazy; only present in the scratch venv

    # Overlay app type: runs ALONGSIDE a VR game (unlike VRApplication_Scene). If SteamVR is
    # not running this raises -> caught by main() and reported as fail-soft.
    openvr.init(openvr.VRApplication_Overlay)
    try:
        overlay = openvr.IVROverlay()
        handle = overlay.createOverlay("covas.hud.spike", "COVAS++ HUD spike")

        overlay.setOverlayWidthInMeters(handle, 0.6)   # physical size of the quad
        overlay.setOverlayAlpha(handle, 0.9)

        # Park it ~1.5 m in front of the seated origin (absolute tracking space). A real HUD
        # would instead track the HMD or a cockpit-fixed anchor via setOverlayTransform*.
        transform = openvr.HmdMatrix34_t()
        # identity rotation, translate -1.5 m on Z (forward), +0.2 m up.
        m = [[1.0, 0.0, 0.0, 0.0],
             [0.0, 1.0, 0.0, 0.2],
             [0.0, 0.0, 1.0, -1.5]]
        for r in range(3):
            for c in range(4):
                transform.m[r][c] = m[r][c]
        overlay.setOverlayTransformAbsolute(
            handle, openvr.TrackingUniverseStanding, transform)

        overlay.showOverlay(handle)

        buf = _render_demo_rgba()
        # setOverlayRaw(handle, buffer, width, height, bytes_per_pixel=4). No DirectX texture.
        # The binding does byref(buffer), so this must be a ctypes object whose address IS the
        # pixels — NOT buf.ctypes.data (a plain int; byref() rejects it). This POC shipped the
        # int form and was never run against SteamVR (see the header), and #48 copied it, so the
        # real overlay raised on every repaint and never showed. See covas/capabilities/vr_hud.py.
        raw = (ctypes.c_ubyte * buf.nbytes).from_buffer(buf)
        overlay.setOverlayRaw(handle, raw, HUD_W, HUD_H, 4)

        print("Overlay up. It should now be visible in the headset. Ctrl+C to quit.")
        # A real feature re-uploads setOverlayRaw only when the HudModel changes (throttled).
        while True:
            time.sleep(0.5)
    finally:
        openvr.shutdown()


def main() -> int:
    if np is None:
        print("This sketch needs numpy: pip install numpy openvr")
        return 2
    try:
        import openvr  # noqa: F401
    except Exception:
        print("SteamVR/openvr not available: pip install openvr and start SteamVR.\n"
              "This is the expected 'fail-soft' branch the real HudCapability must also take "
              "when SteamVR is not running (no VR overlay; the 2D window still works).")
        return 0
    try:
        _run_overlay()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # broad on purpose — spike-only; the real code fails soft too
        print(f"SteamVR overlay unavailable / init failed ({exc!r}). "
              "Is SteamVR running? Failing soft, as the real feature would.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
