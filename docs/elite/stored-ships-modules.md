# Stored ships & modules

> *"I find your stored ships and modules from the journal — where each one is, and what it costs
> and how long to transfer it to you."*

COVAS++ reads your parked ships and shelved modules straight from the game's journal and answers
"where's my Cutter?", "where did I leave that fuel scoop?", and "how much to bring it here?" out
loud. It's **pure your-state data** — the journal already holds it, so there's no lookup, no
account login, and no way for it to be wrong about what you own.

**Example:** *"where's my Cutter"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"Where's my Cutter?"* | Whether that ship is here or names the system it's in, plus the transfer cost and time |
| *"Where did I leave my Corvette?"* | The stored ship's location and the quoted transfer to bring it to you |
| *"What ships do I have in storage?"* | A rundown of your stored fleet — which are here, which are elsewhere |
| *"Where's my spare shield generator?"* | A shelved module's location and its transfer cost and time |
| *"What modules do I have stored?"* | The full stored-modules list, grouped by here / elsewhere / in transit |

Name the ship or module in plain speech — a hull (**Cutter**, **Federal Corvette**), a custom
ship name, or a module (**shield generator**, **fuel scoop**, **FSD**) — and COVAS++ finds it in
your stored inventory. It only ever names something genuinely in storage; ask for one you don't
have and it says so and lists what you *do* have.

## Transfer cost & time

When a ship or module is stored somewhere other than where you're docked, COVAS++ quotes the
**transfer cost** (credits) and **transfer time**, and copies the destination system to your
[clipboard](../using/clipboard.md) so you can paste it into the galaxy map — unless you're already
in that system.

Those figures come **straight from the game**: Frontier writes the exact `TransferPrice` /
`TransferCost` and `TransferTime` into every remote entry of the `StoredShips` / `StoredModules`
journal events, computed from the distance between where you're docked and where the ship/module
sits (time grows with distance; cost with distance and the item's value). COVAS++ surfaces those
numbers verbatim rather than re-deriving them — so they're exactly what the in-game Shipyard /
Outfitting transfer screen would show.

## Freshness — "as of your last dock"

The game only writes these inventories (and their transfer quotes) when you **dock somewhere with
a shipyard / outfitting**. So the data is accurate *as of that last dock* — the station the quotes
are measured from. If you've jumped away since, COVAS++ still answers, but says the figures are
from your last dock rather than pretending they're live. Dock at a shipyard again and it refreshes.

## No stored data seen yet?

The inventory comes from the journal's `StoredShips` / `StoredModules` events. If COVAS++ hasn't
seen them this session, it'll say so: *"Dock at a station with a shipyard / outfitting and I'll
pick them up."*

## Settings

This reads **only local journal data** — no game-account login or private API. It needs
[game-state monitoring](monitoring.md) (`[elite].enabled = true`) so the journal is being watched.
