# COVAS++

**A local voice AI companion for [Elite Dangerous](https://www.elitedangerous.com/), for Windows.**
Hold a key, talk, and get a spoken reply in character. COVAS++ converses, watches your
game state, tracks a checklist, runs galaxy-wide searches by voice, presses the odd ship
control on request, and can look things up on the web — all through one push-to-talk loop.

!!! info "Unofficial, fan-made"
    Elite Dangerous is a trademark of Frontier Developments plc. COVAS++ is not
    affiliated with, endorsed by, or supported by Frontier.

## What it is

A companion that sits alongside the game and talks with you. You press a key, speak, and it
answers out loud in the voice and character you choose. Because it reads the same journal
and status files the game already writes to disk, it knows where you are, how your ship is
doing, and what you've been up to — so its answers are grounded in your actual game, not
guesses.

- **Push-to-talk voice** — hold a key, speak, release. Speech is transcribed **on your own
  machine**; nothing leaves your PC just to hear you.
- **Situational awareness** — "Where am I? How's my fuel? What did I just do?" answered from
  live telemetry.
- **Galaxy search by voice** — the nearest station selling a module or a ship, the nearest
  system matching what you describe, and more — with the result system copied to your clipboard.
- **A voice checklist**, **proactive callouts**, **route callouts**, **community goals**,
  **fleet-carrier tracking**, and a handful of **guarded ship controls**.
- **In character** — swappable personas, an ElevenLabs or local voice, and your own personal
  Commander facts kept separate so switching voice never wipes them.

## What it does **not** do

COVAS++ is a **conversation and knowledge companion, not a flight-control tool.**

- **It does not fly your ship.** No autopilot, no combat automation, no flying you between
  stars. It offers situational awareness, lookups, and banter — not hands-off flight.
- **The few keystrokes it will send are heavily guarded.** The one ship control it can press
  (toggle landing gear) sits behind an allowlist, a separate spoken confirmation, a
  combat/interdiction guard, and a hard abort. Auto-honk (firing the Discovery Scanner on
  arrival) is the only other keystroke, and it's off by default and combat-gated.
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
