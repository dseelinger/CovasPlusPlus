# Game-state monitoring

> *"I answer from live game telemetry — where you are, your fuel and ship, and what you've been
> doing."*

This is the foundation of everything Elite-aware in COVAS++. When it's on, two background watchers
tail the files Elite Dangerous writes to disk — the **journal** and **`Status.json`** — and keep a
rolling picture of your current situation. **No memory reading, no API keys** — just the same log
files other community tools read.

**Example:** *"where am I"*

!!! important "Enable this first"
    Set `[elite].enabled = true`. Nearly every other Elite feature depends on it —
    [proactive callouts](proactive-callouts.md), [route callouts](route-callouts.md), the
    [keybind](../automation/keybinds.md) and [auto-honk](../automation/auto-honk.md) combat guard,
    [carriers](location-carriers.md), [community goals](community-goals.md), and the live "current
    system" that every [voice search](../search/index.md) starts from.

## What you can ask

| You say… | It answers from… |
|----------|------------------|
| *"Where am I?"* | Your current star system, station, nearest body |
| *"How's my fuel?"* | Fuel level and percentage |
| *"Am I docked?"* / *"What ship am I in?"* | Current flight/ship status |
| *"What did I just do?"* / *"Check my logs."* | Your recent notable journal events |

These are answered from **real telemetry**, not a guess — and the trivial ones don't even need a
full round-trip, so they're quick and cheap.

### The "context" wake word

On an ambiguous question you can force a live lookup by working the word **"context"** into it. The
word is scrubbed from what the model sees — it just guarantees COVAS++ checks your real status for
that turn.

## What it tracks

A rolling snapshot the companion can reference at any time:

- Current **system**, **station**, and **ship**
- **Fuel** and **cargo**
- Flight flags (docked, landing gear, hardpoints, supercruise, low fuel, overheating…)
- Danger / interdiction state (used by the safety guards)
- A **recent-events feed** — jumps, docks, missions, deaths, and fuel/heat alerts, with the
  journal spam filtered out — that answers "what just happened."

The watchers only ever *publish* what they see; they never initiate speech on their own. Turning
your live state into spoken callouts is a separate, opt-in feature — see
[Proactive callouts](proactive-callouts.md).

## Settings

| Setting | What it does |
|---------|--------------|
| `elite.enabled` | Master switch for game-state monitoring |
| `elite.journal_dir` | Where your journal lives (blank = the standard Saved Games location) |
| `elite.journal_poll_interval` | How often to re-scan the journal for new lines |
| `elite.status_poll_interval` | How often to poll `Status.json` for flag changes |
| `elite.recent_events_kept` | How many recent events feed "what just happened" |

The journal location defaults to the standard
`%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous` — set a path only if yours is
non-standard. See the [Configuration reference](../configuration.md#elite-dangerous-elite).
