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

- **Allowlist** — only explicitly permitted macros can run. It ships allowing exactly one:
  `landing_gear`. Ask for anything else ("deploy hardpoints") and it won't do it, unless you
  add that macro's name to `[keybinds].allowlist` yourself (see *More actions* below).
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

## More actions (Tier-1: panels, maps, fire groups)

Beyond landing gear, COVAS++ ships a set of **benign, repeatable** cockpit actions you can opt
into. Opening a panel or map, cycling a fire group, or toggling head-look changes nothing about
your ship's state and is instantly reversible — so unlike landing gear these **fire immediately**
(no separate confirmation). They're still behind the **allowlist**, the **combat/interdiction
guard**, and **mode gating** (all are main-ship actions; fire-group cycling also works in a
deployed fighter).

They are **off until you allowlist them.** Add the ones you want to `[keybinds].allowlist`, e.g.:

```toml
allowlist = ["landing_gear", "open_galaxy_map", "focus_left_panel"]
```

| Macro name | Says | ED control it presses |
|------------|------|-----------------------|
| `focus_left_panel` | *"open the nav panel"* | Focus Left Panel (navigation/target) |
| `focus_right_panel` | *"open the systems panel"* | Focus Right Panel (systems) |
| `focus_comms_panel` | *"open comms"* | Focus Comms Panel |
| `focus_role_panel` | *"open the role panel"* | Focus Role Panel (radar/role) |
| `quick_comms` | *"quick comms"* | Quick Comms Panel |
| `open_galaxy_map` | *"open the galaxy map"* | Galaxy Map (main-ship) |
| `open_system_map` | *"open the system map"* | System Map (main-ship) |
| `cycle_fire_group_next` | *"next fire group"* | Cycle Fire Group Next |
| `cycle_fire_group_previous` | *"previous fire group"* | Cycle Fire Group Previous |
| `ui_back` | *"go back"* | UI Back |
| `ui_focus` | *"toggle UI focus"* | UI Focus |
| `toggle_headlook` | *"toggle head-look"* | Head Look Toggle |

Each control must be **bound to a key in-game** (COVAS presses *your* binding); if it's only on a
joystick, COVAS will say so. On-foot and SRV variants of the panels and maps come with later
tiers (they use different ED controls). `open_galaxy_map` is also the first building block of the
future "set course" voice-plot handoff (it opens the map the destination gets typed into).

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
