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
    COVAS++ one) — run ED borderless if you want the HUD visible over it. For a genuine
    in-headset VR overlay, see the roadmap (a separate first-party SteamVR overlay is planned).

## How it works

The HUD is a thin **view over live state**: a pure adapter maps the EventBus status, the
checklist model, and the plotted route into the four display fields, and a small
[tkinter](https://docs.python.org/3/library/tkinter.html) window (Python standard library — no
new dependency) renders them. Because the data adapter is separate from the window, the feature
is fully testable offline, and the window is only ever created when the HUD is enabled *and* a
display is available.
