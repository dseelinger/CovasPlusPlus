# Keybind automation

> *"I can toggle your landing gear on a separate spoken confirmation, with a combat safety check —
> say 'abort' any time to cancel."*

This is the **one** ship control COVAS++ will physically press for you: **toggle landing gear**.
It's a deliberately small, heavily-guarded prototype — the point is to prove one reliable, safe
keystroke before anything more.

**Example:** *"toggle my landing gear"*

!!! danger "Off by default — opt in deliberately"
    This sends **real keypresses into Elite Dangerous.** Set `[keybinds].enabled = true` to use it,
    and do your first tests **parked and docked.** Keep the safety toggles on.

## How it stays safe

Every safeguard is on by default:

- **Allowlist** — only explicitly permitted macros can run. The prototype allows exactly one:
  `landing_gear`. Ask for anything else ("deploy hardpoints") and it won't do it.
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

## Tier-1 flight & navigation actions (#30)

Beyond landing gear, COVAS++ ships a **flight/navigation** action batch. Every action is still
behind the full safety layer, and **none are on by default** — add the ones you want to
`[keybinds].allowlist`, and bind each to a **key** in Elite Dangerous. Consequential actions
(starting a jump, engaging supercruise, flipping flight assist) still **arm-and-confirm** on a
separate command; benign, repeatable ones (throttle, target cycling, nav-lock) **fire
immediately** — but only after the allowlist, combat guard, and mode gate pass.

| Voice action (allowlist name) | Does | Mode | Confirm? |
|---|---|---|---|
| `throttle_zero` / `throttle_50` / `throttle_100` | Set throttle to 0 / 50% / full | ship or fighter | fires immediately |
| `frame_shift_drive` | Engage the FSD (supercruise, or jump if a system is targeted) | ship | arm-and-confirm |
| `supercruise` | Engage supercruise | ship | arm-and-confirm |
| `hyperspace` | Jump to the targeted system | ship | arm-and-confirm |
| `flight_assist` | Toggle flight assist | ship or fighter | arm-and-confirm |
| `select_target_ahead` | Target the ship directly ahead | ship or fighter | fires immediately |
| `cycle_next_target` / `cycle_previous_target` | Cycle through targets | ship or fighter | fires immediately |
| `target_next_route_system` | Target the next system in your route | ship | fires immediately |
| `nav_lock` | Toggle nav lock | ship | fires immediately |

**To enable, for example:**

```toml
[keybinds]
enabled = true
allowlist = ["landing_gear", "throttle_zero", "cycle_next_target", "target_next_route_system"]
```

Then bind the matching controls in ED (throttle sets under *Flight Throttle*, targeting under
*Targeting*, FSD/supercruise under *Flight Miscellaneous*). Anything you leave off the allowlist
won't be offered or run, even if you ask for it. Consequential actions still need a separate
"confirm"; the combat guard and mode gate apply to all of them.

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
