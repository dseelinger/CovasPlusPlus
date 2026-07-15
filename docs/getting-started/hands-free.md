# Hands-free listening

By default COVAS++ is **push-to-talk**: you hold a key to speak (see [The voice loop](voice-loop.md)).
If you'd rather not touch a key at all, switch on **continuous** (hands-free) mode and just start
talking — a voice-activity gate hears you, captures what you say, and runs the turn.

!!! note "Off by default, opt-in"
    Continuous mode is **not** on out of the box. Turn it on from the Settings page, by voice, or in
    [`config.toml`](../configuration.md). Push-to-talk keeps working in either mode.

## How it works

In continuous mode COVAS++ watches your microphone locally and looks for the moment you **start**
talking (a short burst of speech above a loudness threshold) and the moment you **stop** (a brief
trailing silence). Between those two points it captures your utterance and hands it to the **exact
same** path as a push-to-talk turn:

1. **Speech onset** — you start talking. A **listening** chirp plays and capture begins. If COVAS++
   was mid-reply, this **barges in** and cuts it off, just like grabbing the talk key.
2. **You keep talking** — short pauses (a breath mid-sentence) don't end the capture.
3. **Trailing silence** — once you've been quiet long enough, the utterance closes and the normal
   **processing → thinking → speaking** turn runs. Transcription is still **local** faster-whisper —
   nothing extra leaves your PC, and there's **no added cloud cost** just to listen.

Because it reuses the standard loop, everything you already know still applies: replies are short,
the cost router still picks the cheapest capable model, and **barge-in and cancel are preserved**.

## Turning it on

=== "By voice"

    Say **"switch to continuous listening"** (or "turn on hands-free"). To go back, say
    **"switch to push-to-talk"** or set the activation mode to `ptt`.

=== "Settings page"

    Open the control panel's **Settings** tab and set **Activation mode** (under *Voice input*) to
    `continuous`. It applies **live** — the mic listener starts immediately, no restart.

=== "config.toml"

    ```toml
    [listen]
    mode = "continuous"   # "ptt" (default) or "continuous"
    ```

## Tuning the voice-activity gate

The gate is a simple, local **energy** detector — no cloud, no extra dependency. Its knobs live in
`[listen]` and are all on the Settings page under *Voice input*:

| Setting | What it does |
|---------|--------------|
| **`energy_threshold`** | How loud a moment must be to count as speech. **Raise** it if background noise keeps opening a capture; **lower** it if quiet speech gets missed. |
| **`start_ms`** | How much continuous speech confirms you've started (debounces a click/clack). |
| **`min_speech_ms`** | Shortest capture that counts as a real utterance; briefer blips are dropped as noise. |
| **`hangover_ms`** | How much trailing silence ends an utterance. **Longer** tolerates mid-sentence pauses but reacts slower; **shorter** is snappier but may cut you off. |
| **`frame_ms`** | Analysis frame length. Rarely needs changing. |

!!! warning "Use a headset for hands-free"
    With **open speakers**, the mic can hear COVAS++'s own voice and treat it as you barging in.
    A headset (or a push-to-talk setup) avoids that. If you must use speakers, raise
    `energy_threshold` and keep the volume modest.

## Wake word (optional)

Continuous mode runs a turn on **anything** the gate captures — including things you say to
someone else in the room. To avoid that, set a **wake word**: an arming phrase (like `COVAS`)
that a hands-free capture must contain before it becomes a turn. It's **off by default** and only
affects continuous mode — **push-to-talk is never gated**, so a deliberate press always runs.

With a wake word set, a hands-free capture only reaches the model if you said the phrase, and the
phrase is **stripped** before your words go to the LLM (so it doesn't hear its own name):

- *"COVAS, what's my fuel?"* → runs the turn on **"what's my fuel?"**
- *"is dinner ready yet?"* (to someone else) → **dropped**, no turn, no cost
- *"COVAS"* on its own → nothing to answer, so it just returns to Idle

Because the check runs on the **local** Whisper transcript, a false trigger costs **nothing** — the
drop happens before any cloud call.

=== "By voice"

    Say **"set the wake word to COVAS"**. To turn it off again, **"clear the wake word"** (set it
    blank).

=== "Settings page"

    Under *Voice input*, set **Wake word** to your phrase (blank = off).

=== "config.toml"

    ```toml
    [listen]
    wake_word = "COVAS"     # blank (default) = off; only affects continuous mode
    wake_word_fuzzy = true  # tolerate STT slips like "Kovas"/"Covis"
    ```

!!! tip "Fuzzy matching forgives mistranscriptions"
    A short call sign is easy for speech-to-text to hear a letter off ("Kovas", "Covis"). With
    **`wake_word_fuzzy`** on (the default), those near-misses still arm the turn, so a single slip
    doesn't swallow your command. Turn it off to require an exact (still case-insensitive) match.

## Which should I use?

- **Push-to-talk** (default) — precise, zero false triggers, great with a busy mic or open speakers.
  Bind the talk key to a HOTAS button with JoyToKey and it's effectively hands-on-throttle.
- **Continuous** — truly hands-free; ideal in a quiet room with a headset when you want to just talk.

You can switch between them at any time, by voice or on the Settings page.
