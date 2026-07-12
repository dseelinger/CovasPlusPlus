# Ship loadout & engineering

> *"I read your ship's fitted modules and engineering from the journal — blueprints, grades,
> experimental effects — and can suggest upgrades."*

COVAS++ reads your current ship's full loadout from the game's journal — every fitted module and
its engineering — and answers questions about it out loud. It can also reason over your build and
offer improvements.

**Example:** *"what's the engineering on my FSD"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"What's the engineering on my FSD?"* | The blueprint, grade, experimental effect, and key modified stats on that module |
| *"What experimental effect is on my power distributor?"* | The experimental effect on a named module |
| *"List my experimental effects."* | Every experimental effect fitted across the ship |
| *"What's on my ship?"* / *"What's in my optional internals?"* | The fitted modules — hardpoints, utilities, core, and optional internals |

Name the module in plain speech — **FSD, power plant, thrusters, shield generator, a weapon** — and
COVAS++ reads what's fitted and how it's engineered, translating the game's internal part names
into spoken ones.

## Reasoning about your build

Because the checklist tools are always available, you can go a step further:

> *"Suggest some upgrades and add them to my checklist."*

COVAS++ can reason over your loadout, suggest improvements conversationally, and — with your
go-ahead — add specific upgrades to your [checklist](../using/checklist.md). For "what's the best
X" engineering questions it will lean on web search for the current meta and flag any uncertainty
rather than inventing module stats or blueprint effects.

## No loadout seen yet?

The loadout comes from the journal's `Loadout` event, which the game writes when you board your
ship or open outfitting. If COVAS++ hasn't seen one this session, it'll say so:
*"Board your ship or open outfitting and I'll read it."*

## Settings

This reads **only local journal data** — no game-account login or private API. It needs
[game-state monitoring](monitoring.md) (`[elite].enabled = true`) so the journal is being watched.
