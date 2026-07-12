# Outfitting (modules)

> *"I find the closest station selling an outfitting module and copy that system to your
> clipboard."*

Find the nearest station that sells a given **outfitting module**, resolved conversationally, with
the result system copied to your clipboard.

**Example:** *"find the closest multi-cannon"*

## How it flows

1. Ask for a module in plain speech — *"find the nearest fuel scoop,"* *"find the closest
   multi-cannon."*
2. COVAS++ interprets the (possibly misheard) name against its **complete offline module table** —
   so *"multiple cannon"* becomes **Multi-Cannon** — and if the module comes in several sizes or
   mounts, it asks which you want. It **never guesses** a missing size or mount.
3. Once the module is fully pinned down, it runs a single search and tells you the nearest station,
   its system, and the distance — and copies the system.

The whole ask/clarify step is **offline and instant** — only the final station lookup touches the
network, and only once the module is resolved.

## Refinements you can add

| Refinement | Say something like… | Notes |
|------------|---------------------|-------|
| **Size** | *"a large multi-cannon"* | For modules that come in several sizes: small, medium, large, huge, or a class number |
| **Mount** | *"a gimballed multi-cannon"* | For weapons: fixed, gimballed, or turreted |
| **Landing pad** | *"somewhere with a large pad"* | Restrict to a pad size — small, medium, or large |

If you name just *"multi-cannon,"* COVAS++ will ask for the size and mount rather than picking for
you. Name a module that only comes one way (a fuel scoop of a given class) and it just searches.

## Mishears and unknowns

- A misheard name is resolved against the real module list — *"multiple cannon"* → **Multi-Cannon**.
- A name it genuinely can't place routes to [failure recovery](../using/help.md#3-failure-recovery-the-important-one):
  *"I didn't recognize 'power distributer' — did you mean Power Distributor?"* — always suggesting a
  real module, never an invented one.

## Settings

| Setting | What it does |
|---------|--------------|
| `nav.enabled` | Master switch for the outfitting (and ship) search |
| `nav.default_pad_size` | Default landing-pad size your ship needs (override per search) |
| `nav.search_size` | How many nearby stations to fetch before picking the closest match |
| `nav.require_confirmation` | Off by default: search immediately once resolved. On adds a separate "confirm" turn first |

See the [Configuration reference](../configuration.md#navigation-search-nav-star_systems-search).
