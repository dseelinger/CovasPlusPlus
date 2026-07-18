# Companion HUD

COVAS++ can show a small, glanceable **overlay** on top of the game — a transparent,
always-on-top panel that surfaces the companion-centric information only COVAS++ has. It's a
*view*, not a control surface: it's non-interactive and (on Windows) click-through, so it never
intercepts input meant for Elite Dangerous.

!!! info "Opt-in"
    Off by default — set `[hud].enabled = true`, flip **Companion HUD overlay** on the
    [Settings page](../control-panel.md), or just say *"turn the HUD on."* It needs a desktop:
    on a headless machine (or if the toolkit is unavailable) it simply doesn't appear — no error.

## What it shows

The panel is deliberately minimal — four rows, each of which collapses when there's nothing to
show, so it stays a quick glance rather than a dashboard:

| Row | What you see |
|-----|--------------|
| **Voice-loop state** | Whether COVAS is `Idle`, `Listening`, `Thinking`, or `Speaking` — the same state the control panel shows, at a glance without alt-tabbing |
| **Current checklist step** | Your next pending [checklist](checklist.md) item, with a done/total count (e.g. *"Scan the nav beacon  (2/10 done)"*) |
| **Route progress** | While flying a plotted galaxy-map route: jumps remaining to the destination, and whether the next star is scoopable — the same live route the [route callouts](../elite/route-callouts.md) read |
| **Last proactive callout** | The last line COVAS volunteered on its own — a [proactive callout](../elite/proactive-callouts.md) or a route heads-up |

This is the axis where the HUD beats the competition: EDCoPilot's and COVAS:NEXT's overlays
mirror ship/EDMC telemetry, whereas this shows **the companion's own state, your checklist, and
your route** — a purpose-built minimal panel, not a generic instrument dump.

## Turning it on and off

Three equivalent ways, all writing the same `[hud].enabled` setting:

- **By voice** — *"turn the HUD on"* / *"turn the HUD off"* (handled by [settings by
  voice](settings.md)).
- **Settings page** — toggle **Companion HUD overlay** under *Companion HUD*.
- **Config** — set `[hud].enabled = true` in `config.toml`.

Toggling applies live — no restart. When you turn it off the window disappears; turn it back on
and it returns.

## Where it sits

The panel parks itself in the **top-right** corner. On Windows its background is fully
transparent and click-through (a color-key overlay), so the desktop or game shows through and
your mouse clicks pass to whatever is behind it.

!!! note "Full-screen Elite Dangerous"
    An always-on-top window reliably floats over ED in **borderless / windowed** mode. True
    **full-screen exclusive** mode can cover any overlay (that's a Windows limitation, not a
    COVAS++ one) — run ED borderless if you want the HUD visible over it. Playing in VR? Turn on
    the **VR overlay** below instead — it floats the same panel *inside* the headset.

## In VR — the in-headset overlay

The same four-row HUD can render as a true **SteamVR overlay** floating in your cockpit, so you
never alt-tab out of the headset to see it. It shows exactly the same information as the 2D panel
— it's the same data adapter, just a different rendering surface.

!!! info "Opt-in, and separate from the 2D HUD"
    Off by default — set `[hud].vr_enabled = true`, flip **VR HUD overlay** on the
    [Settings page](../control-panel.md), or say *"turn the VR HUD on."* It's independent of the
    2D overlay: run either, both, or neither.

**Requirements.** The VR overlay needs one thing: **SteamVR running**, with Elite Dangerous
rendering through it. ED natively speaks OpenVR/SteamVR, so that's the default for most PCVR
headsets (Valve Index, HTC Vive, Windows Mixed Reality via SteamVR, and so on). Nothing to
install — the `openvr` binding ships inside COVAS++.

!!! note "Attach-only — it never launches SteamVR"
    The overlay only *attaches* to a SteamVR that's **already running**; enabling it never starts
    SteamVR. So if you normally play through **VDXR / OpenComposite** (or on a flat monitor), you
    can leave `[hud].vr_enabled = true` set and it simply stays off in those sessions — it won't
    drag SteamVR up for an overlay it can't render there. It comes to life only when you're
    actually in a SteamVR session.

!!! warning "Not in v0.12.0 or earlier"
    Releases up to and including **v0.12.0** were built without the `openvr` binding, so
    `[hud].vr_enabled` silently did nothing no matter how it was set. Earlier versions of this
    page told you to run `pip install openvr` — that was never possible against an installed
    COVAS++, which has no Python environment of its own. **Update to a later release** to use the
    VR overlay.

**Placement.** Every setting below is on the Settings page and in `config.toml` — and **applies
live**: change it there or by voice and a shown overlay moves immediately, no re-toggle. So the
way to place the panel is to put the headset on and adjust by voice until it sits right.

The natural way to do that, hands still on the stick, is two kinds of voice command:

- **Look-to-place** — look where you want the panel and say *"pin the HUD here."* It places the
  panel along your gaze — matching your heading **and** how far up or down you're looking (look
  down at the dash and it drops there; look up and it rises) — and **tilts it to face you** so it
  reads head-on. Distance, width, and curvature are kept; it recentres laterally onto your gaze.
  A near-vertical gaze clamps gracefully (±60° tilt, ±2 m). Aimed for seated cockpit play.
- **Nudges** — *"move the HUD left / right / up / down,"* *"closer" / "farther"* (or *"forward" /
  "back"*), *"tilt it up / down,"* *"flatter" / "more curved,"* *"bigger" / "smaller,"* *"centre
  the HUD,"* *"reset the HUD position."* Add an amount if you like: *"move it left 20
  centimetres,"* *"tilt it up 10 degrees."*

For an exact value, the absolute settings still work: *"set the VR HUD distance to 1.5,"* *"set
the VR HUD curvature to 0.1."*

| Setting | What it does |
|---------|--------------|
| **`[hud].vr_placement`** | `world` (default) parks the panel **cockpit-fixed** in front of you; `head` **locks it to your view** so it follows where you look |
| **`[hud].vr_width_m`** | Physical width of the panel in metres (default `0.55` — reads well at arm's length) |
| **`[hud].vr_distance_m`** | How far in front the panel sits, in metres (default `1.30`; range `0.30`–`5.0`) |
| **`[hud].vr_offset_x_m`** | Left/right offset in metres (default `0.0`; `+` = right, `−` = left) |
| **`[hud].vr_offset_y_m`** | Up/down offset in metres (default `−0.12`, slightly below eye-line; `+` = up) |
| **`[hud].vr_pitch_deg`** | Tilt in degrees (default `0`; **positive leans the top toward you**, so a low panel angles up to face you) |
| **`[hud].vr_curvature`** | Curve of the panel: `0` flat … `1` a full cylinder. Default `0.1` — a gentle ED-style wrap |

!!! note "Meta Quest"
    A Quest reaches this overlay **when it runs ED through SteamVR** — Quest Link / Air Link with
    SteamVR active, or Virtual Desktop in its SteamVR mode. On the **native Oculus runtime** or
    **OpenComposite/OpenXR**, SteamVR isn't the compositor, so the first-party overlay won't
    show; for those, capture the **2D window** with a generic tool like OpenKneeboard or OVR
    Toolkit (the same route the other Elite assistants use).

## How it works

The HUD is a thin **view over live state**: a pure adapter maps the EventBus status, the
checklist model, and the plotted route into the four display fields. That single adapter feeds
**two rendering surfaces** — a small [tkinter](https://docs.python.org/3/library/tkinter.html)
window (Python standard library — no new dependency) for the desktop, and a SteamVR overlay
(via the optional `openvr` binding, rendered from a raw RGBA buffer) for the headset. Because the
data adapter is separate from both surfaces, the feature is fully testable offline, and each
surface is only ever created when its toggle is on *and* its runtime (a display, or SteamVR) is
actually present — otherwise it quietly stays off.
