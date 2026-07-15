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

## Instant reflexes — the second push-to-talk

Asking through the assistant ("fire chaff") works, but it takes a full think-and-reply round-trip.
When something's shooting at you, that's too slow. So COVAS++ has a **fast path**: bind a **second
push-to-talk** to `[reflex].ptt` and a snap **"chaff!"** on that key fires **locally** — matched
against a small fixed combat vocabulary straight on the transcript, with **no LLM round-trip**.
Latency is roughly just the speech-to-text time (a heartbeat), and the call never waits behind a
normal conversation turn.

- **Bind a *different* key** than your normal talk key (a spare HOTAS button via JoyToKey works
  well). Blank (the default) disables it — nothing extra is installed.
- **What it hears** (say any synonym): **chaff** — *"chaff"*, *"flares"*, *"decoy"*, *"break lock"*;
  **heat sink** — *"heat"*, *"heat sink"*, *"dump heat"*; **shield cell** — *"shields"*,
  *"shield cell"*, *"cell"*; **boost** — *"boost"*, *"punch it"*; **abort** — *"abort"*, *"stop"*,
  *"cancel"*, *"release"* (releases every held key, instantly).
- **It routes through the exact same safety** as above — the combat-permissive guard, the
  allowlist, and the hard abort. The fast path is *faster*, not *looser*: chaff still only fires
  while you're in danger, and dangerous actions are still off-limits.
- **Say anything that isn't a combat keyword** on that key and it simply **falls through to a normal
  turn** — so it doubles as an ordinary talk key if you mis-hit it.

Today the same one reflex — **chaff** — is wired to fire (heat sink, shield cell, and boost are
recognised by the spotter but not yet pressed). It requires `[reflex].enabled` and `chaff` in the
allowlist, exactly like the assistant path.

## Settings

| Setting | What it does |
|---------|--------------|
| `reflex.enabled` | Master switch (**off** by default) |
| `reflex.combat_guard` | Permit reflexes only while in danger/interdiction; always refuse dangerous actions (leave on) |
| `reflex.allowlist` | Reflex names allowed to fire — ships **empty**; add `"chaff"` to opt in |
| `reflex.ptt` | Second push-to-talk for the instant fast path — a snap *"chaff!"* fires locally with no LLM. Bind a **different** key than the talk key; **blank** disables it |

Requires [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) — the guard must
read your status to confirm you're in danger before firing. See the
[Configuration reference](../configuration.md).

## Roadmap

The chaff reflex is the foundation, not the finish line. The **instant hotword** fast path (the
second push-to-talk above) is already here. One follow-up still builds on this guard:

- **Auto-reflexes** — fire chaff automatically the moment your status flips to *in danger*, no
  command needed.

Heat sink, shield cell, and boost are recognised by the phrase-spotter and the guard today; wiring
each to actually press its key follows the same pattern as chaff.
