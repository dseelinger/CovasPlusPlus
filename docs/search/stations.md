# Stations

> *"I find the nearest station by its type, services, landing pad, distance from the star, or
> controlling faction, and copy its system to your clipboard."*

Find the nearest **station** by what it *is* and what it *offers*.

**Example:** *"find the nearest station with a shipyard and a large pad"*

> *When it's mid-search:* "Tell me the type, services, or pad you need — and say 'no carriers' to
> leave out fleet carriers — and I'll find the nearest station."

## What you can filter on

| Filter | Say something like… | What it restricts |
|--------|---------------------|-------------------|
| **Station type** | *"the nearest Orbis Starport"* | A type — Coriolis Starport, Outpost, Planetary Port, Mega ship… |
| **Services** | *"somewhere with a material trader"* | Required services — Shipyard, Outfitting, Market, Material Trader, Interstellar Factors… |
| **Controlling faction** | *"a station controlled by the Dark Wheel"* | Stations controlled by a named minor faction |
| **Landing pad** | *"somewhere with a large pad"* | A pad size — small, medium, or large |
| **Distance from star** | *"a station close to the star"* | Stations within a short supercruise of the main star |

## Handy defaults and toggles

- **Surface stations** (including Odyssey settlements) are included by default.
- **Fleet carriers** are included, but say **"no carriers"** to leave them out in one word.
- **"Close to the star"** means within about 1000 Ls.

## Looking for a ship or a module?

If your request names a **ship** or an **outfitting module**, COVAS++ routes it to
[shipyard search](shipyards.md) or [outfitting search](outfitting.md) instead — those return
stations too, and they resolve the exact item first. You don't have to think about it; just ask for
what you want.

## Settings

Station search is part of the `[search]` group (shared with factions, signals, and states):

| Setting | What it does |
|---------|--------------|
| `search.enabled` | Master switch for the station/faction/signal/state searches |
| `search.search_size` | How many nearby matches to fetch; the closest is the answer |

See the [Configuration reference](../configuration.md#navigation-search-nav-star_systems-search).
