# Combat reflexes (Tier-2)

> *"Chaff away — I'll fire your defensive reflexes the instant you're under fire, but nothing
> dangerous, and never when you're safe."*

Combat reflexes are the **opposite** of [keybind automation](keybinds.md). Keybinds control the
ship when it's **safe** — and refuse the moment you're in combat. Some ship actions, though, only
make sense **while** you're being shot at: **chaff**, heat sink, shield cell, boost. Those are
*reflexes*, and they run under a **separate, inverted policy** — the *combat-permissive* guard.

Today COVAS++ ships exactly **one** validated reflex: **fire chaff**. It's a prototype for the
inverted safety model; more reflexes (heat sink, shield cell, boost) build on the same guard.

**Example:** *"chaff!"* — while you're in danger or being interdicted.

!!! danger "Off by default — opt in deliberately"
    This sends **real keypresses into Elite Dangerous.** Set `[reflex].enabled = true` **and** add
    `chaff` to `[reflex].allowlist` to use it. The allowlist ships **empty**, so nothing fires until
    you name it.

## Tier-1 vs Tier-2 — the two safety models

|  | Keybinds (Tier-1) | Reflexes (Tier-2) |
|---|---|---|
| **Fires when** | Elite Dangerous status shows you're **safe** | status shows you're **in danger / being interdicted** |
| **Refuses when** | in combat/interdiction, or status unreadable | when you're safe, or status unreadable |
| **Purpose** | act when nothing's shooting (gear, panels, jumps) | act *because* something's shooting (chaff) |
| **Confirmation** | consequential actions need a separate spoken "confirm" | **none** — a reflex fires immediately |

These are two **separate** policies. Enabling reflexes does **not** change any keybind behaviour,
and the keybind allowlist (`landing_gear`) is untouched.

## How it stays safe

- **Combat-permissive guard** — chaff only fires when your Elite Dangerous status shows you're
  **in danger or being interdicted**. Ask for it when you're safe and it refuses. If it can't read
  your status at all, it refuses too (it won't fire a reflex *blind*). Controlled by
  `[reflex].combat_guard` (leave on).
- **Dangerous actions are always off-limits** — the guard **hard-refuses** ejecting cargo,
  self-destruct, and dropping the landing gear, in combat or out. Relaxing the guard *for* combat
  is never a backdoor to those.
- **Allowlist** — only reflexes you've named in `[reflex].allowlist` can fire. It ships **empty**.
- **Fire button bound to a key** — COVAS presses keyboard scancodes, so bind your **chaff launcher**
  to a key in Elite Dangerous (a HOTAS-only bind can't be pressed). An unbound reflex tells you to
  bind it rather than silently doing nothing.
- **Hard abort** — say **"abort"** any time to release every held key immediately. Reflexes share
  the same key executor as your keybinds and auto-honk, so one abort covers all three.

## Settings

| Setting | What it does |
|---------|--------------|
| `reflex.enabled` | Master switch (**off** by default) |
| `reflex.combat_guard` | Permit reflexes only while in danger/interdiction; always refuse dangerous actions (leave on) |
| `reflex.allowlist` | Reflex names allowed to fire — ships **empty**; add `"chaff"` to opt in |

Requires [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) — the guard must
read your status to confirm you're in danger before firing. See the
[Configuration reference](../configuration.md).

## Roadmap

The chaff reflex is the foundation, not the finish line. Two follow-ups build on this guard:

- **Auto-reflexes** — fire chaff automatically the moment your status flips to *in danger*, no
  command needed.
- **Instant hotword** — a local *"chaff!"* phrase-spotter that fires in well under a second,
  bypassing the assistant round-trip.
