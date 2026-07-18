# Shipyards (ships)

> *"I find the closest station selling a given ship and copy that system to your clipboard."*

Find the nearest station that sells a given **ship**, with the result system copied to your
clipboard. The direct sibling of [outfitting search](outfitting.md).

**Example:** *"find the closest Anaconda"*

## How it flows

1. Ask for a ship — *"where can I buy a Python?"*, *"find the closest Anaconda."*
2. COVAS++ resolves the name against its ship roster, handling nicknames and mishears ("conda" →
   Anaconda, "fdl" → Fer-de-Lance, "clipper" → Imperial Clipper).
3. It searches, then tells you the nearest station, system, distance, and price — and copies the
   system.

## Ship families — it asks which

Some names cover several ships. COVAS++ **asks which one** rather than guessing:

- *"Krait"* → MkII or Phantom?
- *"Cobra"* → MkIII, MkIV, or MkV?
- *"Viper"* → MkIII or MkIV?
- *"Asp"* → Explorer or Scout?
- *"Type"* → Type-6, 7, 9, 10…?

Give a specific model up front ("Krait Phantom," "Type-9") and it skips the question.

## Stock verification — answers that match Inara

Ships are stocked at far fewer stations than modules, and a station's *catalog* isn't the same as
what it *currently stocks*. So before naming a station, COVAS++ **confirms the ship is actually in
stock** against EDSM's live shipyard data (the same data Inara shows). The upshot: its answer
should match Inara's own nearest-seller search.

- If the nearest listing turns out not to actually stock the ship, it **skips to the next one** and
  can tell you why.
- In sparse space where stock can't be confirmed, it still answers but adds a caveat
  ("I couldn't verify live stock…") so you know to double-check.

You can turn this off with `[nav].verify_stock = false` if EDSM is misbehaving (answers then come
without the stock guarantee).

## Refinements

| Refinement | Say something like… |
|------------|---------------------|
| **Landing pad** | *"somewhere with a large pad"* — small, medium, or large |
| **Match my ship** | *"find the closest Anaconda for my current ship"* — one-off pad size = whatever ship you're CURRENTLY flying |

## Settings

Ship search shares the `[nav]` section with [outfitting](outfitting.md):

| Setting | What it does |
|---------|--------------|
| `nav.enabled` | Master switch (shared with outfitting search) |
| `nav.default_pad_size` | Default landing-pad size (`S`/`M`/`L`/`any`/**Match Current Ship Size**), override per search |
| `nav.verify_stock` | Verify each candidate's current stock against EDSM before answering |
| `nav.search_size` | How many nearby stations to fetch before picking the closest |

**Match Current Ship Size** reads your CURRENTLY-FLOWN ship live from Elite Dangerous and filters
for a pad it can actually use — falling back to **Large** (never "any") if the ship isn't known yet,
so a search never sends you to a station you can't dock at.

See the [Configuration reference](../configuration.md#navigation-search-nav-star_systems-search).
