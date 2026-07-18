# Trade-route planner

!!! warning "Experimental — off by default"
    The trade-route planner is an **experimental** feature: it ships **disabled** for everyone and
    is gated at capability registration, so a flag-off build exposes no trade-route tool or help.
    Enable it just for yourself by adding `experimental.trade_route.enabled = true` to your
    git-ignored `overrides.json` (see
    [Experimental feature flags](../configuration.md#experimental-feature-flags)) **and** setting
    `route_plan.enabled = true`.

> *"I plan a profitable multi-hop trade loop from where you're docked — every hop's buy, sell, and
> profit plus the round-trip total, with a heads-up when prices are stale — and copy the next stop
> to your clipboard for the galaxy map."*

Ask COVAS++ to plan a **trade route** and it queries [Spansh](https://spansh.co.uk/trade) for a
profitable buy/sell loop starting from the station you're **docked at**, reads you the **whole
loop** (each hop, plus the round-trip total), and hands the next stop to the galaxy map.

**Example:** *"Plan me a trade route from here — I've got 720 tons of cargo and a 30 light-year jump
range."*

!!! note "Off by default"
    Set `[route_plan].enabled = true` to turn it on. It needs [game-state monitoring](../elite/monitoring.md)
    (`[elite].enabled = true`) to know the station you're docked at.

## How it works

1. **You're the start.** The route begins at the station you're currently docked at (or tell COVAS
   a `from_system` / `from_station` to start elsewhere).
2. **Tell it your ship.** COVAS needs your **cargo capacity**, **laden jump range**, and **budget**
   — it'll ask for whatever you don't say.
3. **It plans and reads the whole loop:** for each hop — what to buy, where, where to sell it, and
   the profit per ton — followed by the **round-trip total**.
4. **It plots the next stop.** The destination system is **copied to your clipboard** — paste it
   into the galaxy-map search to set course. (In-game "set course" arrives with the
   [keybind galaxy-map action](../automation/keybinds.md).)

### Refining the run

Mention any of these and COVAS folds them into the request — otherwise it uses sensible defaults:

| Say something like… | Effect |
|---------------------|--------|
| *"…up to 5 hops"* | Loop length (`max_hops`; default 4). |
| *"…large pad only"* | Only stations with a large landing pad (`requires_large_pad`). |
| *"…nothing more than 1500 light-seconds out"* | Cap the supercruise distance from the star to each station (`max_arrival_distance`). |
| *"…include planetary ports"* | Allow surface markets (`allow_planetary`, off by default). |
| *"…don't send me back to the same station"* | Never revisit a station in the loop (`avoid_loops`, on by default). |
| *"…only prices from the last day"* | Tighten the freshness window for this run (`max_price_age_days`). |

## Fresh prices, honestly

Market prices change constantly, so COVAS is honest about **freshness** — the headline of this
planner:

- **Per hop:** any leg whose source price is older than the window is read with an inline tag, e.g.
  *"…for about 7,200 a ton (price ~4 days old)."*
- **Whole loop:** if even the *freshest* hop is past the window — the entire route rests on stale
  data — it adds a spoken caveat like *"heads up, the freshest prices on this route are about 4 days
  old, so they may have moved."*

It never passes off an old price as current. The freshness window is
`[route_plan].max_price_age_days` (default 2 days), overridable per run by asking for newer prices.

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
