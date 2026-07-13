# The voice loop

Everything you do with COVAS++ goes through one **push-to-talk** loop. Understanding it takes a
minute and makes everything else obvious.

## A single turn, step by step

1. **Hold** the push-to-talk key (<kbd>[</kbd> by default) and speak. The moment you press, a
   **listening** chirp plays and your microphone is captured — for as long as you hold.
2. **Release.** A **processing** chirp plays. Your speech is transcribed **locally** by
   faster-whisper — nothing leaves your PC just to turn your voice into text.
3. The transcript goes to Claude with your personality, your recent conversation, and — only when
   the question needs it — a compact snapshot of your live game state.
4. When the reply is ready, a **done** chirp plays and the answer is **spoken aloud** in your
   chosen voice.
5. If anything goes wrong at any stage (no speech heard, a service hiccup), a **failed** chirp
   plays and you drop cleanly back to idle.

The panel's status light walks through **LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE**
so you can see exactly where a turn is.

## Cancelling and barging in

- **Cancel / stop** — while COVAS++ is thinking or speaking, **tap** the talk key briefly (a
  press shorter than ~400 ms). It stops instantly and returns to idle. This is the same key you
  hold to talk; a quick tap means "stop," a real hold means "listen."
- **Barge-in** — while a reply is being spoken, just **hold** the talk key again. The speech cuts
  off and a fresh capture starts. You never have to wait for it to finish.
- **The panel's CANCEL button** also stops an in-progress reply at any time.

The tap-versus-hold threshold is `[keys].tap_cancel_ms` (default 400 ms) in
[`config.toml`](../configuration.md).

## Sound cues

COVAS++ plays a short local sound at each stage so you get instant, zero-latency feedback without
looking at the screen:

| Cue | When it plays |
|-----|---------------|
| **listen** | The instant you press to talk (before you even speak) |
| **processing** | While it's working — transcribing, thinking, or searching the web |
| **completed** | The moment an answer is ready, just before speech plays |
| **failure** | Any failure — no speech heard, or a service error |

Cues are **drop-in folders**, not config paths. COVAS++ ships original defaults, so you hear
chirps out of the box. Each cue type is a folder; a **random** file from it plays each time, so
you add variety just by dropping in more files (1 file or 50 — no config edit, no fixed count).

To use your **own** cues, click **Open cues folder** in the control panel (or open
`<data dir>/sounds/` yourself) and drop audio into the matching `sounds/<type>/` folder:

- While a type's folder holds **≥1 file**, your set **replaces** the shipped default for that type.
- Empty the folder to fall back to the default; with neither, that cue is simply silent.

In a source run the data dir is the project root (so the folder is `./sounds/<type>/`, git-ignored);
a packaged build uses `%APPDATA%\COVAS++\sounds\`. Supported formats: `.wav`, `.ogg`, `.flac`, `.mp3`
(mp3 depends on your libsndfile build).

## Replies are short on purpose

Answers are **spoken**, so they're kept to a few sentences by design. That keeps them quick to
listen to over the noise of the game, and it keeps costs down. When you genuinely want the long
version, ask for it — say **"give me the full breakdown"** and COVAS++ raises its own length cap
for that one turn.

## Cost tiering — cheap by default, smart when it matters

COVAS++ controls cost by **routing each turn to the cheapest capable model**, escalating only when
a turn earns it. This happens automatically:

- **Most turns → Haiku** — banter, acknowledgements, checklist reads, status readouts, anything
  answerable from context you already have. Fast and cheap.
- **Escalate → Sonnet** — when a turn needs depth or analysis, or current information from the web.
  Trigger it deliberately with phrases like **"think hard"** or **"walk me through…"**.
- **Premium → Opus** — only on an explicit ask, e.g. **"use Opus for this."**

Every routing decision is logged with its reason, so you can see (in the log or the panel) which
model answered and why. You can also **pin** a tier if you want to force one. The router, its
phrases, and the tiers are all configurable — see [Settings by voice](../using/settings.md) and
the [Configuration reference](../configuration.md#cost-router-router).

A few other cost levers work quietly in the background: prompt **caching** (with a 1-hour lifetime
that survives the long gaps between in-game voice turns), the short reply cap above, and a limit
on how many web searches a single reply may run. You don't have to think about any of it — it's on
by default.

!!! tip "Iterating for free"
    A **dev-mock mode** swaps the language model, voice, and speech-to-text for fakes so the whole
    loop runs with **zero API calls and zero cost**. It's for tinkering with the app itself, not
    for real use — flip `[dev].mock` on, or set the `COVAS_MOCK=1` environment variable for a
    one-off run.

## Next

You know how a turn works. Now explore what you can *say* — start with
**[Personas & voice](../using/personas-voice.md)**, or jump to any feature in the left nav.
