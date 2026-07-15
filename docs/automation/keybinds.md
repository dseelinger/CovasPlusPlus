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
## Odyssey on-foot actions (opt-in)

When you're **disembarked** (on foot in Odyssey), COVAS++ can press a handful of **benign, utility**
controls for you. These are **mode-gated**: they're only offered while you're actually on foot, and
never while you're flying — just as landing gear is never offered on foot. This is the improvement
over a flat action list: the companion offers what makes sense for what you're *currently* doing.

They're all benign (a toggle or a selection — nothing here ever *fires a weapon or throws a
grenade*, which are out of scope on purpose), so unlike landing gear they fire **immediately** on
request (no separate confirmation) — still behind the allowlist, the combat guard, and the mode
gate. **None is enabled by default:** add the ones you want to `[keybinds].allowlist` by name.

| Macro name (allowlist) | Voice tool | What it does | ED binding |
|---|---|---|---|
| `on_foot_flashlight` | toggle flashlight | Suit flashlight on/off | Toggle Flashlight |
| `on_foot_night_vision` | toggle night vision | Suit night vision on/off | Toggle Night Vision |
| `on_foot_select_primary` | draw primary weapon | Select primary weapon | Select Primary Weapon |
| `on_foot_select_secondary` | draw secondary weapon | Select secondary weapon | Select Secondary Weapon |
| `on_foot_select_utility` | draw utility weapon | Select utility weapon | Select Utility Weapon |
| `on_foot_holster` | holster weapon | Holster / hide weapon | Hide Weapon |
| `on_foot_energylink` | switch to energy link | Select the recharge tool | Switch to Recharge Tool |
| `on_foot_profile_analyser` | switch to profile analyser | Select the profile analyser | Switch to Comp. Analyser |
| `on_foot_suit_tool` | switch to suit tool | Select the suit tool | Switch to Suit Tool |
| `on_foot_crouch` | toggle crouch | Crouch | Crouch |
| `on_foot_galaxy_map` | open galaxy map | Open the galaxy map on foot | Galaxy Map (on foot) |

Each control must be **bound to a key** in Elite Dangerous (Controls → On Foot); a joystick-only
bind can't be pressed, and COVAS will say so. Example allowlist:

```toml
[keybinds]
allowlist = ["landing_gear", "on_foot_flashlight", "on_foot_night_vision"]
```

!!! note "The combat guard still applies on foot"
    Even though these are benign, COVAS won't press them while your suit reports **danger** (or when
    it can't read your status). That's the same conservative guard as the ship controls.
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
