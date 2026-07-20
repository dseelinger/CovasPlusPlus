# Personas & voice

COVAS++ speaks in character. Two things are kept deliberately separate so you can change one
without disturbing the other:

- **Persona** — the *voice and register*: how the companion talks (dry and professional, warm,
  sardonic, and so on). Swappable at will.
- **Campaign** — *your personal Commander facts*: your name, ranks, holdings, ongoing goals.
  Yours alone, kept private, and **never wiped when you switch persona.**

When personality is on, the system prompt is composed as **Base + selected Persona + your
Campaign**. When it's off, replies are plain and neutral (no "Commander" address, no campaign
context).

## Choosing a persona

COVAS++ ships a set of read-only preset personas plus a shared base. The easiest way to switch is
the **Personality tab** in the [control panel](../control-panel.md):

- Pick a persona from the list and see a **preview** of its register.
- Select it — the *next* reply changes voice/register.
- Your **Campaign** text is untouched by the switch.

You can also edit the persona box and **Save as custom** to create your own persona (written to a
git-ignored folder so it stays private and never gets committed). Custom personas appear in the
list alongside the presets.

### Auto-paired voices per persona

Switching persona should *sound* like a different character, not just read differently. So at
startup — if you're on **ElevenLabs** — COVAS++ quietly asks the LLM, **once**, to pair a fitting
default voice with each shipped persona, matching the persona's character against your voice
library's metadata (gender, age, accent, description). Then, whenever you pick a persona you
**haven't** set a voice for, it arrives already wearing that paired voice.

- **Your choice always wins.** Set a voice for a persona yourself (Settings → Text-to-speech, or
  *"use the George voice"*) and that's remembered as *your* pick for that persona — it's never
  overwritten by the auto-pairing.
- **It never slows startup.** The pairing runs on a background thread; until it lands (or if it
  can't — no key, offline, LLM off), the app is fully usable on your current voice.
- **It's computed once and cached.** The result is written to a git-ignored per-account file
  (`personalities/voice_pairings.json`) keyed to your persona set + voice list, so it only
  recomputes when one of those actually changes — not every launch.
- **Cost-aware.** It's a single cheap-tier call, gated by the [optimization
  level](../elite/proactive-callouts.md): skipped on lean/constrained setups. Turn the whole thing
  off with `[personality].auto_voice_pairing = false`.

Only ElevenLabs is auto-paired today (its catalog carries the richest metadata); the design leaves
room for other providers to plug in later.

### Writing a persona that actually stays in character

The strongest personas give the model something to **imitate**, not just adjectives to admire. When
you write (or edit) one, include:

- **A few verbal tics** — signature words, a cadence, a move it makes ("Copy," "darling," a dry aside
  at the end). Concrete tics survive across turns far better than "witty" or "warm."
- **One or two short in-character example lines** covering the beats that come up most: one where it
  **can't** do a ship action (COVAS never flies the ship — the decline should sound like the
  character, not a flat apology), one where it hands over a **number**, and one where it **flags
  danger** or a bad plan.

Keep example lines as ordinary prose in the persona body — that's the text the model actually reads.
In the shipped presets, the single quoted line shown as a **preview** is UI-only and is stripped out
before the model sees it, so it's not the place for instructions or examples. The Base prompt already
tells every persona to hold its voice even on short, practical, or can't-do turns, so you don't need
to repeat that — just show *how* your character sounds when it happens.

## Editing your campaign

The **Campaign editor** on the Personality tab holds your personal facts — your Commander's name,
ranks, what you fly, what you're working toward. Save it and subsequent replies reflect the
updated facts. Because it's separate from the persona, trying out different voices never costs you
your campaign.

!!! info "Migrating from a single personality file"
    If you started from the shipped `personality.txt`, its voice becomes a persona and its
    personal section becomes your Campaign — so nothing is lost when you move to the persona +
    campaign split.

## The spoken voice

By default COVAS++ speaks with a **free Edge neural voice** (`[tts].provider = "edge"`, see below) —
no key, no per-word cost. Prefer the **premium ElevenLabs** cloud voice instead? Set
`[tts].provider = "elevenlabs"`; then you control:

- **Which voice** speaks — pick from your ElevenLabs library (Settings → Text-to-speech, or by
  voice: *"use the George voice"*).

**Speaking speed** is **one normalized control** (`[tts].speed`) that applies to *whichever* TTS
provider is active — `1.0` = the voice's normal pace, below `1.0` slower, above `1.0` faster (range
**0.5×–2.0×**). COVAS maps that single value into each provider's own speed mechanism and clamps it
to what that voice can actually do, so you can't push any provider out of its safe range:

| Provider | Real speed range | How it's applied |
|----------|------------------|------------------|
| ElevenLabs | **0.7×–1.2×** | native `voice_settings.speed` (quality-safe band — you can now slow *below* normal) |
| OpenAI | **0.25×–4.0×** | native `speed` parameter |
| Edge | wide (±%) | SSML/`rate` percentage (e.g. `+50%`, `-20%`) |
| Azure | wide (±%) | SSML `<prosody rate="…">` |
| Cartesia | wide | its `[-1, 1]` speed control |
| Piper | wide | `length_scale` (inverse — larger = slower) |

Nudge it on the Settings page or say *"set the voice speed to 1.5."* A value beyond a provider's
range is safely capped, and because only the normalized value is stored, switching providers never
carries an out-of-range speed across.

!!! warning "Azure and Cartesia are experimental — off by default"
    The **Azure Neural** and **Cartesia** TTS providers are **experimental**: they ship **disabled**
    for everyone, aren't offered on the first-run wizard or the public Settings dropdown, and are
    gated at provider registration. Use one just for yourself by adding
    `experimental.azure_tts.enabled = true` (or `experimental.cartesia_tts.enabled = true`) to your
    git-ignored `overrides.json` (see
    [Experimental feature flags](../configuration.md#experimental-feature-flags)) **and** setting
    `tts.provider` to `"azure"` / `"cartesia"`.

Prefer a **free, fully-local voice** with no external service at all? Switch `[tts].provider` to
**Piper**. Piper runs on your CPU alongside the game at no cost — the voice is good, if not quite as
smooth as ElevenLabs. See [Install & setup](../getting-started/install.md#local-cpu-only-speech).

### Free neural voices via Edge (`edge`)

Want free voices that sound closer to the cloud? Set `[tts].provider = "edge"` to speak through
**Microsoft Edge's "Read Aloud" neural voices** (the `edge-tts` project) — hundreds of voices, **no
API key**. Set `[edge].voice` to a voice ShortName (e.g. `en-US-GuyNeural`); browse the catalog with
`python -m edge_tts --list-voices`. Edge is especially handy for the [voice cast](../audio/ambient-audio.md#voices-for-the-cast)
so ambient chatter never burns ElevenLabs credits.

> ⚠ **Optional, not load-bearing.** `edge-tts` rides an **undocumented** endpoint Microsoft intends
> for the Edge browser: it's **ToS-gray**, has **no SLA**, and periodically breaks when Microsoft
> rotates its anti-abuse tokens. When it's unavailable, COVAS's persona voice **falls back to Piper**
> (if `[piper].model` is set, else it degrades to text), and cast Edge voices fall silent — **Piper
> stays the guaranteed free floor.** For the *same* voices without the asterisk, use **Azure** ↓.

### Reliable neural voices via Azure (`azure`)

Want the same neural voices as Edge, but **dependable**? Set `[tts].provider = "azure"` to use
**official Azure Neural TTS** — a real API, an **SLA**, and a **free monthly tier (~0.5M characters)**
that comfortably covers a lot of talking before any spend. It's the shippable, no-asterisk way to give
the [voice cast](../audio/ambient-audio.md#voices-for-the-cast) big voice variety at low/zero cost.

Setup: create a **Speech** resource in the Azure portal, then provide its **key** (enter it on the
Settings **API keys** card, or paste it into `AzureSpeechKey.txt` — stored DPAPI-encrypted at rest)
and its **region** (`[azure].region`, e.g. `eastus`). Pick a voice ShortName in `[azure].voice` (same names as Edge), and
optionally an SSML speaking style in `[azure].style` (e.g. `cheerful`, `newscast` — voice-dependent).
If the key/region is missing or the service errors, the persona degrades to text and cast voices fall
silent — it never crashes the loop.

### Cheap cloud voice via OpenAI (`openai`)

Set `[tts].provider = "openai"` to speak through an **OpenAI-compatible** `audio/speech` endpoint — a
**cheap** cloud voice with a small, fixed voice set (great as a persona, or a supplemental cast voice).
Provide the key on the Settings **API keys** card (or paste it into `OpenAIAPIKey.txt` — stored
DPAPI-encrypted at rest; the same key a future OpenAI LLM provider will use). Pick a voice in `[openai_tts].voice` (`alloy`, `nova`, `shimmer`, …), a model in
`[openai_tts].model` (`gpt-4o-mini-tts` is cheap; `tts-1` also works), and optionally a tone steer in
`[openai_tts].instructions` (honored by newer models). `[openai_tts].base_url` is configurable, so any
OpenAI-compatible endpoint works. A missing key or service error degrades the persona to text (cast
voices fall silent) — it never crashes the loop.

### Low-latency premium voice via Cartesia (`cartesia`)

Want the **snappiest** persona voice? Set `[tts].provider = "cartesia"` to use **Cartesia Sonic** — a
premium alternative to ElevenLabs tuned for very low **time-to-first-audio**, which is what a live
companion feels most. It **streams**, so COVAS starts talking sooner. This is a **persona-only** voice —
it's deliberately *not* offered for the NPC/comms/chatter cast (it's premium, and its value is the live
reply, not background chatter). Provide the key on the Settings **API keys** card (or paste it into
`CartesiaAPIKey.txt` — stored DPAPI-encrypted at rest) and a
**voice id** in `[cartesia].voice` (browse the library at [play.cartesia.ai](https://play.cartesia.ai)
or `GET https://api.cartesia.ai/voices`); set the model in `[cartesia].model` (e.g. `sonic-2`). A
missing key/voice or service error degrades the persona to text — it never crashes the loop.

> **Alternative:** Deepgram **Aura** is a comparable low-latency option; Cartesia Sonic was chosen to
> start. The provider seam makes adding Aura later a drop-in.

### Voice follows your reply language

If you set a non-English [reply language](language.md), COVAS++ makes the **voice** follow it too —
so a German reply is read by a voice that actually pronounces German, not an English voice stumbling
through it. This is on by default (`[language].match_voice = true`).

- **It only steers a voice that would mispronounce.** Edge and Azure tag every neural voice with a
  locale (`de-DE-…`, `fr-FR-…`, …). When your reply language turns non-English and the configured
  voice can't speak it, COVAS switches to a locale-matched voice from the same catalog (keeping the
  same gender where it can). A voice that already speaks the language is left exactly as it is.
- **Your explicit pick is respected.** If *you* chose the voice, COVAS won't silently swap it — it
  keeps your choice and logs a mismatch warning instead, so switching is your call.
- **ElevenLabs, OpenAI, Piper are left alone.** ElevenLabs and OpenAI voices are multilingual (the
  model handles the language), and Piper is one voice per downloaded model — none carry a locale to
  steer within. Only **Edge** and **Azure** auto-steer.
- **The voice pickers follow too.** With a non-English reply language set, the Edge/Azure voice
  dropdowns on the Settings page list voices for *that* language, so picking a matching voice by hand
  is easy.

Prefer to keep whatever voice you configured regardless of language? Set `[language].match_voice =
false` (or *"turn match voice to language off"*).

## Turning personality off

Say *"turn personality off"* (or toggle it on the Settings page) and replies become plain and
neutral — no in-character address or campaign context. Turn it back on the same way.

## Related settings

| Setting | What it does |
|---------|--------------|
| `personality.enabled` | Whether the in-character system prompt is used at all |
| `language.match_voice` | When the reply language is non-English, steer an Edge/Azure voice that can't pronounce it to one that can (default on; explicit picks are kept and flagged, not overridden) |
| `elevenlabs.voice_id` | Which ElevenLabs voice speaks |
| `tts.speed` | One normalized voice speed (0.5–2.0×, 1.0 = normal) for the active provider; each maps + clamps it to its own range (ElevenLabs 0.7–1.2, OpenAI 0.25–4.0, Edge/Azure/Cartesia/Piper wider) |
| `tts.provider` | `edge` (free neural, default), `azure` (free-tier neural + SLA), `openai` (cheap cloud), `cartesia` (low-latency premium persona), `elevenlabs` (cloud), or `piper` (local, free) |
| `edge.voice` | Edge voice ShortName when `tts.provider = edge` (e.g. `en-US-AriaNeural`) |
| `azure.region` / `azure.voice` / `azure.style` | Azure region, voice ShortName, and optional SSML style when `tts.provider = azure` |
| `openai_tts.base_url` / `.model` / `.voice` / `.instructions` | OpenAI-compatible endpoint, model, voice, and optional tone steer when `tts.provider = openai` |
| `cartesia.model` / `.voice` / `.language` | Cartesia Sonic model, voice id, and language when `tts.provider = cartesia` (persona-only) |

See the [Configuration reference](../configuration.md) for the full list.
