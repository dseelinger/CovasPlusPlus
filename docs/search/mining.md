# Mining helper

> *"I plan a mining run — the nearest ring hotspot for a material, the best FRESH place to sell it
> (stale prices flagged), and the go-mine-sell loop dropped onto your checklist — and copy the
> hotspot system for the galaxy map."*

Ask COVAS++ where to mine something and it does three things at once:

1. **Finds the nearest hotspot.** It queries [Spansh](https://spansh.co.uk/) for the closest ring
   **hotspot** of the material you name (Painite, Low Temperature Diamonds, Void Opal, Tritium…) from
   your **current system**, and tells you the ring, how many overlapping hotspots it holds, and how
   far out it is.
2. **Finds the best _fresh_ place to sell.** It looks up the highest **sell price** for the mined
   commodity — but **freshness-verified**: it drops transient fleet carriers and only trusts a
   recent quote, flagging a stale one instead of quoting it blind.
3. **Drops the loop onto your checklist.** The mining loop — *go to the hotspot → mine → sell here* —
   is added to your [objective checklist](../using/checklist.md) as trackable steps, and the hotspot
   system is handed to the galaxy map.

**Example:** *"Where's the nearest Painite hotspot?"* or *"Plan a Low Temperature Diamonds run and
somewhere with a large pad to sell it."*

!!! note "Off by default"
    Set `[mining_helper].enabled = true` to turn it on. It needs
    [game-state monitoring](../elite/monitoring.md) (`[elite].enabled = true`) to know your current
    system (or just tell COVAS a `from_system`).

## Why freshness is the point

Mining prices swing hard — a single filled sell order moves the board — and the **highest** headline
prices on Spansh are almost always **fleet carriers whose market data is years stale** (a carrier
that jumped away, its old price frozen in the database). Quoting one of those costs millions when you
arrive to find the price gone. So the mining helper:

- **Drops fleet carriers** (they're transient — they jump), the same rule the station searches use.
- **Verifies the timestamp.** It answers with the best **fresh** quote (within
  `max_price_age_days`); only if nothing fresh exists does it fall back to the best available and say
  so — *"that's the freshest quote I found and it's about N days old, so it may have moved."*

This is the differentiator over read-it-aloud mining tools: the number it gives you is one you can
actually act on.

## How it works

1. **You're the start.** The search begins at your current system (or tell COVAS a `from_system`).
2. **Name the material.** *"Painite"*, *"LTDs"*, *"Void Opals"*, *"Tritium"* — it maps the spoken
   name to the right hotspot/commodity. It'll ask if you don't say.
3. **Refine if you like:** *"…large pad only to sell it"* (`requires_large_pad`), *"…prices no older
   than a day"* (`max_price_age_days`), *"…don't touch my checklist"* (`add_to_checklist`), or *"…just
   the hotspot, no plotting"* (`plot`).
4. **It speaks the plan:** the nearest hotspot, the best fresh sell (with any age caveat), and that
   the loop was added to your checklist.
5. **It plots the hotspot system.** That system is **copied to your clipboard** — paste it into the
   galaxy-map search to set course. (In-game "set course" arrives with the
   [keybind galaxy-map action](../automation/keybinds.md).)

## Settings

| Setting | What it does |
|---------|--------------|
| `mining_helper.enabled` | Master switch (off by default) |
| `mining_helper.max_price_age_days` | A sell quote older than this (days) is spoken with an age caveat (default 2) |
| `mining_helper.add_to_checklist` | Drop the mining loop onto your checklist as trackable steps (default on) |

Requires [game-state monitoring](../elite/monitoring.md) for the current-system start. It rides the
same [Spansh](https://spansh.co.uk/) search layer as the [voice searches](index.md) and the same
[galaxy-map plot handoff](trade-routes.md) as the route planners. See the
[Configuration reference](../configuration.md#mining-helper-mining_helper).
