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

## It knows where it is (place-aware callouts)

When you arrive somewhere **notable**, COVAS++ recognises it and grounds the callout in real facts
instead of a generic "docked" line:

- **Engineer bases** — dock at Farseer Inc and it knows that's Felicity Farseer's workshop, and what
  she engineers.
- **Your own fleet carrier** — it knows when you're home.
- **Landmarks** — a small built-in list (Hutton Orbital, Sol, Shinrarta Dezhra, Colonia,
  Sagittarius A*).
- **First visit to a system** — it notices the first time you set foot somewhere new.

It also remembers **how often you come here**. A private, on-disk **visit ledger** counts your
arrivals per system and per station, so a callout can say *"Farseer's again — tenth time today,
Commander"* or *"first time here."* The place names and the counts are **supplied facts** — COVAS++
voices them, it never makes them up.

!!! info "Grounded, occasional, and private"
    The ledger is **git-ignored per-user data** (your own travel history) and never leaves your
    machine. It's bounded — ancient entries roll off — so the file stays small. History remarks are
    **occasional**: they only fire on something worth mentioning (a special place, your first visit,
    a round-number milestone, or an unusually busy day) and ride a **dedicated cooldown**
    (`proactive.place_cooldown`), so a busy engineering session never narrates every dock.

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
| `proactive.place_cooldown` | How long before another place-aware / visit-history remark may ride a callout |
| `proactive.visit_ledger_file` | Where the private per-location arrival log lives (blank disables it) |
| `[proactive.events]` | Per-event whitelist — only events set `true` are ever announced |

See the [Configuration reference](../configuration.md#proactive-callouts-proactive) for the full
section. For heads-up callouts specific to flying a plotted route, see
[Route callouts](route-callouts.md).
