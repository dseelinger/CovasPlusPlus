# Reply language

COVAS++ can **reply in your language**. Set one language and the companion responds in it — spoken
and on-screen — no matter what language you type or speak.

```
"COVAS, reply in German."
```

…or set **Reply language** on the [Settings page](../control-panel.md), or `[language].reply` in
[`config.toml`](../configuration.md).

## Supported languages

Reply language is deliberately a **curated list**, not "any language":

- English *(default)*
- German (Deutsch)
- French (Français)
- Russian (Русский)
- Spanish (Español)
- Portuguese (Português)

We only offer languages we intend to support end-to-end. A half-localized experience — a companion
that answers in German but mishears your German speech — feels more broken than an honestly
English-only one, so the list grows only as the rest of the pipeline catches up.

!!! note "What this does today (and doesn't yet)"
    This setting changes the language COVAS **replies in** — the single cheapest, highest-impact
    slice of localization. Your **speech → text follows it automatically**: `[whisper].language`
    ships as `"follow"`, so setting the reply language moves Whisper's transcription language with
    it — no separate step. One piece is still up to you:

    - **The voice that reads the reply.** Pick a [TTS voice](personas-voice.md) that speaks your
      language (Edge and Azure cover many; Piper is per-voice-model). A voice that can't pronounce
      the language will read it awkwardly.

    The one caveat for STT: a `.en` Whisper model (e.g. `small.en`) is **English-only**. Before
    setting a non-English reply language, switch to a **multilingual** model (e.g. `small`) on the
    [Settings page](../control-panel.md) — otherwise COVAS logs a warning and transcribes your
    speech poorly. Locale-aware voice pairing, translated control-panel text, and localized
    number/date formatting are still on the roadmap.

## How it works

Reply language is a single instruction added to the model's system prompt ("respond in French…"),
so it works on **every LLM provider** (Anthropic, OpenAI-compatible, Gemini) and rides the cached
prompt prefix — it costs nothing extra per turn. Elite Dangerous proper nouns (system, station,
ship, module, commodity, engineer and keybind names) are kept **verbatim** so grounding and voice
search still resolve them against their canonical names.

The English default adds **nothing** to the prompt, so if you're playing in English everything is
exactly as before.
