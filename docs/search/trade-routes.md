# Trade-route planner

> *"I plan a profitable trade loop from where you're docked — what to buy, where to sell, and the
> profit — and copy the next stop to your clipboard for the galaxy map."*

Ask COVAS++ to plan a **trade route** and it queries [Spansh](https://spansh.co.uk/trade) for a
profitable buy/sell loop starting from the station you're **docked at**, tells you the best first
hop, and hands the next stop to the galaxy map.

**Example:** *"Plan me a trade route from here — I've got 720 tons of cargo and a 30 light-year jump
range."*

!!! note "Off by default"
    Set `[route_plan].enabled = true` to turn it on. It needs [game-state monitoring](../elite/monitoring.md)
    (`[elite].enabled = true`) to know the station you're docked at.

## How it works

1. **You're the start.** The route begins at the station you're currently docked at (or tell COVAS
   a `from_system` / `from_station` to start elsewhere).
2. **Tell it your ship.** COVAS needs your **cargo capacity**, **laden jump range**, and **budget**
   — it'll ask for whatever you don't say. You can also set the max number of hops and whether you
   need a large pad.
3. **It plans and speaks the best hop:** what to buy, where to sell it, and the profit per ton.
4. **It plots the next stop.** The destination system is **copied to your clipboard** — paste it
   into the galaxy-map search to set course. (In-game "set course" arrives with the
   [keybind galaxy-map action](../automation/keybinds.md).)

## Fresh prices, honestly

Market prices change constantly. If the best route COVAS can find is built on **stale prices**, it
still gives you the answer — but with a spoken caveat like *"heads up, the freshest prices on this
route are about 4 days old, so they may have moved."* It never passes off an old price as current.
The freshness window is `[route_plan].max_price_age_days` (default 2 days).

## Settings

| Setting | What it does |
|---------|--------------|
| `route_plan.enabled` | Master switch (off by default) |
| `route_plan.default_max_hops` | Hops in the loop when you don't say (default 4) |
| `route_plan.max_price_age_days` | Prices older than this get a spoken "may have moved" caveat |

Requires [game-state monitoring](../elite/monitoring.md) for the docked-station start. This is the
first of the [Spansh route planners](https://spansh.co.uk/) — Road to Riches, neutron plotting, and
mining routes build on the same foundation. See the
[Configuration reference](../configuration.md#trade-route-planner-route_plan).
