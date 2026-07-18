# Route callouts

When you're flying a plotted galaxy-map route, COVAS++ can give you hands-free heads-ups as you
jump — so you don't have to keep checking the map. It reads your plotted route from `NavRoute.json`
(the full jump list, with star classes) and follows your progress via each jump.

!!! info "Opt-in"
    Off by default — set `[route].enabled = true`. Requires
    [game-state monitoring](monitoring.md) (`[elite].enabled = true`). Route callouts go through
    the same [proactive path](proactive-callouts.md): spoken only when idle, cancelable with a tap,
    and silenced by the proactive mute too.

## The callouts

| Callout | What you hear |
|---------|---------------|
| **Scoopable star** | As a target locks in: whether the star you're *arriving at* can be fuel-scooped, naming the right hop — see below |
| **Hazard star** | When the arriving star is a neutron star or white dwarf: a warning naming the hazard, *instead of* the plain "not scoopable" line |
| **Jumps remaining** | Every Nth jump (default every 5): how many jumps are left to your destination |
| **Arrival** | On reaching the final system: "Arrived at *system*. Route complete." |

Scoopable stars are the KGBFOAM classes (K, G, B, F, O, A, M) — the callout warns you *before* you
arrive somewhere you can't refuel.

### Which star it's talking about

In a plotted route, Elite Dangerous locks the galaxy-map target for the hop *after* the one you're
currently flying to — so a callout that just said "next star" could describe the wrong hop. COVAS++
anchors the wording to your actual position on the route instead, and always names which star it
means:

- **Arriving star scoopable, the one after isn't** — "This star's scoopable — but the one after
  isn't, so top off here before you jump on."
- **Arriving star not scoopable** — "Heads up — the star you're jumping to isn't scoopable."
  (unambiguously *this* jump's destination)
- **Both scoopable** — the brief "Next star's scoopable."

This is safe to speak mid-hyperspace — it isn't delayed to avoid overlapping with the jump.

### Hazard warning (neutron stars & white dwarfs)

Neutron stars and white dwarfs both have exclusion-zone jets that can damage your ship, and neither
can be fuel-scooped. When the arriving star is one of these, COVAS++ speaks a warning naming the
hazard *instead of* the plain scoopable callout (no redundant "not scoopable" right before it):

- Neutron star: "Heads up, Commander — next jump's a neutron star. Mind the exclusion zone, and no
  fuel there."
- White dwarf: "Careful — a white dwarf next. Watch the jets; you can't scoop it."

Turn this off with `route.callout_hazard = false` if you deliberately neutron-jump for the FSD
supercharge and want it quieter — the scoopable callout still fires for those stars once the
hazard warning is off (they're non-scoopable, so you'll still hear "isn't scoopable").

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
| `route.callout_scoopable` | Announce whether the arriving star (and the one after it) is scoopable |
| `route.callout_hazard` | Warn when the arriving star is a neutron star or white dwarf |
| `route.callout_jumps_remaining` | Announce jumps remaining |
| `route.callout_arrival` | Announce arrival at the destination |

See the [Configuration reference](../configuration.md#route-callouts-route).
