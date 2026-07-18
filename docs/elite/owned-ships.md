# Owned ships

> *"I keep track of the ships you own — updated as you buy, sell, and switch ships — and you can
> correct the list by voice."*

COVAS++ keeps a persistent registry of the ships you **own** — your fleet identity — built straight
from the game's journal and kept up to date as you buy, sell, and switch ships. Ask *"what ships do
I own?"* and it reads back your fleet, flags the one you're flying, and gives each ship's last-known
location. It survives restarts, so it remembers your fleet even before you've docked this session.

This is different from [stored ships](stored-ships-modules.md): *stored* ships are the ones parked
in **storage** right now (never the one you're flying). *Owned* ships are your **whole fleet** —
the active ship plus everything in storage — as one durable list.

**Example:** *"what ships do I own"*

## What you can ask

| You say… | It does… |
|----------|----------|
| *"What ships do I own?"* | Reads back your fleet, marks the ship you're flying, and gives each one's last-known system |
| *"Which ship am I flying?"* | Names the active ship |
| *"I bought a Python"* | Adds a Python to your fleet |
| *"I own an Anaconda called Void Runner"* | Adds it with its custom name |
| *"Remove the Cobra"* | Takes that ship off your fleet |
| *"I sold my Anaconda"* | Removes it |

## How it stays up to date

COVAS++ folds the game's own **ownership events** into the registry as they happen:

- **Buy a new ship** → it's added to your fleet and becomes the active ship.
- **Sell a ship** → it's removed.
- **Switch ships** (in the shipyard) → the ship you switched into is marked active.
- **Board / dock** → the active ship and your stored ships' locations are reconciled from the
  `Loadout` and `StoredShips` the game writes, so names and last-known systems stay fresh.

Each ship is keyed by its journal **ShipID** — the stable per-hull id — so a ship is tracked as one
identity across renames and moves.

## Correcting the list by voice

Elite doesn't always give COVAS++ an event for a ship you already owned before you started using the
app, and you may want to fix a detail. So you can **add or remove** ships by voice:

- *"I bought a Python"*, *"add my Cobra to the fleet"* — records a ship manually.
- *"remove the Cobra"*, *"I sold my Anaconda"* — takes one off. If more than one ship matches
  (say, two Cobras), COVAS++ asks which rather than guessing.

A ship you added or edited by hand is a **correction**: it's kept, and the next journal event won't
overwrite your custom name or silently delete it. So your fixes stick.

## Where it's stored

The fleet lives in a small git-ignored `owned_ships.json` in your data directory — journal-derived
personal data, never committed. Nothing is fetched from Inara, Coriolis, or your game account; it's
**only your local journal** plus anything you've told it by voice.

## Settings

This needs [game-state monitoring](monitoring.md) (`[elite].enabled = true`) so the journal is being
watched. The registry filename is `[ships].registry_file` (default `owned_ships.json`); you won't
normally touch it.

```toml
[ships]
registry_file = "owned_ships.json"
```
