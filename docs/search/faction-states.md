# Faction states

> *"I find the nearest system by its controlling faction's state — wars, civil wars, boom, election,
> infrastructure failure — and copy the system to your clipboard."*

Find the nearest **system** by what its controlling faction is *doing* — useful for chasing the
missions and opportunities a given state generates.

**Example:** *"find the nearest system at war"*

> *When it's mid-search:* "Tell me the state — war, boom, election — or the kind of missions you
> want, and I'll find the nearest system."

## What you can filter on

| Filter | Say something like… | What it finds |
|--------|---------------------|---------------|
| **Faction state** | *"the nearest civil war"* | By the controlling faction's state — War, Civil War, Boom, Election, Infrastructure Failure… |
| **Controlling faction** | *"where the Dark Wheel is in control"* | Narrow to a named controlling faction |
| **Allegiance** | *"a nearby Empire system at war"* | Narrow by allegiance — Federation, Empire, Alliance, Independent |
| **Powerplay state** | *"a Fortified system in boom"* | Narrow by Powerplay state — Stronghold, Fortified, Exploited, Unoccupied |

This is the "misc" search: nearest wars and civil wars, boom/election systems, infrastructure
failures, and the missions those states tend to spawn (restore, mining, massacre, and so on).

## How it differs from minor-faction search

- **Faction states** (this page) — "find me a *war*," regardless of who's fighting.
- [**Minor factions**](minor-factions.md) — "find me a system with *this specific faction*."

## Settings

Part of the `[search]` group:

| Setting | What it does |
|---------|--------------|
| `search.enabled` | Master switch for the station/faction/signal/state searches |
| `search.search_size` | How many nearby matches to fetch; the closest is the answer |

See the [Configuration reference](../configuration.md#navigation-search-nav-star_systems-search).
