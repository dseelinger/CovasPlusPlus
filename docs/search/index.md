# Voice search

COVAS++ can search the galaxy for you, by voice, in plain conversational speech. You say what you
want; it asks only for what it's missing; and it copies the result system to your clipboard so you
can paste it into the galaxy map. Under the hood it queries the community **Spansh** database.

All searches share the same feel:

- **Conversational.** Say it however it comes out. If something's missing or ambiguous, COVAS++
  asks — it doesn't guess. Refine it turn by turn ("actually, make it a low-security anarchy") and
  it re-runs the search.
- **Nearest first, from where you are.** Results are sorted by distance from your **current
  system** (from [game-state monitoring](../elite/monitoring.md); it falls back to your latest
  journal position if the game isn't running).
- **Validated before it's spoken.** Every filter value and result name is checked against a
  canonical vocabulary first, so a misheard filter is *corrected*, not silently widened — and it
  never speaks a system or module it can't verify.
- **Copied for you.** The result system goes to your clipboard automatically — unless it's your
  current system, in which case it says you're already there.

## The categories

| Category | Find the nearest… | Example |
|----------|-------------------|---------|
| [Outfitting (modules)](outfitting.md) | station selling an outfitting **module** | *"find the closest multi-cannon"* |
| [Shipyards (ships)](shipyards.md) | station selling a **ship** | *"find the closest Anaconda"* |
| [Star systems](star-systems.md) | **system** matching traits you describe | *"find the nearest Empire system with high security"* |
| [Stations](stations.md) | **station** by type, services, pad, distance, or faction | *"find the nearest station with a shipyard and a large pad"* |
| [Minor factions](minor-factions.md) | **system** where a faction is present or in control | *"find the nearest system where the Dark Wheel is present"* |
| [Faction states](faction-states.md) | **system** by its controlling faction's state | *"find the nearest system at war"* |
| [Signals & structures](signals.md) | **structure** — megaship, settlement, outpost, starport | *"find the nearest megaship"* |
| [Body finder](bodies.md) | **body** by type or biological signal | *"find the nearest Earth-like world"* |

!!! tip "Not sure which to use? Just ask"
    You don't need to know the categories. Ask for what you want ("where can I buy a Python?",
    "nearest station with a material trader") and COVAS++ routes it to the right search. If you name
    a **module or a ship**, it uses outfitting or shipyard search; a station by service or type goes
    to station search.

## What each needs

The search categories are enabled by default but need internet (to reach Spansh) and benefit from
[game-state monitoring](../elite/monitoring.md) for your live current system. The relevant config
sections are `[nav]` (modules + ships), `[star_systems]`, and `[search]` (stations, factions,
signals, states) — see the [Configuration reference](../configuration.md).
