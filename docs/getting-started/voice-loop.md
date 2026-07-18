# The voice loop

Everything you do with COVAS++ goes through one **push-to-talk** loop. Understanding it takes a
minute and makes everything else obvious.

## A single turn, step by step

1. **Hold** the push-to-talk key (<kbd>[</kbd> by default) and speak. The moment you press, a
   **listening** chirp plays and your microphone is captured — for as long as you hold.
2. **Release.** A **processing** chirp plays, then a soft **thinking** bed fades in and hums
   gently underneath while COVAS works. Your speech is transcribed **locally** by faster-whisper —
   nothing leaves your PC just to turn your voice into text.
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
  off and a fresh capture starts. You never have to wait for it to finish. Playback is silenced
  *before* the mic opens (and a short leading slice of the capture is muted as a backstop), so the
  new capture never picks up the tail of the reply still leaving the speakers.
- **The panel's CANCEL button** also stops an in-progress reply at any time.

The tap-versus-hold threshold is `[keys].tap_cancel_ms` (default 400 ms) in
[`config.toml`](../configuration.md).

!!! tip "Prefer not to hold a key?"
    Turn on **[hands-free listening](hands-free.md)** — a local voice-activity gate starts a turn
    when you begin talking and ends it after a short silence, reusing this same loop (barge-in and
    cancel included). Push-to-talk stays the default.

## Sound cues

COVAS++ plays a short local sound at each stage so you get instant, zero-latency feedback without
looking at the screen:

| Cue | When it plays |
|-----|---------------|
| **listen** | The instant you press to talk (before you even speak) |
| **processing** | A one-shot tick the moment you release — "got it" |
| **thinking** | A soft, looping bed that **fills the whole wait** while it transcribes / thinks / searches |
| **completed** | The moment an answer is ready, just before speech plays |
| **failure** | Any failure — no speech heard, or a service error |

The **thinking** bed is the audio equivalent of a spinning "loading" icon: after the one-shot
*processing* tick, it hums gently under the moment so a slow turn never feels like COVAS ignored
you, and it **stops the instant** the reply starts speaking (or you cancel / barge in). It's soft
and non-verbal by design — never a spoken "let me look that up." Prefer just the single tick?
Turn it off with **"turn the thinking sound off"** or the **Thinking sound** toggle on the Settings
page (`[audio].thinking_bed`).

Cues are **drop-in folders**, not config paths. COVAS++ ships original defaults, so you hear
chirps out of the box. Each cue type is a folder; a **random** file from it plays each time, so
you add variety just by dropping in more files (1 file or 50 — no config edit, no fixed count).
Drop your own loopable audio into `sounds/thinking/` to replace the default bed.

To use your **own** cues, click **Open cues folder** in the control panel (or open
`<data dir>/sounds/` yourself) and drop audio into the matching `sounds/<type>/` folder:

- While a type's folder holds **≥1 file**, your set **replaces** the shipped default for that type.
- Empty the folder to fall back to the default; with neither, that cue is simply silent.

Click **Reload cues** next to it to pick up the change **without restarting** — it re-scans every
`sounds/<type>/` folder and reports what it found (e.g. "reloaded — 3 failure, 1 thinking"), so a
file you just dropped in (or removed) joins the rotation on your very next press. This covers the
cue types above (`listen`/`processing`/`completed`/`failure`/`thinking`); the separate ambient
drop-in content described in [Ambient audio](../audio/ambient-audio.md#drop-in-content) (SFX,
music, chatter) still needs a restart to pick up changes.

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
