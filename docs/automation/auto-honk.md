# Auto-honk

Auto-honk fires the **Discovery Scanner** — the "honk" that reveals a system's bodies — shortly
after you jump into a **new system**, hands-free. It's built on the same guarded
[keybind executor](keybinds.md), and it needs **no fire-group setup**.

!!! tip "On by default — and safe"
    Auto-honk ships **on**. It never needs to know your fire groups: it fires your *current*
    group and reacts. If that turns out to be the wrong group and opens the Surface Scanner, it
    **backs out, warns you, and pauses itself** until you re-arm. A weapons group can't fire in
    supercruise, so the worst non-scanner case is a harmless no-op.

## What happens on arrival

When you jump into a new system (and it's safe — see below), auto-honk:

1. Sends a short **probe-press** of your fire button.
2. Watches the game for a moment. If that opened the **Detailed Surface Scanner** (the probe
   view — meaning your current fire group holds the DSS, not the Discovery Scanner), it presses
   your **Exit Mode** bind to back out, speaks a heads-up, and **disarms** so it can't keep
   misfiring.
3. Otherwise it **holds** the fire button for a few seconds to complete the honk.

### Re-arming after a misfire

If auto-honk paused itself, re-arm it either way:

- **Say so** — e.g. *"re-arm auto honk"* or *"the discovery scanner's set"* (it exposes a
  `rearm_auto_honk` tool the assistant calls).
- **Automatically** — the next time a real discovery scan completes (you honked manually, or it
  worked once), it re-arms itself.

The real fix is to put the **Discovery Scanner** in your selected fire group — then every honk
just works.

## Safety

Auto-honk only fires when **all** of these hold, else it skips (with a logged reason):

- **In supercruise** — not in normal space or docked.
- **In analysis mode** — not combat mode (the scanners don't work in combat mode anyway).
- **Not in danger / not being interdicted**, and your status is readable (it won't act if it
  can't prove it's safe). Controlled by `honk.combat_guard` (leave on).
- **Fire button is bound to a key** — COVAS presses keyboard scancodes, so bind the Discovery
  Scanner's fire to a key in-game (a HOTAS-only bind can't be pressed; a keyboard secondary,
  even with a modifier, is fine).

Plus the shared [keybind](keybinds.md#how-it-stays-safe) **hard abort** (*"abort"*) releases the
held key immediately, the sequence runs on a background thread so it never blocks anything else,
and a second honk is dropped while one's in progress.

## Settings

| Setting | What it does |
|---------|--------------|
| `honk.enabled` | Master switch (**on** by default) |
| `honk.trigger` | Which fire button the scanner is on — `primary` or `secondary` |
| `honk.hold_seconds` | How long to hold the fire button to complete the scan (~5 s) |
| `honk.combat_guard` | Refuse during danger/interdiction or unknown status (leave on) |

There's **no fire-group setting** — the detect-and-recover replaces it. Requires
[game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) for the arrival event
and the guards. See the [Configuration reference](../configuration.md#auto-honk-honk).
