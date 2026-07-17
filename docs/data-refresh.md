# Keeping game data current

COVAS++'s promise is a **grounded, offline reference**: when you ask what a ship carries, what a
module costs, or what an engineering roll needs, the answer comes from bundled community data —
never from the language model's training-cutoff memory. This page explains how that bundled data
is kept current with Frontier's releases, and how to check how fresh it is.

It's the same *"ground it, don't guess"* principle behind grounded provider model ids (issue
#91) and fetched [settings catalogs](configuration.md) (issue #92), applied to game content:
**if the community maintains it, a developer shouldn't have to re-type it.**

## How fresh is my data?

Just ask — *"how up to date is your ship data?"* COVAS++ reads its bundled **data manifest** and
tells you each dataset's source and when it was last generated. This is the honest companion to
*"I don't have that hull yet"*: if you ask about content newer than the data, it says so and
offers a web search rather than inventing numbers.

`check_setup.py` reports the same freshness at startup and warns when any dataset is older than
about six months.

## The datasets

| Dataset | Source | Powers |
|---------|--------|--------|
| Ship roster (names) | Spansh shipyard harvest | [find a shipyard](search/shipyards.md), name resolution |
| Ship specifications | [EDCD/coriolis-data](https://github.com/EDCD/coriolis-data) | [ship specs](elite/ship-specs.md) |
| Outfitting modules | [EDCD/FDevIDs](https://github.com/EDCD/FDevIDs) | [find a module](search/outfitting.md) |
| Engineering blueprints / materials | EDCD coriolis-data + FDevIDs | [blueprints](elite/blueprints.md) |

The ship **engineers** tables ([ship](elite/engineers.md) and
[on-foot](elite/on-foot-engineering.md)) are hand-curated snapshots — their sources are
wiki-shaped, not machine-readable — so they're refreshed by hand when they drift. Currencies and
balances follow a separate honest-degradation contract, documented on the *currency behavior*
page.

## How a refresh works

Each dataset is regenerated in **two stages**, so the app stays 100% offline at runtime while the
bundled data converges on live community data every release:

1. **Fetch** — download the latest community source into a *committed snapshot* (a fixture in the
   repo). This is the only step that touches the network, and it happens at dev time.
2. **Generate** — a pure function of the committed snapshot writes the bundled table. Because it
   depends only on committed inputs, regeneration is deterministic and testable offline.

**Failure contract:** the Spansh ship-name harvest is **fail-soft** — if Spansh is down, the
committed snapshot is kept and the run notes it, so a release never blocks on an outage. The
coriolis-data and FDevIDs fetches are **fail-loud** — a bad fetch aborts rather than silently
shipping stale data. When Frontier ships a new hull, the coriolis file for it has no roster id
yet, and the ship-spec regen **fails loudly naming the ship** — that loud error *is* the
new-content detector.

## Running it

One command refreshes everything and prints a diff summary (new hulls, new modules, changed
blueprints, orphaned overlay rows) plus a nag for the hand-curated tables:

```powershell
.venv\Scripts\python.exe scripts\refresh_datasets.py
```

Then review the diff, run `pytest`, and commit — that's the whole per-patch workflow. Add
`--no-fetch` to regenerate from the committed snapshots without any network.

Individual generators exist too (`scripts\gen_ship_roster.py`, `scripts\gen_ship_specs.py`,
`scripts\gen_module_taxonomy.py`), each with a `--fetch` stage where applicable.

## Adding a new ship takes zero code edits

When Frontier releases a hull, running the refresh:

1. harvests its name + FDev symbol from Spansh and assigns it a canonical id automatically;
2. writes it into the generated ship roster, so COVAS++ can resolve and find it in a shipyard;
3. matches its coriolis spec file to that id and bakes its real specifications.

The only thing left for a human is *editorial* — a nickname or two, if the ship has common short
forms — and even without that, the ship resolves by its full name and reports real specs. The
roster's aliases, family disambiguation, and starter list live in a small curated overlay next to
the resolution logic; everything else is data.
