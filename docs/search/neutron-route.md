# Neutron / long-range route planner

> *"I plot a long-range neutron-highway route to a distant system — total jumps and the first
> waypoint — and copy that waypoint to your clipboard for the galaxy map."*

Ask COVAS++ to **plot a neutron route** (a.k.a. long-range or galaxy route) and it queries
[Spansh](https://spansh.co.uk/plotter) for a neutron-highway route to a distant system, tells you
how many jumps it takes and where the first hop is, and hands that first waypoint to the galaxy map.
This is Elite's long-range travel workhorse: riding neutron stars gets you from A to B in **far
fewer jumps** than a straight route.

**Example:** *"Plot a neutron route to Colonia — my laden jump range is 55 light-years."*

!!! note "Off by default"
    Set `[neutron_plan].enabled = true` to turn it on. It needs
    [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) to default the start
    to your **current system** — otherwise just tell COVAS a `from_system`.

## How it works

1. **Where to.** Give COVAS the **destination system** — it'll ask if you don't say.
2. **Tell it your jump range.** COVAS needs your **laden jump range** in light-years to bound each
   jump; it'll ask for it if missing.
3. **Start is where you are.** The route begins at your **current system** by default (or tell COVAS
   a `from_system` to start elsewhere).
4. **Efficiency (optional).** Higher efficiency (1–100, default 60) trades longer neutron detours
   for fewer total jumps — ask for a *"more efficient"* or *"more direct"* route to nudge it.
5. **It plots and speaks the summary:** the **total jumps**, the **number of waypoints**, and the
   **first waypoint**.
6. **It plots the first stop.** The first waypoint system is **copied to your clipboard** — paste it
   into the galaxy-map search to set course. (In-game "set course" arrives with the
   [keybind galaxy-map action](../automation/keybinds.md).)

## Settings

| Setting | What it does |
|---------|--------------|
| `neutron_plan.enabled` | Master switch (off by default) |
| `neutron_plan.default_efficiency` | Spansh efficiency 1–100 when you don't say (default 60) |

Requires [game-state monitoring](../elite/monitoring.md) for the current-system default start. This
is one of the [Spansh route planners](https://spansh.co.uk/) built on the same
[route foundation](trade-routes.md) as the trade planner. See the
[Configuration reference](../configuration.md#neutron-long-range-route-planner-neutron_plan).
