# Blueprint & material sourcing

> *"I work out the materials an engineering blueprint needs, check your live inventory to see
> what you're short on, tell you where to farm it, and can drop the farm plan onto your checklist."*

Engineering is a materials problem. COVAS++ knows what every blueprint costs, reads the materials
you're actually carrying from the game's journal, and works out the gap — so instead of reciting a
full recipe it tells you **only what you're missing** and where to get it. Then, on your say-so, it
turns that shortfall into trackable steps on your [checklist](../using/checklist.md).

**Example:** *"What do I need for a grade 5 FSD?"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"What do I need for a grade 5 FSD?"* | The grade-5 Increased Range recipe **and** which of those materials you're short on, with where to farm each |
| *"What am I missing for grade 3 dirty drive tuning?"* | Same, for that blueprint and grade |
| *"What blueprints can I engineer on my power plant?"* | The real blueprint names available for that module |
| *"Add these to my checklist."* | Adds one trackable objective per short material — name, count, and sourcing hint |

Say the blueprint however you like — a name (*"increased range"*, *"dirty drives"*, *"heavy duty
shield booster"*) or a module and grade (*"grade 5 FSD"*). If you only name a module that has
several blueprints, COVAS++ lists them and asks which you meant rather than guessing.

Grade defaults to **5** if you don't say one; add *"grade 3"*, *"g4"*, etc. to pick another. The
recipe is the material cost **per upgrade roll** — reaching a grade takes several rolls, so treat
the shortfall as a floor and stock up.

## What "missing" means

COVAS++ reads the journal's `Materials` event — your full Raw / Manufactured / Encoded inventory —
and keeps it current as you pick up and spend materials. For the blueprint you asked about it
compares each required material against what you're holding and reports the ones you don't have
enough of, e.g.:

> *"A grade 5 Increased Range on the Frame Shift Drive needs, per upgrade roll: 1× Arsenic, 1×
> Chemical Manipulators, 1× Datamined Wake Exceptions. You're SHORT on 2 of them: Chemical
> Manipulators (you have 0) — trade at a Manufactured Material Trader, or salvage High-Grade
> Emission sources; Datamined Wake Exceptions (you have 0) — scan ship wakes…"*

The counts and the missing-material maths both come from real data (your journal + the bundled
tables), so nothing here is invented.

## Farm plan on your checklist

The differentiator: once COVAS++ has named your shortfall, tell it *"add these to my checklist"* and
it records one objective per short material — with the amount and where to source it — using the
same [checklist](../using/checklist.md) it tracks everything else with. Tick them off as you farm.

## Where the data comes from

The blueprint recipes and material sourcing hints ship **offline** in the app — nothing here calls
the network at runtime. They're regenerated from the community-maintained
[EDCD/coriolis-data](https://github.com/EDCD/coriolis-data) (recipes) and
[EDCD/FDevIDs](https://github.com/EDCD/FDevIDs) (material catalogue) — the same data Coriolis and
EDEngineer use — by `covas/ed/data/regen_engineering_data.py`. When Frontier changes a recipe, a
maintainer re-runs that script to refresh the two bundled JSON tables.

## Settings

This reads **only local journal data** — no game-account login or private API. It needs
[game-state monitoring](monitoring.md) (`[elite].enabled = true`) so your material inventory is
being watched. There's nothing else to configure.
