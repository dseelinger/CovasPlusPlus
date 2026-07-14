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
- **Speaking speed** — a slider from **1.0× to 1.2×** (ElevenLabs' supported range; values are
  clamped so you can't push it out of range). Nudge it on the Settings page or say
  *"set the voice speed to 1.1."*

Prefer a **free, fully-local voice** with no external service at all? Switch `[tts].provider` to
**Piper**. Piper runs on your CPU alongside the game at no cost — the voice is good, if not quite as
smooth as ElevenLabs. See [Install & setup](../getting-started/install.md#run-fully-local-no-cloud).

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
> stays the guaranteed free floor.** Official **Azure Neural TTS** (the same voices with an API +
> SLA + free tier) is the version to actually depend on when that arrives.

## Turning personality off

Say *"turn personality off"* (or toggle it on the Settings page) and replies become plain and
neutral — no in-character address or campaign context. Turn it back on the same way.

## Related settings

| Setting | What it does |
|---------|--------------|
| `personality.enabled` | Whether the in-character system prompt is used at all |
| `elevenlabs.voice_id` | Which ElevenLabs voice speaks |
| `elevenlabs.speed` | Speaking speed, 1.0–1.2× |
| `tts.provider` | `elevenlabs` (cloud), `piper` (local, free), or `edge` (free neural, no SLA) |
| `edge.voice` | Edge voice ShortName when `tts.provider = edge` (e.g. `en-US-AriaNeural`) |

See the [Configuration reference](../configuration.md) for the full list.
