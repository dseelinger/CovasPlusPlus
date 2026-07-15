# Road-to-Riches planner

> *"I plan a Road to Riches from where you are — nearby systems full of high-value bodies to
> First-Discovery-scan for credits — and copy the first system to your clipboard for the galaxy
> map."*

Ask COVAS++ to plan a **Road to Riches** and it queries [Spansh](https://spansh.co.uk/riches) for a
chain of nearby systems packed with **high-value, unscanned bodies** to First-Discovery-scan for
exploration credits, starting from your **current system**, tells you where to begin and how much
it's worth, and hands the first system to the galaxy map.

**Example:** *"Plan me a Road to Riches route — I've got a 40 light-year jump range."*

!!! note "Off by default"
    Set `[riches_plan].enabled = true` to turn it on. It needs
    [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) to know your current
    system (or just tell COVAS a `from_system`).

## How it works

1. **You're the start.** The route begins at your current system (or tell COVAS a `from_system` to
   start elsewhere).
2. **Tell it your jump range.** COVAS needs your **laden jump range** — it'll ask if you don't say.
   You can also set the search **radius**, the number of **systems**, and a **minimum scan value**.
3. **It plans and speaks the first stop:** the first system, how many bodies to scan there, and the
   estimated value — plus a rough total across the whole route.
4. **It plots the first system.** That system is **copied to your clipboard** — paste it into the
   galaxy-map search to set course. (In-game "set course" arrives with the
   [keybind galaxy-map action](../automation/keybinds.md).)

## Settings

| Setting | What it does |
|---------|--------------|
| `riches_plan.enabled` | Master switch (off by default) |
| `riches_plan.default_radius` | Search radius in ly when you don't say (default 50) |
| `riches_plan.default_max_results` | Systems in the route when you don't say (default 25) |
| `riches_plan.default_min_value` | Minimum per-body scan value to include (default 300,000 cr) |
| `riches_plan.use_mapping_value` | Fold FSS-mapping value into each body's worth (default on) |

Requires [game-state monitoring](../elite/monitoring.md) for the current-system start. This is one of
the [Spansh route planners](https://spansh.co.uk/) — it shares the async route client and the
galaxy-map plot handoff with the [trade-route planner](trade-routes.md). See the
[Configuration reference](../configuration.md#road-to-riches-planner-riches_plan).
