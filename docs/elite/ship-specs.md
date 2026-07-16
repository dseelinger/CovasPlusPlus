# Ship specifications

> *"I look up any ship's real specs — pad size, hull mass, hardpoints, slots, cargo capacity —
> from a bundled dataset, so even the newest hulls are accurate."*

Ask COVAS++ what any Elite Dangerous ship *is* and it answers from a **bundled specification
dataset**, not from the language model's memory. That distinction matters: a model's ship
knowledge is frozen at its training cutoff, so the newest hulls — Panther Clipper Mk II, Python
Mk II, Type-8 Transporter, Mandalay, Cobra Mk V, Corsair — are otherwise unknown or confidently
wrong. Grounding the answer in real data means it stays right as Frontier ships more.

**Example:** *"how much cargo can a Type-8 carry"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"How much cargo can a Type-8 carry?"* | Maximum cargo capacity (every optional slot as a cargo rack) |
| *"What pad does a Mandalay need?"* | Landing-pad size, plus manufacturer and hull mass |
| *"How many hardpoints has the Corsair?"* | Weapon hardpoints by size and utility mount count |
| *"What are the specs on a Python Mk II?"* | A full rundown — pad, hull mass, hardpoints, slots, cargo, speed, shields, armour |

It covers the same ships COVAS++ can already find in a [shipyard](../search/shipyards.md), and
resolves loose or misheard names the same way — say *"conda"* and it knows the Anaconda, say
*"panther"* and it knows the Panther Clipper Mk II. For a **family** (Krait, Cobra, Viper, Asp,
Diamondback, or a Type-*n*) it asks which model rather than guessing.

## What it won't do

COVAS++ **won't invent numbers.** If you ask about a hull it has no bundled data for, it says so
and offers to web-search instead of making something up.

**Jump range** is deliberately not in the dataset. Unlike hull mass or slot layout, jump range
isn't a fixed property of the hull — it depends on the frame shift drive you fit, your ship's
mass, and your fuel. For **your own ship**, ask about your [loadout](loadout.md) and COVAS++ reads
the real figure from the journal; for any other ship it will web-search rather than guess.

## Always available

Unlike the [loadout](loadout.md) reader, ship-spec lookup needs **no game-state monitoring** and
no login — the dataset is bundled and offline, so it works any time, even out of the game.

## Refreshing the data

The dataset is baked from the community-maintained
[EDCD/coriolis-data](https://github.com/EDCD/coriolis-data) ship files — the same lineage
Coriolis and EDSY use. When Frontier releases a new hull, refreshing is a single command
(`scripts/gen_ship_specs.py`); no per-release code change is needed.
