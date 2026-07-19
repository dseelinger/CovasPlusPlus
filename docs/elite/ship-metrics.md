# Ship metrics: jump range & fleet ranking

> *"I compute numbers about your real, engineered fleet — your current jump range accounting for
> cargo, or a ranking of your ships by jump range — from each ship's remembered loadout, so you
> don't have to open Coriolis."*

COVAS++ can **compute** questions about your ships instead of just reading back a stored figure:
your **current jump range** right now (accounting for what you're carrying), and a **ranking of your
fleet** by jump range. The numbers come from your *own* ships — each ship's real modules and applied
engineering ([remembered per ship](engineering-planning.md)), your current cargo and fuel, and
bundled ship specs — never invented.

**Example:** *"what's my current jump range"* · *"top three small ships by jump range"*

## What you can ask

| You say… | It does… |
|----------|----------|
| *"What's my current jump range?"* | Computes the **live** figure for the ship you're flying, from its fitted FSD + engineering + **current cargo and fuel**, and states the load basis |
| *"What's my range with a full tank?"* | Same ship, quoted at the reference load (full tank, no cargo) |
| *"What's the jump range of my Anaconda?"* | Computes a named owned ship at the reference load (only the current ship's live cargo is known) |
| *"Top three ships by jump range"* | Ranks your owned ships (that have a remembered build) by jump range, best first |
| *"Top three **small** ships by jump range"* | Same, filtered to a landing-pad size class (small / medium / large) |
| *"Which of my ships jumps furthest?"* | The top of that ranking |

A ship COVAS++ **hasn't seen a build for yet** is reported as *unknown* ("fly it once and I'll have
its range") — it is never guessed.

## The cargo & reference-load basis

Jump range depends on **mass**, and mass depends on what's loaded — so the answer always states the
basis it used:

- **Your current ship** is computed **laden**, at your *actual* cargo and fuel. Load up 100t of cargo
  and ask again — the figure drops, because the ship is heavier. That is the honest "right now"
  number.
- **Any other ship** is computed at a **consistent reference load — a full main tank and empty
  cargo** (the usual quoted "jump range" basis). Live cargo is only knowable for the ship you're
  actually flying, so ranking every ship at the same reference load keeps the comparison fair. The
  ranking answer says so.

## How the number is computed

COVAS++ uses the standard Elite Dangerous FSD equation over your ship's real drive:

```
jump_range = optimal_mass / total_mass × (max_fuel / fuel_mul) ^ (1 / fuel_power)
             + Guardian FSD booster bonus (if one is fitted)
```

- **The FSD** is read from your loadout (its class and rating). Its **engineering** — Increased
  Range, Mass Manager, Deep Charge — comes straight from the journal's own modifiers, so an
  engineered drive uses your *engineered* optimal mass and max fuel, not a stock guess.
- **A Guardian FSD Booster** adds its flat per-size bonus when fitted.
- **Total mass** is hull + every fitted module + fuel + cargo.

### A note on module masses (approximation)

Your loadout and the bundled specs record *which* modules are fitted, but **not each module's
individual mass**. Rather than guess those, COVAS++ **calibrates the ship's dry mass from the game's
own maximum jump range** (which the game computed *with* the real module masses): inverting the
equation at that figure recovers a dry mass that already accounts for everything fitted, and then only
fuel and cargo — the two things that change and that COVAS++ *does* know — are varied. This makes the
current-ship figure track the in-game FSD panel closely (within your cargo), without any per-module
mass data.

If a ship's remembered build predates that data (no stored maximum jump range), COVAS++ falls back to
a **hull-mass-only** estimate and **flags the result as rough** rather than quoting false precision. If
even the hull mass is unknown (an unrecognized ship), the range is reported as **unknown** — COVAS++
never substitutes a smaller figure like fuel capacity for the missing mass, which would overstate the
range dramatically.

## The metric registry (extensibility)

Under the hood this is a **pluggable ship-metric registry**. The query surface — *"my current
&lt;metric&gt;"* and *"top N &lt;class&gt; ships by &lt;metric&gt;"* — is **metric-agnostic**: it
dispatches through the registry by the metric's spoken name and knows nothing about what any metric
*is*. Jump range is the first (and, today, only) metric.

Adding another — DPS, shield strength, cargo capacity, top speed — is a **new registry entry plus its
`compute` function**, with no change to the query tools, the ranking, or the voice surface. That is
deliberate: the measurement plumbing is built once so future metrics are cheap.

## Grounding & fail-soft

Every number is **computed from your real loadout and spec data and relayed** — nothing is fabricated.
A ship with no remembered build is reported unknown, an unfamiliar drive is reported rather than
guessed, and any error is spoken, never crashing the voice loop.

## Settings

This needs [game-state monitoring](monitoring.md) (`[elite].enabled = true`) so the journal is
watched, and it builds on your [owned ships](owned-ships.md) fleet and
[per-ship loadout memory](engineering-planning.md). There are no settings of its own — fly your ships
once so their builds are remembered, and the metrics follow.
