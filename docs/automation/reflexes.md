# Combat reflexes (Tier-2)

> *"Chaff away — I'll fire your defensive reflexes the instant you're under fire, but nothing
> dangerous, and never when you're safe."*

Combat reflexes are the **opposite** of [keybind automation](keybinds.md). Keybinds control the
ship when it's **safe** — and refuse the moment you're in combat. Some ship actions, though, only
make sense **while** you're being shot at: **chaff**, heat sink, shield cell, boost. Those are
*reflexes*, and they run under a **separate, inverted policy** — the *combat-permissive* guard.

COVAS++ ships two validated reflexes — **fire chaff** and **deploy heat sink** — on three paths: ask
the assistant, a **second push-to-talk** hotword that fires locally, or **automatically** when your
status crosses a threshold. All run under the same inverted safety model (shield cell and boost
build on the same guard next).

**Example:** *"chaff!"* — while you're in danger or being interdicted.

!!! danger "Off by default — opt in deliberately"
    This sends **real keypresses into Elite Dangerous.** For the **spoken/hotword** reflexes, set
    `[reflex].enabled = true` **and** add `chaff` / `heat_sink` to `[reflex].allowlist`. The allowlist
    ships **empty**, so nothing fires until you name it. The **automatic** reflexes are separate and
    also off by default — see [Automatic reflexes](#automatic-reflexes) below.

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

Two reflexes — **chaff** and **heat sink** — are wired to fire (shield cell and boost are recognised
by the spotter but not yet pressed). They require `[reflex].enabled` and the reflex in the allowlist,
exactly like the assistant path.

## Automatic reflexes

!!! warning "Experimental — off by default"
    The **automatic** layer (this section) is an **experimental** feature: it ships **disabled**
    for everyone and is gated at capability registration. The verbal and second-push-to-talk
    reflex paths above are **not** experimental. Enable the automatic layer just for yourself by
    adding `experimental.auto_reflex.enabled = true` to your git-ignored `overrides.json` (see
    [Experimental feature flags](../configuration.md#experimental-feature-flags)) **and** setting
    `reflex.auto.enabled = true` (plus the per-reflex enable below).

The **automatic** layer fires the same reflexes the instant your Elite Dangerous status crosses a
threshold — **no voice, no key, no assistant round-trip.** It's the fastest path (sub-100ms), and it
runs under the **same** combat-permissive guard as everything above: it can only fire a defensive
reflex, only while you're in danger, and never a dangerous action.

It's part of the same Tier-2 subsystem, so it needs `[reflex].enabled = true` (the master switch)
**and** `[reflex.auto].enabled = true`, then a per-reflex enable. Two automatic reflexes ship, both
**off by default** and enabled one at a time by name:

- **Heat sink** — deploys a heat sink when your ship **overheats**. Elite Dangerous reports
  overheating above **100%** heat; the `threshold` setting is the heat percent to react at (a value
  above 100 turns it off by threshold). With the combat guard on it fires only while you're *also*
  in danger; set `reflex.combat_guard = false` if you'd rather it fire on any overheat (e.g. fuel
  scooping). Needs **DeployHeatSink** bound to a key in ED.
- **Chaff** — fires chaff when a hostile **locks on** or you're **interdicted**. Needs
  **FireChaffLauncher** bound to a key in ED.

Each reflex has its own **cooldown** (minimum seconds between fires) and there's a global
**min-interval** across all of them, so a sustained overheat or a long fight can't spam presses. If
the guard refuses a fire (status says you're safe, or it can't read your status), the cooldown is
*not* used up — a real danger trigger can still fire. Say **"abort"** to release every held key.

## Settings

| Setting | What it does |
|---------|--------------|
| `reflex.enabled` | Master switch for the **spoken/hotword** reflexes (**off** by default) |
| `reflex.combat_guard` | Permit reflexes only while in danger/interdiction; always refuse dangerous actions (leave on). Shared by every reflex path |
| `reflex.allowlist` | Spoken/hotword reflex names allowed to fire — ships **empty**; add `"chaff"` / `"heat_sink"` to opt in |
| `reflex.ptt` | Second push-to-talk for the instant fast path — a snap *"chaff!"* fires locally with no LLM. Bind a **different** key than the talk key; **blank** disables it |
| `reflex.auto.enabled` | Master switch for the **automatic** reflexes (**off** by default) |
| `reflex.auto.min_interval` | Global governor — no two auto-reflexes fire within this many seconds |
| `reflex.auto.heat_sink.enabled` | Auto-deploy a heat sink on overheat (**off** by default) |
| `reflex.auto.heat_sink.threshold` | Heat percent to react at (default `100`) |
| `reflex.auto.heat_sink.cooldown` | Minimum seconds between auto heat-sink deployments |
| `reflex.auto.chaff.enabled` | Auto-fire chaff when targeted/interdicted (**off** by default) |
| `reflex.auto.chaff.cooldown` | Minimum seconds between auto chaff bursts |

Requires [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) — the guard must
read your status to confirm you're in danger before firing, and the automatic layer reads it for the
triggers. See the [Configuration reference](../configuration.md).

## Roadmap

Chaff and heat sink — spoken, hotword, and automatic — are the foundation. Shield cell and boost are
recognised by the phrase-spotter and the guard today; wiring each to actually press its key follows
the same pattern as chaff and heat sink.
