# Keybind automation

> *"I can toggle your landing gear on a separate spoken confirmation, with a combat safety check —
> say 'abort' any time to cancel."*

Out of the box COVAS++ will physically press exactly **one** ship control for you: **toggle
landing gear**. It's a deliberately small, heavily-guarded prototype — the point is to prove one
reliable, safe keystroke before anything more. You can opt additional benign toggles in via the
allowlist (see [Tier-1 ship-systems actions](#tier-1-ship-systems-actions-31) below).

**Example:** *"toggle my landing gear"*

!!! danger "Off by default — opt in deliberately"
    This sends **real keypresses into Elite Dangerous.** Set `[keybinds].enabled = true` to use it,
    and do your first tests **parked and docked.** Keep the safety toggles on.

## How it stays safe

Every safeguard is on by default:

- **Allowlist** — only explicitly permitted macros can run. The default allows exactly one:
  `landing_gear`. Ask for anything you haven't allowlisted ("deploy hardpoints") and it won't do it.
- **Separate-turn confirmation** — asking arms the action but does **not** fire it. You must
  confirm on a *separate* command. The model can't arm and fire in one breath, by design.
- **Combat / interdiction guard** — it refuses to touch controls while you're in danger or being
  interdicted. If it can't read your status at all, it refuses too (it won't act unless it can
  *prove* it's safe).
- **Mode gating** — it only offers and runs actions that make sense for what you're *currently*
  doing: mainship, fighter, SRV, or on foot. A ship control like landing gear isn't offered while
  you're on foot, and it's re-checked at confirm time (so if you disembark after arming, it won't
  fire). When game-state monitoring is off (COVAS can't tell your mode), gating is skipped and the
  combat guard still applies.
- **Confirmation expiry** — an armed action stops being confirmable after a timeout (60 s), so a
  stale "confirm" can't fire it later.
- **Hard abort** — say **"abort"** (or "belay that") any time to cancel a pending action and
  immediately release any held key.

## Using it

1. **Arm:** *"COVAS, toggle my landing gear."* → it says it's *armed but not done* and asks you to
   confirm separately. The gear does **not** move yet.
2. **Confirm on a separate turn:** *"Confirm."* (or *"do it"*) → the gear toggles in-game.

Three voice commands are involved:

| Command | What it does |
|---------|--------------|
| `toggle_landing_gear` | Arms the landing-gear toggle (doesn't fire) |
| `confirm_keybind` | Confirms and executes the armed action (refused in the same turn it was armed) |
| `abort_keybinds` | Hard abort — cancels anything armed and releases every held key |

## Tier-1 ship-systems actions (#31)

Beyond landing gear, COVAS++ ships a batch of **benign, repeatable main-ship toggles** — but
they're **off until you opt each one in** via the allowlist. They're benign (harmless and
repeatable), so they **fire immediately** on request rather than arming-and-confirming; the
combat/interdiction guard and mode gating still apply. None fire while you're on foot, in the
SRV, or in a fighter.

| Say something like | Macro name (allowlist) | ED control it presses |
|--------------------|------------------------|-----------------------|
| *"toggle my cargo scoop"* | `cargo_scoop` | Toggle Cargo Scoop |
| *"night vision"* | `night_vision` | Night Vision |
| *"ship lights"* | `ship_lights` | Ship Spotlight |
| *"switch HUD to analysis mode"* | `hud_mode` | HUD Combat/Analysis toggle |
| *"pips to engines"* | `pips_engines` | Increase Engines Power (one pip) |
| *"pips to weapons"* | `pips_weapons` | Increase Weapons Power (one pip) |
| *"pips to systems"* | `pips_systems` | Increase Systems Power (one pip) |
| *"balance the pips"* | `pips_balance` | Reset Power Distribution (2/2/2) |

Each pip command adds **one** pip, so ask a few times to fill a bank. **Docking request** isn't
here — it's a panel action with no direct keybind (a later panel batch handles it).

To enable one, add its **macro name** to `[keybinds].allowlist` in `config.toml` and bind the
matching control to a **key** in Elite Dangerous:

```toml
[keybinds]
allowlist = ["landing_gear", "cargo_scoop", "ship_lights", "pips_engines"]
```

Anything you don't list stays off — COVAS won't press it even if asked.

## It reads *your* bindings

COVAS++ reads your **actual** Elite Dangerous key bindings (it resolves your active preset and pulls
out the keyboard bind), so it presses whatever key *you've* bound to landing gear — portable across
setups. It injects at scancode level, which is what Elite Dangerous actually listens to. The
**Toggle Landing Gear** control must be bound to a key in-game; if it's only on a joystick, COVAS++
will say so and ask you to bind it to a key.

By default COVAS reads the **Primary** binding — which is where the keyboard key normally lives (a
joystick/HOTAS bind usually sits on Secondary). If you deliberately put COVAS's keyboard binds on the
**Secondary** slot, set `[keybinds].binding_preference = "secondary"`. Either way it falls back to the
other slot, so a keyboard bind on either one is found.

## Settings

| Setting | What it does |
|---------|--------------|
| `keybinds.enabled` | Master switch (off by default) |
| `keybinds.require_confirmation` | Require a separate spoken confirm before firing (leave on) |
| `keybinds.combat_guard` | Refuse during danger/interdiction or unknown status (leave on) |
| `keybinds.mode_guard` | Only offer/run actions valid for your current mode (ship/fighter/SRV/foot; leave on) |
| `keybinds.binding_preference` | Which `.binds` slot to read the key from: `primary` (default) or `secondary` |
| `keybinds.confirm_window` | Seconds an armed action stays confirmable |
| `keybinds.binds_file` | Override the auto-detected bindings file (rarely needed) |

Requires [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) for the combat
guard. See the [Configuration reference](../configuration.md#keybind-automation-keybinds).
