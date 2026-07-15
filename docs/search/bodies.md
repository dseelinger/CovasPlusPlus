# Body finder

> *"I find the nearest body — an Earth-like world, ammonia or water world, or one with a given
> biological signal — and copy its system to your clipboard for the galaxy map."*

Find the nearest **body** (planet or moon) by its **type** or its **biological signals** — a
targeted, single-body lookup. Ask for the nearest Earth-like world, the closest ammonia or water
world, or a body with a particular exobiology signal, and COVAS++ queries
[Spansh](https://spansh.co.uk/bodies) from your **current system**, tells you which body and where,
and copies its **system** to your clipboard for the galaxy map.

**Example:** *"find the nearest Earth-like world"* — or *"the closest body with Bacterium signals"*

> *When it's mid-search:* "Tell me a body type — like an Earth-like world — or a biological signal
> like Bacterium, and I'll find the nearest one."

!!! note "Off by default"
    Set `[bodies].enabled = true` to turn it on. It needs
    [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) to know your current
    system — or just tell COVAS a system to search *near*.

!!! tip "Route, not a single body? Use Road to Riches"
    This finds **one** nearest body. If you want a whole **run** of systems to scan for exploration
    credits, that's the [Road-to-Riches planner](road-to-riches.md).

## What you can find

| Say something like… | Finds |
|---------------------|-------|
| *"the nearest Earth-like world"* | Nearest ELW |
| *"the closest ammonia world"* | Nearest ammonia world |
| *"nearest landable body with Bacterium"* | Nearest landable body with any *Bacterium* signal |
| *"a body with Bacterium Aurasus"* | Nearest body with that exact species |
| *"the closest body with any biological signal"* | Nearest body with any exobiology signal |
| *"an Earth-like world close to the star"* | Nearest ELW within a short arrival distance |

### Body types

Earth-like world, ammonia world, water world, water giant, high-metal-content world, metal-rich
body, rocky body, rocky-ice world, icy body, and the gas-giant classes (Class I–V, helium, and the
ammonia-/water-based-life giants).

### Biological signals of type X

Name an **exobiology genus** — *Bacterium, Stratum, Tussock, Aleoida, Cactoida, Clypeus, Concha,
Electricae, Fonticulua, Frutexa, Fumerola, Fungoida, Osseus, Recepta, Tubus* — and COVAS++ finds a
body listing any of that genus's species. Name a **specific species** (*"Bacterium Aurasus"*) to
pin it exactly, or say **"any biological"** to match any exobiology signal at all. Because that
signal data is crowd-sourced, a match on a very old survey comes with a gentle "that scan is N days
old" caveat.

You can also require a **landable** body (needed to scan surface biology on foot) and cap the
**distance from the star** in light-seconds.

## How it works

1. **You're the reference.** Results are nearest-first from your current system (or say *"near
   \<system\>"* to measure from somewhere else).
2. **Say a type and/or a biological signal.** Unspoken filters mean *any*. A misheard type or genus
   is **corrected**, not guessed — it's checked against a bundled canonical vocabulary (baked from
   the live Spansh API) before any query.
3. **It speaks the match:** the body, its system, the distance, and how far it sits from the star —
   plus, for a biology search, the confirmed signal.
4. **It copies the system** to your clipboard — paste it into the galaxy-map search to set course.
   (There's no per-*body* plot; you plot the system, then fly in.)

## Settings

| Setting | What it does |
|---------|--------------|
| `bodies.enabled` | Master switch (off by default) |
| `bodies.search_size` | How many nearby matches to fetch; the closest is the answer |

Requires [game-state monitoring](../elite/monitoring.md) for the current-system reference. See the
[Configuration reference](../configuration.md).
