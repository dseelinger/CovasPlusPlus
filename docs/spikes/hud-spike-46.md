# HUD spike (issue #46) — OpenXR vs SteamVR vs 2D overlay

> **Correction banner (issue #103).** This is a **dated historical record**; two of its findings
> were later superseded and are corrected here, not rewritten below:
> 1. Its advice to capture the 2D window with **OVR Toolkit** on a non-SteamVR runtime is **wrong**
>    — OVR Toolkit is itself a SteamVR-only overlay app (a point this very spike's own sources note),
>    so it cannot help on OpenComposite/VDXR. Use the **web HUD** (`[hud].web_enabled` → OpenKneeboard
>    Web Dashboard on `/hud`), added in #103, for in-headset on non-SteamVR runtimes.
> 2. This spike **never evaluated VDXR / Virtual Desktop's native OpenXR** at all; its Quest matrix
>    treats "OpenComposite / native OpenXR" only as a perf tweak and misses that rig entirely. #103
>    covers it: the runtime-agnostic route is OpenKneeboard's in-process OpenXR **API layer**, which
>    we *reuse* (serve a transparent page) rather than build.

*Research spike. Produces a **decision**, not a shipped feature. Part of epic #40 (VR overlay
+ simplified HUD). No product commitment until this lands. Web facts verified 2026-07;
nothing here was run against VR hardware or Elite Dangerous in this environment — see
[What I could and could not verify](#what-i-could-and-could-not-verify).*

## Goal

A companion HUD available on two surfaces:

- **(a) 2D flat-screen overlay** — a transparent, always-on-top window for players on a
  monitor (and a fallback for VR players).
- **(b) true in-headset VR overlay** — the HUD floating in the cockpit for VR players,
  not just a 2D window they alt-tab to.

Content is COVAS++ state it already has: current system / station, fuel and status, the
active checklist item, and the last proactive callout — a *glanceable* companion panel,
not a full instrument suite.

---

## Executive summary (the recommendation)

- **2D surface — ship a first-party transparent always-on-top window using stdlib
  `tkinter`.** Zero new runtime dependency, trivial in the frozen PyInstaller build
  (it is in the standard library, already present), and Windows' `-transparentcolor`
  gives both transparency **and** click-through for free. Reserve a richer
  **PyWebView** variant (already bundled — it is the app's main window) as a follow-up if
  we want to reuse the existing Flask/HTML HUD markup, accepting that it is opaque and not
  click-through.
- **VR surface — ship a first-party SteamVR overlay via `pyopenvr` (`IVROverlay`),
  pushing an RGBA buffer with `SetOverlayRaw`.** It is the only in-headset path that is
  (1) feasible from pure Python, (2) actually supported by a shipping runtime today, and
  (3) native to how Elite Dangerous renders VR (ED speaks OpenVR/SteamVR natively). One new
  dependency (`openvr`, BSD-3, on PyPI), fail-soft when SteamVR is not running.
- **Drop the "true OpenXR overlay" (`XR_EXTX_overlay`) idea.** No shipping runtime
  implements it (SteamVR, Oculus/Meta, and every other compositor all report it
  unsupported); it exists only as a provisional LunarG API-layer reference (D3D11-only).
  It is a dead end for us today.
- **Meta Quest reach:** a SteamVR overlay reaches Quest players **when they run ED through
  SteamVR** (Quest Link / Air Link with SteamVR as the active runtime, or Virtual Desktop
  in its SteamVR mode). It does **not** reach a Quest player running ED on the native
  Oculus runtime or via OpenComposite/OpenXR. For those, we do **not** build a bespoke
  OpenXR-API-layer overlay (a large C++/D3D effort — see below); we document capturing the
  first-party **2D window** with a generic tool (OpenKneeboard / OVR Toolkit), which is
  exactly how the competition does it.

**One unified renderer feeds both surfaces.** Render the HUD once to an offscreen RGBA
buffer (numpy); the 2D window blits it and the SteamVR overlay uploads it via
`SetOverlayRaw`. Same layout code, two sinks. This keeps the VR sub-issue small and de-risks
both at once.

---

## Background: the three "VR overlay" mechanisms (they are not the same thing)

"OpenXR overlay" gets used loosely for three very different things. Untangling them is the
whole spike:

1. **`XR_EXTX_overlay` (the spec extension).** Lets a *separate* OpenXR application submit
   composition layers on top of the running game. **Not implemented by any shipping
   runtime** — SteamVR, Oculus/Meta, WMR all list it unsupported. The only implementation is
   LunarG's provisional `OpenXR-OverlayLayer` reference (Windows/Direct3D 11, written against
   the OpenXR 1.0.9 provisional spec). **Not usable.**
2. **An OpenXR *API layer* (how OpenKneeboard works).** Not the extension above — an
   interception layer inserted between the game and its OpenXR runtime that hooks
   `xrEndFrame` and injects an extra quad layer. Runtime-agnostic (works on Oculus, SteamVR,
   WMR, Virtual Desktop) **because the layer, not the runtime, does the compositing.** But
   it is a substantial **C++ + Direct3D 11/12/Vulkan** project with per-game registry
   registration and shared-texture plumbing — far outside "a small Python capability," and
   it only helps ED at all when ED is forced onto OpenXR (ED has no native OpenXR — see
   below). We should **reuse** an existing one (OpenKneeboard), not build it.
3. **SteamVR overlay (`IVROverlay`, OpenVR).** A separate overlay *application* that asks
   the **SteamVR compositor** to draw a quad. Mature, well-trodden, and has **first-class
   Python bindings** (`pyopenvr`). Works whenever the game renders through SteamVR. **This is
   our path.**

The decisive Elite-Dangerous fact: **ED natively supports OpenVR (SteamVR) and the Oculus
SDK — it has no native OpenXR.** Any OpenXR path for ED requires a wrapper (OpenComposite)
that translates ED's OpenVR calls to OpenXR. So the SteamVR/OpenVR overlay is not just the
easy path — it is the one that matches ED's *native* VR rendering for the majority of PCVR
players.

---

## Option evaluation

### 1. OpenXR overlay — `XR_EXTX_overlay` / compositor layer

| | |
|---|---|
| **Feasibility from Python** | None in practice. No maintained Python OpenXR overlay binding; `pyopenxr` exists but the overlay *extension itself* is unsupported by runtimes, so bindings are moot. |
| **Runtime support** | **Unsupported** by SteamVR, Oculus/Meta, WMR, and all shipping compositors. Only LunarG's provisional D3D11 reference API layer implements it. |
| **ED compatibility** | Irrelevant — ED has no native OpenXR anyway; would require OpenComposite *and* a runtime that supports the extension (none does). |
| **Verdict** | **Reject.** Revisit only if runtimes ever adopt a ratified overlay extension. |

The runtime-agnostic *API-layer* approach (OpenKneeboard-style, mechanism #2 above) is
technically the "works everywhere" option, but it is a C++/D3D/Vulkan product in its own
right, needs per-game API-layer registration, and for ED specifically only applies once ED
is running on OpenXR via OpenComposite. **Building our own is out of scope.** Reusing
OpenKneeboard (capturing our 2D window) is the documented Quest/Oculus-runtime answer.

### 2. SteamVR overlay — `IVROverlay` via `pyopenvr` (RECOMMENDED for VR)

| | |
|---|---|
| **Feasibility from Python** | **High.** `pip install openvr` (the `pyopenvr` project, `cmbruns`) is a mature ctypes binding to Valve's OpenVR SDK, bundling `openvr_api.dll`. Init as an **overlay app** with `openvr.init(openvr.VRApplication_Overlay)` — this type is *designed* to run alongside a running VR game. |
| **Rendering from Python** | `IVROverlay.setOverlayRaw(handle, buf, w, h, 4)` uploads a **raw RGBA byte buffer from system memory — no DirectX/OpenGL context required.** This is the key enabler: we can render the HUD with numpy/Pillow and push pixels directly. (`SetOverlayFromFile` is the even-simpler static-image variant; `SetOverlayTexture` is the GPU-texture fast path we do *not* need.) |
| **Positioning** | `setOverlayWidthInMeters`, `setOverlayTransformAbsolute` (world-fixed) or transform-relative-to-HMD/controller; `setOverlayAlpha`. Enough to park a panel in the cockpit or lock it to the view. |
| **Runtime support** | SteamVR (shipping, first-class). Third-party overlays are a SteamVR-supported feature (OVR Toolkit, XSOverlay, OpenKneeboard-SteamVR all use exactly this). |
| **ED compatibility** | **Native.** ED renders VR through OpenVR/SteamVR out of the box; a SteamVR overlay composites over it directly. Best-case fit. |
| **Failure modes** | If SteamVR is not running, `VRApplication_Overlay` init fails (`VRApplication_Background` would fail with `Init_NoServerForBackgroundApp`). Must **fail soft** — no SteamVR → no VR overlay, 2D window still works. |
| **Dependency cost** | One new dep, **`openvr`** (BSD-3-Clause, permissive — fine for our public repo). Pure-Python + a bundled `openvr_api.dll` (a few MB). PyInstaller needs the DLL + data collected (`collect_all("openvr")` in `covas.spec`), mirroring how we already collect `av`/`onnxruntime`. Modest size, no native build toolchain. |
| **Verdict** | **Adopt for the VR surface.** |

### 3. 2D transparent always-on-top window (RECOMMENDED for the flat surface)

Toolkit options for a frozen-PyInstaller-friendly transparent, always-on-top HUD:

| Toolkit | New dep? | Frozen size | Transparency / click-through | Notes |
|---|---|---|---|---|
| **`tkinter`** (stdlib) | **None** | ~0 (in Python already) | `-alpha` (whole-window), **`-transparentcolor` = color-key transparency *and* click-through on Windows**, `overrideredirect(True)` borderless, `-topmost` always-on-top | **Recommended.** Simplest possible, zero dep cost, perfect for a glanceable text/box HUD. Optional `ctypes` `WS_EX_TRANSPARENT` for whole-window click-through. |
| **PyWebView** (Edge WebView2) | **None — already bundled** (`webview`, it is the app's main window) | ~0 marginal | Frameless + `on_top` yes; **transparency & click-through are weak/unsupported** across backends | Attractive because it could render the **existing Flask/HTML HUD markup** — reuse the web stack. But opaque rectangle, no click-through. Good **follow-up** for a rich HUD, not the MVP. |
| **PySide6 / Qt** | Large new dep | **~100 MB+** frozen | Excellent (`WA_TranslucentBackground`, frameless, `WindowTransparentForInput` click-through) | Most capable, but a heavyweight dependency that does **not** earn its place per CLAUDE.md for a companion panel. Reject. |
| **Dear PyGui** | New dep (~10 MB) | Moderate | GPU-rendered viewport; always-on-top yes, transparency/click-through limited | Nice for gauges/graphs later; overkill and unproven-here for the MVP. Reject for now. |
| **Raw Win32 layered window (`ctypes`)** | None | ~0 | Full control (`UpdateLayeredWindow`, per-pixel alpha, `WS_EX_TRANSPARENT`) | Maximum capability at zero dep, but hand-rolled GDI is a lot of code. Only if `tkinter` proves too limiting. |

**Recommendation: `tkinter` for the MVP 2D overlay.** Zero new dependency (the single
strongest fit with CLAUDE.md's "standard library first" and frozen-build constraints),
Windows color-key transparency gives click-through for free, and it is more than enough for
a text/box companion panel. Keep **PyWebView** (already in the bundle) as the documented
upgrade path when we want a visually rich HUD reusing the Flask templates.

### Meta Quest — what actually reaches a Quest user

Quest is not one path; it is several, and only some carry a SteamVR overlay:

| How the Quest player runs ED | Active compositor | Does our SteamVR overlay show? |
|---|---|---|
| **Quest Link / Air Link, SteamVR as runtime** | SteamVR | **Yes** — this is the target case. |
| **Virtual Desktop, "SteamVR" streaming mode** | SteamVR | **Yes.** |
| **Quest Link, native Oculus runtime** (no SteamVR) | Oculus | **No** — Oculus runtime does not host third-party overlays. |
| **OpenComposite / native OpenXR** (perf tweak) | OpenXR (Oculus) | **No** — not SteamVR; needs an OpenXR-API-layer overlay. |

So: **most Quest ED players who use SteamVR are covered by the same first-party overlay.**
Quest players on the native Oculus runtime or OpenComposite are covered by the **2D-window +
OpenKneeboard/OVR Toolkit** documented route — the same approach EDCoPilot ships (see below).
Building our own OpenXR API layer to cover them natively is explicitly **not** recommended
for this epic.

---

## "Beats competitors" thesis

EDCoPilot's documented VR story is: run a **generic** window-capture overlay
(OpenKneeboard, after switching ED to OpenXR via OpenComposite) and have *it* grab the
EDCoPilot window. COVAS:NEXT is in the same family — a 2D app the user must bring into VR
with third-party tooling. In every case the overlay is a **dumb mirror of a 2D window** that
the user must install extra software to set up, and it knows nothing about what it is
showing.

COVAS++ can do better on a concrete axis: **a first-party, context-aware SteamVR overlay
that needs no third-party capture tool for the common PCVR/SteamVR case.** Because we own
the renderer and the game state, the HUD can be *reactive* rather than a static window
mirror — surface the active checklist item, flash on a proactive callout, show current
system / fuel / danger state, and fade to near-invisible when idle so it is glanceable, not
clutter. That is a genuine step up from "capture my 2D window," and it composites over ED's
**native** SteamVR rendering with zero extra user tooling. We still interoperate with
OpenKneeboard/OVR Toolkit for the Oculus-native/OpenXR minority (via the same 2D window),
so we match the competition's reach *and* beat it where most players are.

---

## Recommended architecture (both sub-issues)

```
   COVAS++ state (EDContext, checklist, proactive callouts, EventBus)
                     │
              HudModel  (a tiny, provider-agnostic snapshot: system, fuel,
                     │   status flags, active checklist item, last callout)
                     │
              HudRenderer.render() ──► offscreen RGBA buffer (numpy, HxWx4)
                     │
         ┌───────────┴───────────────┐
   Flat2DOverlaySink            SteamVROverlaySink
   (tkinter window,             (pyopenvr IVROverlay,
    color-key transparent,       setOverlayRaw, fail-soft
    always-on-top)               when SteamVR absent)
```

- A **capability** (per CLAUDE.md "capabilities over loop edits") subscribes to the
  `EventBus`, keeps a small `HudModel`, and drives the renderer. Off by default
  (`[hud].enabled`), like every other opt-in capability.
- **One renderer, two sinks.** The renderer never knows which surface consumes it. Either
  sink can be absent (no SteamVR → VR sink disabled; headless/CI → both disabled) without
  affecting the other — same fail-soft discipline as the TTS/provider seams.
- **No always-on GPU cost.** `setOverlayRaw` from a CPU buffer sidesteps any D3D/GL context.
  Redraw only on state change (throttled), so the overlay is cheap next to ED.

### Rough effort estimate

| Sub-issue | Scope | Rough effort |
|---|---|---|
| **2D flat overlay** | `HudModel` + numpy renderer + tkinter sink + capability wiring + settings toggle + PyInstaller check | **~2–3 days** |
| **VR overlay** | `openvr` dep + `covas.spec` `collect_all` + SteamVR sink (`setOverlayRaw`, transform, fail-soft) + on-hardware validation | **~3–5 days** (most of it on-hardware iteration: placement, scale, legibility, alpha) |
| Shared | HudRenderer design, EventBus subscription, docs/tests/help sync | folded into the two above |

New dependency to add **only when the VR sub-issue is actually built** (not for this spike):
**`openvr`** (BSD-3). The 2D sub-issue adds **no** dependency.

---

## Proof-of-concept artifacts (throwaway — not shipped code)

Both live under `docs/spikes/`, deliberately **outside** the `covas/` package so they are
never part of the app:

- **`hud_2d_overlay_poc.py`** — a **runnable** standalone tkinter transparent
  always-on-top HUD showing a static demo panel (system / fuel / checklist item / callout)
  with a simulated update loop. Byte-compiled clean here; **run on Doug's Windows machine**
  to eyeball transparency, always-on-top, and color-key click-through. This de-risks the 2D
  sub-issue.
- **`hud_vr_overlay_poc.py`** — a **code sketch, intentionally un-run** (no VR hardware or
  SteamVR in this environment). It documents the exact `pyopenvr` calls to stand up a
  SteamVR overlay from a raw RGBA buffer, guarded so it prints setup steps and exits cleanly
  if `openvr` / SteamVR are absent. It is the minimal path Doug can run with an HMD +
  SteamVR to de-risk the VR sub-issue. **Do not** add `openvr` to `requirements.txt` for
  this — `pip install openvr` in a scratch venv to try it.

---

## What I could and could not verify

**Verified (from code, docs, and web research, 2026-07):**

- `XR_EXTX_overlay` is unsupported by all shipping runtimes; only LunarG's provisional D3D11
  API-layer reference implements it.
- `pyopenvr` (`pip install openvr`) is BSD-3, mature, exposes `IVROverlay` including
  `setOverlayRaw` (raw RGBA from system memory, **no DirectX context**), and the correct
  init type for an overlay is `VRApplication_Overlay` (runs alongside a VR game).
- ED natively supports OpenVR (SteamVR) + Oculus SDK and has **no native OpenXR** (OpenXR
  needs OpenComposite to translate).
- Oculus native runtime does **not** host third-party overlays; only SteamVR does — so the
  Quest reach matrix above holds.
- OpenKneeboard is an OpenXR **API layer** (C++/D3D), and it (not `XR_EXTX_overlay`) is how
  EDCoPilot reaches Oculus/OpenXR — via **window capture** of the 2D app.
- PyWebView (`webview`) is **already bundled** in `covas.spec`; `tkinter` is stdlib. So the
  recommended 2D path adds **zero** dependency and is frozen-build-safe.
- The 2D PoC byte-compiles cleanly (`python -m compileall`).

**Could NOT verify in this environment (needs Doug's hardware — see manual tests):**

- That the tkinter overlay actually renders transparent, click-through, and stays above ED
  in **full-screen/exclusive** VR-mirror or borderless ED (full-screen exclusive games can
  cover always-on-top windows; the 2D overlay is primarily for the desktop/mirror case).
- That a `pyopenvr` `setOverlayRaw` overlay appears **in-headset over running ED on
  SteamVR**, at a legible size/placement — none of the VR path was executed here.
- Quest-through-SteamVR behavior (Link / Air Link / Virtual Desktop) end to end.
- PyInstaller correctly bundling `openvr_api.dll` in a frozen build (needs a real
  `build.ps1` run once the dep is added).

---

## Manual on-hardware validation steps (for Doug)

**2D overlay (`docs/spikes/hud_2d_overlay_poc.py`):**

1. `.venv\Scripts\python.exe docs\spikes\hud_2d_overlay_poc.py` — confirm a borderless HUD
   panel appears top-right, background fully transparent (desktop shows through), panel
   stays **on top** of other windows, and the demo values tick.
2. Move the mouse over the transparent area and click — confirm the click lands on the
   window **behind** the overlay (color-key click-through working).
3. Launch ED in **borderless/windowed** and in **full-screen** — note whether the overlay
   stays visible above each (full-screen exclusive may hide it; that is expected and informs
   whether we recommend borderless).

**VR overlay (`docs/spikes/hud_vr_overlay_poc.py`):**

4. In a scratch venv: `pip install openvr numpy`. Start **SteamVR**. Run
   `python docs\spikes\hud_vr_overlay_poc.py` — confirm a colored test panel appears
   floating in the headset, and it does **not** crash when SteamVR is running.
5. Stop SteamVR and re-run — confirm it **fails soft** (prints "SteamVR not available" and
   exits, no traceback).
6. Launch **Elite Dangerous in VR (SteamVR)**, run the PoC alongside — confirm the overlay
   composites over the ED cockpit at a readable size; try `setOverlayWidthInMeters` and a
   transform to park it sensibly.
7. **Quest only:** repeat step 6 with ED on **Quest Link/Air Link with SteamVR active** and
   again with **Virtual Desktop (SteamVR mode)** — confirm the overlay shows. Then try ED on
   the **native Oculus runtime** and confirm it does **not** (documents the boundary and the
   "use OpenKneeboard for that case" note).

---

## Sources

- [XR_EXTX_overlay unsupported in SteamVR OpenXR (Steam Community)](https://steamcommunity.com/app/250820/discussions/8/2448217320142811491/)
- [LunarG OpenXR-OverlayLayer (provisional reference impl)](https://github.com/LunarG/OpenXR-OverlayLayer)
- [pyopenvr (cmbruns) — Python OpenVR bindings](https://github.com/cmbruns/pyopenvr)
- [openvr on PyPI (BSD-3)](https://pypi.org/project/openvr/)
- [IVROverlay::SetOverlayRaw (raw RGBA, no DirectX)](https://github.com/ValveSoftware/openvr/wiki/IVROverlay::SetOverlayRaw)
- [IVROverlay overview](https://github.com/ValveSoftware/openvr/wiki/IVROverlay_Overview)
- [VRApplication_Background / overlay init requirements](https://steamcommunity.com/app/358720/discussions/0/343788552532794340/)
- [OVR Toolkit: OpenXR/Oculus overlay limitations (only OpenVR/SteamVR host overlays)](https://steamcommunity.com/app/1068820/discussions/0/3418809548706349214/)
- [EDCoPilot VR overlay via OpenKneeboard + OpenComposite (Oculus/Meta)](https://www.razzafrag.com/post/vr-overlay-for-oculus-using-openkneeboard)
- [OpenKneeboard internals — OpenXR API layer, window capture](https://openkneeboard.com/internals/README/)
- [Elite Dangerous VR is OpenVR/Oculus, not native OpenXR (Frontier Forums)](https://forums.frontier.co.uk/threads/a-few-quick-vr-things.618890/)
