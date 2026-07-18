# Materials inventory

> *"I read your live engineering-materials inventory straight from the journal — how many of
> one you're holding, a bucket listing, or what you're capped on."*

COVAS++ already reads your full Raw / Manufactured / Encoded materials inventory from the game's
journal to answer [blueprint](blueprints.md) shortfalls — this is the DIRECT query on top of that
same live data, for when you just want to know what you're carrying.

**Example:** *"How many chemical manipulators do I have?"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"How many arsenic do I have?"* / *"Do I have any chemical manipulators?"* | The exact count for that material, its grade, and its cap |
| *"List my raw materials."* / *"What manufactured materials do I have?"* | The materials you're actually holding in that bucket (zero-count ones are skipped) |
| *"What raw materials am I near-capped on?"* | Same listing, filtered to the ones at or close to their grade cap |
| *"What am I capped on?"* / *"Am I full on anything?"* | Everything at or close to capped, across all three buckets |

Say the material however you'd say it in-game — a full name ("chemical manipulators"), a
shortened one ("wake solutions" for "Strange Wake Solutions"), or the plain element name
("arsenic"). COVAS++ fuzzy-matches it to the real material; if nothing matches it says so rather
than guessing.

## Grade caps

Elite Dangerous caps how many of a material you can hold by its engineering **grade**:

| Grade | Cap |
|-------|-----|
| 1 | 300 |
| 2 | 250 |
| 3 | 200 |
| 4 | 150 |
| 5 | 100 |

"Capped" means at the cap; "close to capped" means at or above 90% of it. Both are computed from
the grade cap table against your live count — never guessed per material.

## Where the data comes from

Same source as [blueprint & material sourcing](blueprints.md): the journal's `Materials` event
(your full inventory at every game load), kept current between events by `MaterialCollected` /
`MaterialDiscarded` deltas. Material names, buckets, and grades come from the same bundled
engineering tables the blueprint tool uses — nothing here is a second, separately-maintained list.

## Settings

This reads **only local journal data** — no game-account login or private API. It needs
[game-state monitoring](monitoring.md) (`[elite].enabled = true`) so your material inventory is
being watched. There's nothing else to configure.
