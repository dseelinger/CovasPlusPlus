# Minor factions

> *"I find the nearest system where a minor faction is present or in control, or by faction
> allegiance, government, or state, and copy the system to your clipboard."*

Find the nearest **system** where a minor faction lives — either just *present*, or actually *in
control*.

**Example:** *"find the nearest system where the Dark Wheel is present"*

> *When it's mid-search:* "Name the faction — and say whether it should control the system or just
> be present — and I'll find the nearest match."

## What you can filter on

| Filter | Say something like… | What it finds |
|--------|---------------------|---------------|
| **Present** | *"where the Dark Wheel is present"* | Systems where a named faction has any presence |
| **In control** | *"controlled by the Dark Wheel"* | Systems a named faction actually controls |
| **Allegiance** | *"a nearby Independent faction system"* | By faction allegiance — Federation, Empire, Alliance, Independent |
| **Government** | *"a Cooperative faction nearby"* | By faction government — Democracy, Corporate, Cooperative… |
| **Faction state** | *"the nearest faction at war"* | By active faction state — War, Boom, Election, Expansion… |

"Present" is the default — say so if you specifically want *control* instead. Spoken polarity
("present" vs. "in control") just flips the filter.

## Faction names are validated

Faction names are matched against known factions before a search runs. An unknown name triggers a
[recovery suggestion](../using/help.md#3-failure-recovery-the-important-one) ("did you mean…?")
rather than a bogus search — it never invents a faction.

!!! tip "War, boom, election — as a *system* search"
    If you care more about the *state* than the specific faction (nearest war, nearest boom), use
    [Faction states](faction-states.md) instead.

## Settings

Part of the `[search]` group:

| Setting | What it does |
|---------|--------------|
| `search.enabled` | Master switch for the station/faction/signal/state searches |
| `search.search_size` | How many nearby matches to fetch; the closest is the answer |

See the [Configuration reference](../configuration.md#navigation-search-nav-star_systems-search).
