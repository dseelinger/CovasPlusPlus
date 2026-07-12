# Route callouts

When you're flying a plotted galaxy-map route, COVAS++ can give you hands-free heads-ups as you
jump — so you don't have to keep checking the map. It reads your plotted route from `NavRoute.json`
(the full jump list, with star classes) and follows your progress via each jump.

!!! info "Opt-in"
    Off by default — set `[route].enabled = true`. Requires
    [game-state monitoring](monitoring.md) (`[elite].enabled = true`). Route callouts go through
    the same [proactive path](proactive-callouts.md): spoken only when idle, cancelable with a tap,
    and silenced by the proactive mute too.

## The three callouts

| Callout | What you hear |
|---------|---------------|
| **Scoopable star** | As you lock/enter the next jump: whether the next star can be fuel-scooped ("Next star's scoopable." / "…isn't scoopable. Top off your fuel if you're low.") |
| **Jumps remaining** | Every Nth jump (default every 5): how many jumps are left to your destination |
| **Arrival** | On reaching the final system: "Arrived at *system*. Route complete." |

Scoopable stars are the KGBFOAM classes (K, G, B, F, O, A, M) — the callout warns you *before* you
arrive somewhere you can't refuel.

Each callout kind can be toggled independently, so you can keep the scoopable heads-up and silence
the jump counter, or vice versa.

## Replotting

Plot a new route mid-flight and the callouts follow the new route — the counts reset
automatically. Reach the end and it announces arrival and stops.

## Settings

| Setting | What it does |
|---------|--------------|
| `route.enabled` | Master switch for route callouts |
| `route.every_n` | Announce jumps-remaining every Nth jump (lower = chattier) |
| `route.callout_scoopable` | Announce whether the next star is scoopable |
| `route.callout_jumps_remaining` | Announce jumps remaining |
| `route.callout_arrival` | Announce arrival at the destination |

See the [Configuration reference](../configuration.md#route-callouts-route).
