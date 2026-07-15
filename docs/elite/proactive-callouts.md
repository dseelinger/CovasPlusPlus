# Proactive callouts

Proactive callouts let COVAS++ **speak up on its own** — without a push-to-talk press — when
something notable happens in the game. Arrive in a new system, dock, finish a mission, drop low on
fuel, overheat, or die, and it says a short in-character line.

!!! info "Opt-in and polite"
    Requires [game-state monitoring](monitoring.md) (`[elite].enabled = true`). It's designed
    never to be annoying: a callout fires **only when you're idle** (never over your own turn),
    it's rate-limited, and you can mute it instantly by voice.

## What triggers a callout

Out of the box, these events can produce a line (each individually toggleable):

| Event | Example moment |
|-------|----------------|
| `FSDJump` | Arriving in a new system |
| `Docked` | Docking at a station |
| `MissionCompleted` | Completing a mission |
| `LowFuel` | Fuel dropping below ~25% |
| `Overheating` | Ship over 100% heat |
| `Died` | The bad one |
| `ScanOrganic` | Logging an exobiology sample — *"one more to analyse"* |
| `OxygenLow` | On-foot suit oxygen below ~25% |
| `HealthLow` | On-foot health critical |
| `SrvHullLow` | SRV hull dropping below ~30% |

The last four cover the Odyssey **on-foot** and **SRV** modes (see
[On-foot & SRV awareness](monitoring.md#on-foot-srv-awareness)) — the same never-chatty
discipline applies: they're whitelisted by default but only speak when `[proactive].enabled`,
and both cooldowns still gate them.

Lines are short, in-character, and generated on the **cheap tier**, so callouts stay inexpensive.

## It never talks over you

- A callout is spoken **only when the loop is idle** — if you're mid-conversation, it waits.
- **Rate limits** keep a burst of transitions (jump → supercruise exit → dock) down to a single
  line, and the same event type won't re-announce too soon.
- A callout in progress is **cancelable** — tap the talk key mid-sentence and it stops, like any
  other speech.

## Muting on the fly

Say **"stop the callouts"** (or "be quiet," "no more announcements") and it goes silent. Say
**"turn callouts back on"** to re-enable them. Two voice commands handle this:

- `mute_proactive` — silences proactive callouts
- `unmute_proactive` — turns them back on

## Settings

| Setting | What it does |
|---------|--------------|
| `proactive.enabled` | Master switch for proactive callouts |
| `proactive.min_interval` | Minimum seconds between any two callouts |
| `proactive.cooldown` | How long before the same event type may re-announce |
| `proactive.max_tokens` | Reply length cap for a callout (it's one sentence — keep it tight) |
| `[proactive.events]` | Per-event whitelist — only events set `true` are ever announced |

See the [Configuration reference](../configuration.md#proactive-callouts-proactive) for the full
section. For heads-up callouts specific to flying a plotted route, see
[Route callouts](route-callouts.md).
