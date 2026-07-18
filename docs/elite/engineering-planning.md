# Per-ship engineering planning

> *"I remember how each of your ships is engineered and help you plan what to upgrade next — the
> materials you're short on, which engineer to see — and can drop the plan onto your checklist."*

COVAS++ remembers **how each of your ships is built** — its modules and applied engineering — and
uses that memory to help you plan what to engineer next, grounded on the ship's real loadout, your
live material inventory, and your engineer unlock progress. Then it can turn a plan into
[checklist](../using/checklist.md) to-dos through the same checklist tools it
already uses.

This is the payoff of the engineering feature set: it ties together your
[owned ships](owned-ships.md), your [ship loadout](loadout.md), the
[blueprint & material sourcing](blueprints.md) tables, your [materials inventory](materials.md), and
the [engineers finder](engineers.md) into one conversational planner.

**Example:** *"what should I engineer next on my Python"*

## Per-ship config memory

Elite only ever describes the ship you're **currently** flying (the `Loadout` event), replacing it
wholesale each time you board a different ship. COVAS++ captures each of those into a small,
persistent per-ship store **keyed by the journal ShipID** — the same identity your
[owned-ships](owned-ships.md) fleet uses. So:

- **Switching ships doesn't lose a build.** Board your explorer, then your combat ship — COVAS++
  still remembers the explorer's engineering.
- **It survives restarts.** Your ships' builds are there next session, before you've re-boarded
  anything.
- **It's per ship.** Ask about a ship you're *not* flying and it recalls that ship's own loadout.

Nothing is fetched from Coriolis, EDSY, or your game account — it's **only your local journal**. A
ship COVAS++ hasn't seen a `Loadout` for yet simply has no remembered build, and it will say so
rather than guess.

## What you can ask

| You say… | It does… |
|----------|----------|
| *"What's the engineering on my Python?"* | Recalls that ship's remembered build — engineered modules (blueprint + grade) and which are still stock |
| *"What should I engineer next on my Anaconda?"* | Same summary, so you can see what's left to do |
| *"What's left to grade 5 my FSD?"* | Reports the FSD's current grade, the material shortfall for grade 5, and which engineer applies it |
| *"Plan grade 5 dirty drives on my Python"* | Plans that upgrade, grounded on the remembered build + live materials |
| *"Add engineering my FSD to grade 5 to my checklist"* | Writes the plan onto your checklist as trackable to-dos |

For a **generic** recipe comparison that isn't tied to one of your ships (*"compare grade 3 vs
grade 5 dirty drives"*), COVAS++ uses the [blueprint tool](blueprints.md) instead.

## How a plan is grounded

When you ask to plan an upgrade, COVAS++ reasons over real data only:

1. **The remembered build** gives the module's *current* blueprint and grade (nothing invented — if
   the module is still stock it asks which blueprint you mean rather than guessing one).
2. **The bundled blueprint recipe** for the target grade is crossed with **your live material
   inventory** to compute exactly what you're short on.
3. **Your live `EngineerProgress`** names the engineer who applies that blueprint and whether you've
   unlocked them (and at what grade).

## The checklist bridge

Ask COVAS++ to *"add this to my checklist"* and it records the plan through the **same checklist
tools** you use everywhere else — one objective per step, naming the ship, module, target grade,
engineer, and any material shortfall. Completing or removing them uses those same tools, so an
engineering plan is just ordinary checklist items you can track, tick off, and edit by voice.

## Where it's stored

The per-ship builds live in a small git-ignored `ship_loadouts.json` in your data directory —
journal-derived personal data, never committed.

## Settings

This needs [game-state monitoring](monitoring.md) (`[elite].enabled = true`) so the journal is being
watched. The store filename is `[ships].loadouts_file` (default `ship_loadouts.json`); you won't
normally touch it.

```toml
[ships]
loadouts_file = "ship_loadouts.json"
```
