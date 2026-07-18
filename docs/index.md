# COVAS++

**A real ship's AI for [Elite Dangerous](https://www.elitedangerous.com/), for Windows.**
The COVAS the game ships with reads canned lines. COVAS++ holds up its end of the conversation:
it **knows** your game — grounded in the same journal Elite writes to disk, searching the whole
galaxy by voice — it **acts** on your word through a small set of heavily-guarded ship controls,
and it **inhabits** your ship, with swappable personas, a named and voiced crew, an ambient
cockpit soundscape, and a glanceable HUD. All through one push-to-talk loop that runs on your
own machine.

!!! info "Unofficial, fan-made"
    Elite Dangerous is a trademark of Frontier Developments plc. COVAS++ is not
    affiliated with, endorsed by, or supported by Frontier.

## What it is

Hold a key, speak, release — and the ship answers, out loud, in the voice and character you
chose. Because COVAS++ reads the journal and status files the game already writes to disk, it
knows where you are, how your ship is doing, and what you've been up to — so its answers are
**grounded in your actual game, not guessed**. Three things, one loop:

### It knows your game

- **Push-to-talk voice** — hold a key, speak, release. Speech is transcribed **on your own
  machine**; nothing leaves your PC just to hear you.
- **Situational awareness** — "Where am I? How's my fuel? What did I just do?" answered from
  live telemetry.
- **Galaxy search by voice** — the nearest station selling a module or a ship, the nearest
  system matching what you describe, and more — with the result system copied to your clipboard
  — plus [trade-route](search/trade-routes.md), [neutron](search/neutron-route.md),
  [Road-to-Riches](search/road-to-riches.md) and [mining](search/mining.md) planners.
- **A voice checklist**, **proactive callouts**, **route callouts**,
  **[community goals](elite/community-goals.md)**, **fleet-carrier tracking**, and a
  transparent, editable [persistent memory](using/memory.md).

### It acts on your word

- **Guarded ship controls** — a small [allowlisted set of keystrokes](automation/keybinds.md)
  behind a separate spoken confirmation, a combat/interdiction guard, and a hard abort.
- **Voice-authored [macros](automation/custom-macros.md)** and
  **[reflexes](automation/reflexes.md)** — name a sequence once, trigger it by voice.
- **[In-game comms](automation/comms.md)** — dictate a local or wing chat message without
  touching the keyboard.

### It brings the ship to life

- **Swappable [personas](using/personas-voice.md)** with an ElevenLabs, cloud, or local voice —
  and your own Commander facts kept separate, so switching voice never wipes them.
- **A named, voiced [crew](using/crew.md)** — distinct cast voices today, growing into
  role-aware characters you can address, drawn from the fighter pilots you've actually hired.
- **An ambient cockpit [soundscape](audio/ambient-audio.md)** — station chatter, a music
  director, alert cues — mixed underneath the ship's voice.
- **A glanceable [HUD](using/hud.md)** — on the desktop or inside your VR headset.

## Why COVAS++

Other Elite voice assistants converse and read your game state too. What sets COVAS++ apart:

- **Grounded, not guessed.** [Ship specs](elite/ship-specs.md), [module costs](search/outfitting.md),
  [blueprint materials](elite/blueprints.md), [engineer unlocks](elite/engineers.md) and your
  [credits](elite/currency-behavior.md) come from **bundled datasets and your real journal**, not the
  language model's training-cutoff memory — so the newest hulls stay accurate and money is never
  invented. [Voice search](search/index.md) is **structurally anti-hallucination**: any name spoken
  back must resolve against a canonical vocabulary, or you get a "did you mean…" instead of a made-up
  answer.
- **Galaxy-wide search & planning by voice** — the nearest station selling a
  [module](search/outfitting.md) or [ship](search/shipyards.md);
  [systems](search/star-systems.md), [stations](search/stations.md) and
  [factions](search/minor-factions.md) matching what you describe; [bodies](search/bodies.md) by type
  or exobiology; plus [trade-route](search/trade-routes.md), [neutron](search/neutron-route.md),
  [Road-to-Riches](search/road-to-riches.md) and [mining](search/mining.md) planners — each result
  copied to your clipboard for the galaxy map.
- **Immersion, not just answers** — an optional cockpit [ambient-audio](audio/ambient-audio.md)
  layer, a multi-voice [interactive crew](using/crew.md), a glanceable [HUD](using/hud.md) overlay,
  and swappable [personas](using/personas-voice.md).
- **It remembers you.** A transparent, editable [persistent memory](using/memory.md) of how you like
  to be addressed, your main ship, and standing preferences — a plain file you own, never leaving your
  machine.
- **Local-first and private.** Speech-to-text always runs **on your machine**, there's a fully
  offline mode, and your API keys are encrypted at rest.
- **Affordable to leave on.** A cost-engineered router keeps routine turns on a cheap model and
  escalates only when a turn earns it — no local model fighting Elite for your GPU.
- **Safety-first automation.** The [handful of keystrokes](automation/keybinds.md) it will send sit
  behind an allowlist, a separate spoken confirmation, a combat/interdiction guard, and a hard abort.
- **Hands-free option** for accessibility — a [voice-activity gate](getting-started/hands-free.md) so
  you never have to touch a key.

## Trust & scope

- **Every keystroke it can send is heavily guarded.** Ship controls sit behind an allowlist, a
  separate spoken confirmation, a combat/interdiction guard, and a hard abort — automation grows
  one proven action at a time, never by opening the floodgates.
- **It does not read game memory or use any private API.** Everything about your game comes
  from the log files Elite Dangerous writes to disk — the same source other community tools use.
- **It is not a Frontier product** and can't do anything the game doesn't expose through those
  files and your own key bindings.

## How the voice loop works

1. **Hold** the push-to-talk key and speak. A *listening* chirp plays; your mic is captured while held.
2. **Release.** A *processing* chirp plays; your speech is transcribed locally.
3. The transcript goes to the language model with your personality, recent conversation, and —
   when the question calls for it — your live game state.
4. A *done* chirp plays and the reply is **spoken aloud**.
5. **Cancel** anything mid-flight with a brief **tap** of the same key.

Every stage fails soft: a dead voice service falls back to on-screen text, a hiccup returns
you to idle — the session never crashes out from under you.

## Where to go next

- **New here?** Start with [Install & setup](getting-started/install.md), then
  [Running COVAS++](getting-started/running.md) and [The voice loop](getting-started/voice-loop.md).
- **Want the full feature tour?** Browse the sections in the left nav — each feature has its own
  page with example voice commands and its settings.
- **Just want to tune it?** See the [Configuration reference](configuration.md).

!!! tip "Most game features are off until you opt in"
    The core voice loop works out of the box. Game-awareness features that read Elite Dangerous
    (and especially the ones that press keys) are enabled per-feature in
    [`config.toml`](configuration.md) or on the [Settings page](control-panel.md). Each page here
    notes what its feature needs.
