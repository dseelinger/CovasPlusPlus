# Auto-honk

Auto-honk fires the **Discovery Scanner** — the "honk" that reveals a system's bodies — shortly
after you jump into a **new system**, hands-free. It's the second keystroke COVAS++ can send, built
on the same guarded [keybind executor](keybinds.md).

!!! danger "Off by default — it presses a fire button"
    Set `[honk].enabled = true` to use it. Because it holds a fire button, it's combat-gated and
    opt-in. Test it parked and safe.

## What happens on arrival

When you jump into a new system (and it's safe), auto-honk does one of two things:

- **If you've told it your scanner's fire group** — it reads your *current* fire group from the
  game, cycles to the scanner group, **holds** the fire button for a few seconds to complete the
  honk, then cycles back. Deterministic — no guessing.
- **If you haven't configured a fire group** (`fire_group = -1`) — it just holds the primary fire
  button for the duration (the "hope for the best" fallback that works when the scanner is already
  your selected group).

## Safety

Auto-honk reuses the [keybind safety layer](keybinds.md#how-it-stays-safe):

- **Combat / interdiction guard** — it won't honk while you're in danger or being interdicted, and
  it won't act if it can't read your status (it can't prove it's safe).
- **Cycle safety** — if cycling to the scanner group is needed but your current group can't be read,
  it **refuses** rather than risk holding fire in the wrong group (which could fire weapons).
- **Hard abort** — the shared *"abort"* releases the held fire key immediately.
- Every honk (and every skip) is logged.

The hold runs on a background thread so it never blocks anything else, and a second honk is dropped
while one's in progress.

## Setting it up

To get cycling (recommended), bind the scanner's fire button — and, for cycling, fire-group
next/previous — to keys in-game, and note the scanner's fire group number (0-based, from the right
HUD panel). Then:

| Setting | What it does |
|---------|--------------|
| `honk.enabled` | Master switch (off by default) |
| `honk.fire_group` | The scanner's fire group (0-based). `-1` = don't cycle, just hold primary fire |
| `honk.trigger` | Which fire button the scanner is on — `primary` or `secondary` |
| `honk.hold_seconds` | How long to hold the fire button to complete the scan (~6 s) |
| `honk.combat_guard` | Refuse during danger/interdiction or unknown status (leave on) |

At launch the log reports the fire key and group it found, or a "bind it in-game" warning if the
binding's missing. Requires [game-state monitoring](../elite/monitoring.md)
(`[elite].enabled = true`). See the [Configuration reference](../configuration.md#auto-honk-honk).
