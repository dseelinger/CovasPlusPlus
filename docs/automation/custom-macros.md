# Custom macros

!!! warning "Experimental — off by default"
    Custom macros are an **experimental** feature: they ship **disabled** for everyone and are
    gated at capability registration, so a flag-off build exposes no macro tools or help. Enable
    them just for yourself by adding `experimental.macro.enabled = true` to your git-ignored
    `overrides.json` (see
    [Experimental feature flags](../configuration.md#experimental-feature-flags)) **and** setting
    `macros.enabled = true`.

> *"Create a macro called Dock ASAP. When docking is granted, throttle to zero and drop the
> landing gear."* — *"Saved 'Dock ASAP' with two steps. It'll run automatically when docking is
> granted, and I'll ask you to confirm before running it."*

Every other companion gives you a **fixed** list of things it can do. COVAS++ lets you **invent
your own** named macros — by voice or in the control panel — and it *remembers* them, runs them
on command, or fires them automatically when a game event happens. This is the headline feature:
you're not picking from a catalogue, you're *authoring* one.

A macro is a short, ordered recipe of ship actions, pauses, and game-state checks. You give it a
name; later you say **"run \<name\>"**, or you bind it to a game event so it runs hands-free.

**Example:** *"create a macro called gear up: retract the landing gear"* → then any time,
*"run gear up"*.

!!! danger "Off by default — opt in deliberately"
    A macro sends **real keypresses into Elite Dangerous.** Set `[macros].enabled = true` to use
    it, set up [keybind automation](keybinds.md) first (it presses the same keys), and do your
    first tests **parked and docked.** Keep every safety toggle on.

## Why it can't go wrong

The thing that makes "invent your own automation" safe is that a macro **cannot reference
anything you haven't already enabled.** When you author a macro, COVAS++ validates every part of
it against what it actually knows how to do:

- **Actions must be allowlisted.** A macro can only use ship actions that are in your
  `[keybinds].allowlist` (see [keybind automation](keybinds.md)). Ask for a macro that
  "launches torpedoes" or "ejects cargo" and it's **refused at authoring** — those actions
  aren't registered at all, and even a real action you haven't allowlisted is rejected with a
  message telling you to allowlist it first. COVAS++ never invents an action to satisfy a
  request.
- **Triggers must be events COVAS++ already tracks.** You can only bind to the game moments it
  folds from your journal / status (below). An unknown trigger is refused.
- **Status checks must be flags it actually reads.** A "wait until docked" step can only check a
  real status flag.
- **One game mode per macro.** A macro that mixes, say, a ship control and an on-foot control can
  never run (you're never both at once), so it's refused rather than saved to misfire later.

If anything doesn't check out, **nothing is saved** and COVAS++ tells you exactly why, listing the
real options. This validation is *structural* — it isn't the AI "promising" to be careful, it's a
gate the macro has to pass.

On top of that, a saved macro runs behind the **same safety layer** as every other ship control:

- **Confirmation** — a consequential macro (one whose actions need confirming, like landing gear)
  **arms** and waits for a *separate* spoken "confirm" before it runs. A benign one (throttle,
  panels) runs immediately. When a *trigger* fires a consequential macro, COVAS++ doesn't just do
  it — it tells you the trigger happened and asks you to confirm. If **two** consequential macros
  share the same trigger, COVAS++ arms the first and **queues** the second (telling you it did),
  then offers it for a separate "confirm" once you've dealt with the first — so a shared trigger
  never silently drops one of them. Saying **"abort"** clears the whole queue.
- **Combat / interdiction guard** — a macro refuses to run while you're in danger or being
  interdicted, or when COVAS++ can't read your status at all (it won't act unless it can *prove*
  it's safe).
- **Mode gating** — a macro only runs in the game mode its actions belong to.
- **Hard abort** — say **"abort"** any time to cancel an armed/pending macro and immediately
  release every held key. It shares one abort with the rest of ship control, so "abort" stops
  *everything* — a running keybind sequence and any triggered macro alike. Each run tracks its
  own stop signal, so a macro that starts (or auto-triggers) at the same instant you say "abort"
  can never cancel out an abort meant for a sequence already in progress.

## Authoring a macro by voice

Just describe it. COVAS++ gathers the name, the ordered steps, and (optionally) a trigger, then
validates and saves it:

> **You:** "Create a macro."
> **COVAS:** "Sure — what should I call it?"
> **You:** "Call it Dock ASAP. When docking is granted, throttle to zero and put the gear down."
> **COVAS:** "Saved 'Dock ASAP'… it'll run automatically when docking is granted, and I'll ask you
> to confirm before running it."

You can also manage them by voice: *"list my macros"*, *"run Dock ASAP"*, *"delete Dock ASAP"*.

## Authoring a macro in the control panel

Open the control panel and click **🎛 macros**. The editor lets you:

- name the macro,
- add ordered steps (an **action** from your allowlist, a **wait**, a **require status** check, or
  an **await status** wait),
- optionally pick a **trigger**,
- choose whether it needs a spoken confirm.

The dropdowns only offer real, allowlisted actions and known triggers/status flags, and the
server re-validates with the exact same gate the voice path uses — so a web-authored macro is
just as locked-down. Saved macros show up here and in voice immediately (they share one file).

## Step kinds

| Step | What it does |
|------|--------------|
| **action** | Perform one allowlisted ship action (e.g. `throttle_zero`, `landing_gear`). |
| **wait** | Pause for N seconds. |
| **require status** | A **precondition** — refuse the whole macro *now* unless a game flag matches (e.g. "require `docked` = false"). |
| **await status** | **Block** until a flag matches, or fail after a timeout (e.g. "await `landing_gear` = true"). |

The status checks are what make a macro *non-blind*: instead of firing keys and hoping, a macro
can verify game state between steps (this reuses the [status-checked sequence
framework](keybinds.md)).

## Triggers

Bind a macro to one of the game moments COVAS++ already folds, and it runs hands-free when that
moment happens:

| Trigger | Fires when… |
|---------|-------------|
| `supercruise_exit` | you drop out of supercruise |
| `supercruise_entry` | you enter supercruise |
| `docked` | you dock |
| `undocked` | you undock |
| `docking_granted` | docking is granted |
| `arrival` | you arrive in a new system (an FSD jump completes) |
| `landing_gear_down` | your landing gear deploys |
| `low_fuel` | your fuel drops below 25% |
| `overheating` | your ship starts overheating |

Danger and interdiction are deliberately **not** offered as triggers — a macro would just be
refused by the combat guard, so binding to them would be pointless. For combat, use the separate
[combat reflexes](reflexes.md).

## Status flags you can check

`docked`, `landing_gear`, `supercruise`, `hardpoints`, `low_fuel`, `analysis_mode`, `in_danger`,
`being_interdicted`. Each can be required to be true or false. If you hand-edit the saved macros
file, the `expect` (and `confirm`) fields accept `true`/`false` written as real JSON booleans **or**
as the strings `"true"`/`"false"`/`"yes"`/`"no"`/`"1"`/`"0"` — so a stray `"false"` reads as false
rather than silently flipping the check.

## Configuration

```toml
[macros]
enabled = false            # off by default — opt in deliberately
require_confirmation = true # consequential macros need a separate spoken confirm
combat_guard = true         # refuse during danger/interdiction (or unknown status)
mode_guard = true           # only run in the macro's game mode
confirm_window = 60         # seconds an armed macro stays confirmable
file = "custom_macros.jsonl" # where saved macros live (git-ignored — it's your content)
```

Custom macros need [keybind automation](keybinds.md) set up (bound keys + an allowlist) to
actually press anything, and [game-state monitoring](../elite/monitoring.md) for the combat guard
and for triggers to fire.

## What's *not* here yet

Two kinds of macro condition are genuinely out of reach today and are tracked as spikes, not built:

- **Continuous-distance conditions** — "boost when we're within 7.5 km of the station." Elite
  Dangerous doesn't stream a live distance-to-target, so there's nothing to threshold on. Tracked
  as a Tier-2 spike.
- **Analog / spatial actions** — "boost *toward* the station", "line up on the pad." These need
  visual aiming COVAS++ doesn't do (it never flies the ship). Tracked as a Tier-3 spike.

See the [roadmap](../roadmap.md) for where these sit.
