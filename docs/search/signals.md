# Signals & structures

> *"I find the nearest structure — a megaship, settlement, outpost, or starport — and copy its
> system to your clipboard."*

Find the nearest **structure** of a given kind.

**Example:** *"find the nearest megaship"*

> *When it's mid-search:* "Tell me the structure — a megaship, settlement, and so on — and I'll find
> the nearest one."

## What you can find

| Say something like… | Finds |
|---------------------|-------|
| *"the closest settlement"* | Nearest settlement |
| *"find the nearest megaship"* | Nearest megaship |
| *"the nearest outpost"* | Nearest outpost |
| *"closest starport"* | Nearest starport |

Structure types include **megaship, settlement, outpost, starport, planetary port, and asteroid
base**. Name the kind you want and COVAS++ finds the nearest one from your current system and copies
its system to your clipboard.

## Settings

Part of the `[search]` group:

| Setting | What it does |
|---------|--------------|
| `search.enabled` | Master switch for the station/faction/signal/state searches |
| `search.search_size` | How many nearby matches to fetch; the closest is the answer |

See the [Configuration reference](../configuration.md#navigation-search-nav-star_systems-search).
