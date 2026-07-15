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

## SRV / buggy controls (opt-in)

Beyond landing gear, COVAS++ can press the useful **SRV** controls while you're driving the
surface buggy. They're **mode-gated to the SRV** — offered *only* while you're actually driving,
never while flying the ship or on foot — and each must be added to the allowlist by name (none
are on by default). EDCoPilot/COVAS:NEXT narrate the SRV but don't drive its controls; this
toggles them hands-free behind the same safety layer.

| Macro name | Does | Confirmation |
|------------|------|--------------|
| `drive_assist` | Toggle SRV drive assist | Fires immediately (benign) |
| `srv_headlights` | Toggle headlights | Fires immediately (benign) |
| `srv_night_vision` | Toggle night vision | Fires immediately (benign) |
| `srv_cargo_scoop` | Toggle cargo scoop | Fires immediately (benign) |
| `srv_auto_brake` | Toggle auto-brake | Fires immediately (benign) |
| `recall_ship` | Recall / dismiss your ship | **Arms-and-confirms** (disruptive) |

The benign toggles just flip a convenience state, so with `require_confirmation` on they still
fire on a single command (they're covered by the allowlist, combat and mode guards). **Recall/dismiss
ship** summons or sends away your mothership, so it always arms-and-confirms like landing gear.

To enable, add the ones you want to `[keybinds].allowlist` — for example:

```toml
allowlist = ["landing_gear", "drive_assist", "srv_headlights", "srv_night_vision", "srv_cargo_scoop", "srv_auto_brake", "recall_ship"]
```

Combat controls (SRV weapons and the turret) are deliberately **not** exposed. As always, the
matching ED bindings (Drive Assist, Headlights, Night Vision, Cargo Scoop, Auto Brake, Recall/Dismiss
Ship) must be bound to a **key** in-game for COVAS to press them.

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
