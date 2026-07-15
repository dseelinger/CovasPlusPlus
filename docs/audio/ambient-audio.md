# Ambient audio

COVAS++ has an optional **atmospheric audio layer** — an in-cockpit soundscape of radio comms,
ambient chatter, music, and layered alert cues, all mixed underneath your companion's voice. It's a
big, entirely optional subsystem, and it's **off by default.**

!!! info "Opt-in, and off by default"
    The whole layer is gated behind a master switch (`[audio].enabled`), and each part has its own
    toggle on top of that. Turn the master on first (it changes how the audio device is opened —
    restart to apply), then enable the parts you want. Most of it also needs
    [game-state monitoring](../elite/monitoring.md) so game events can drive it.

## What's in it

| Part | What it adds | Toggle |
|------|--------------|--------|
| **Comms voices** | Voices the game's comms-panel lines (NPC/station messages and direct player DMs) over a radio-treated bus | `audio.comms.enabled` (on by default *within* the layer) |
| **Space chatter & SFX** | Occasional ambient radio chatter (**populated systems only**) and sound effects | `audio.cues.enabled` |
| **Ambient music** | Context-crossfaded background music from your own local tracks | `music.enabled` |
| **Interdiction cue** | A layered "pirate interdiction" moment — a warning sting, your companion's threat line, and the pirate's line | `audio.interdiction.enabled` |

Your companion's own voice always plays clean and full-volume on its own bus, so a radio line can
never bury it. Each bus (COVAS, comms, ambient/SFX, music, alert) has its own volume trim.

## The safety idea behind it

The design keeps the language model **out of the realtime audio path** — it only ever produces text
that's then validated and routed, never live audio. Two consequences you'll notice:

- **Player messages are read verbatim.** Direct player DMs are never reworded. Only NPC lines may be
  lightly varied, and only when validated.
- **The Open-play firehose is dropped.** Local/wing chatter and anything it can't clearly attribute
  is *not* voiced — the gate fails closed. Repeated station lines aren't re-read every jump either.

## Space chatter

Ambient chatter is the sound of *other people* — station traffic, patrols, market buzz — so it only
plays in **populated systems**; out in empty or unpopulated space it stays quiet. How often it
speaks **scales with the system's population**: a busy hub chatters near the fast end, a sparse
outpost near the slow end. You set the two bounds — `[audio.chatter].min_seconds` (busiest systems)
and `max_seconds` (barely-populated) — and lower `full_population` to make more systems feel lively.
All three are on the Settings page.

## Voices for the cast

Everything the audio layer speaks (NPCs, comms, chatter) gets a voice from a pool of **random
ElevenLabs voices**, drawn automatically from your ElevenLabs library (minus your companion's own
voice, so the cast always sounds like someone else). Voices are **random but consistent within a
play session**:

- **NPC / station** speakers keep one voice for as long as you're in a system, then get re-cast when
  you **jump** — the liner captain here sounds the same all the way through, a different captain in
  the next system sounds different.
- **Players** keep their voice for the whole session (the last 25 commanders are remembered), so a
  wing or an operation keeps stable per-person voices.
- **Chatter** picks a fresh random voice per line, so the background radio sounds like many people.

!!! warning "This uses ElevenLabs credits"
    The cast now defaults to **ElevenLabs**, which burns credits on every comms/chatter line. To go
    back to the free, game-friendly local path, set `[audio.voices].cast_provider = "piper"` and add
    Piper `.onnx` entries to `[audio.voices].pool` (or set `random_el = false` for a single voice).
    For **free neural** voices with hundreds of distinct speakers, set `cast_provider = "edge"` (no
    key) or `cast_provider = "azure"` (free tier + SLA) — see below — perfect for keeping ambient
    chatter off your ElevenLabs bill.

### Per-role providers (the voice ladder)

Every cast voice is synthesized through a small **provider registry**, so each cast **role** can use
a different TTS provider. Set overrides in `[audio.voices.providers]`; roles you don't list fall back
to `cast_provider`. Roles: `comms`, `chatter`, `player`, `interdiction` (and `cast` = the pool
default). COVAS's own **persona** voice is not a cast role — it stays on `[tts].provider`.

```toml
[audio.voices.providers]
chatter = "azure"        # free-tier neural voices for throwaway ambient lines (SLA-backed)
comms   = "elevenlabs"   # premium voices for station/NPC comms
```

This is the seam additional voice providers plug into: each registers under its name and becomes
selectable for any role — a ladder from free/local Piper through free-neural Edge/Azure and cheap
cloud (OpenAI) to premium (ElevenLabs), mirroring the LLM cost router.

**`edge` (edge-tts) — free neural voices, no key.** The `edge` provider speaks through Microsoft
Edge's "Read Aloud" Azure Neural voices: hundreds of distinct voices, no API key, and no ElevenLabs
credits — ideal for the NPC/comms/chatter cast. Pin explicit voices by adding pool entries with
`provider = "edge"` and `ref = "<ShortName>"` (list them with `python -m edge_tts --list-voices`).

!!! warning "Edge is optional and has no SLA"
    `edge-tts` rides an **undocumented** endpoint Microsoft intends for the Edge browser: **ToS-gray,
    no SLA**, and it periodically breaks when Microsoft rotates its anti-abuse tokens. Cast Edge
    voices **fall silent** when the endpoint is down (they never crash the loop), and the COVAS
    persona voice on `[tts].provider = "edge"` **falls back to Piper**. Keep **Piper** as your
    guaranteed free floor; for the same voices without the asterisk, use **Azure** ↓.

**`azure` — the reliable sibling of Edge (free tier + SLA).** The `azure` provider uses **official
Azure Neural TTS**: the *same* voices as Edge, but over the Speech service with a real API, an SLA,
and a **free monthly tier (~0.5M characters)** — the shippable way to give the cast big voice variety
at low/zero cost, with no ToS/reliability asterisk. Needs a Speech resource **key** (`AZURE_SPEECH_KEY`
env var or `AzureSpeechKey.txt`) and **region** (`[azure].region`). Set `cast_provider = "azure"` or a
per-role override, and pin explicit voices with pool entries `provider = "azure"`, `ref = "<ShortName>"`.
A key/region/service problem makes those cast voices **fall silent** (never crashes the loop).

**`openai` — a cheap cloud voice.** The `openai` provider speaks through an OpenAI-compatible
`audio/speech` endpoint. Its voice set is small and fixed (alloy, nova, shimmer, …), so it's better as
a persona or a *supplemental* cast voice than a large diverse cast. Needs `OPENAI_API_KEY`
(env var or `OpenAIAPIKey.txt`); `[openai_tts].base_url` is configurable for compatible endpoints. Pin
voices with pool entries `provider = "openai"`, `ref = "<voice>"`. A key/service problem makes those
cast voices **fall silent** (never crashes the loop).

!!! note "Cartesia is persona-only"
    The **Cartesia** (`cartesia`) low-latency voice is a **persona** provider — a snappier alternative
    to ElevenLabs for COVAS's *own* voice ([tts].provider). It is **not** registered for the cast, so
    `cast_provider = "cartesia"` (or a per-role override) has no effect. See
    [Personas & voice](../using/personas-voice.md#low-latency-premium-voice-via-cartesia-cartesia).

## Drop-in content

Audio and line content is **drop-in**: on startup COVAS++ scans a set of convention folders and
overlays whatever it finds onto the cues, music contexts, and chatter pools — so adding content is
just dropping a file in, no config editing:

- `audio/sfx/<cue>/` — sound effects for a cue
- `audio/music/<context>/` — music tracks for a context
- `content/chatter/<category>.txt` — chatter lines (one per line; `#` starts a comment)
- `content/interdiction_threat.txt` — interdiction threat lines

Files you drop in **override** the shipped defaults. A missing or empty folder just means that cue
is silent — no error. COVAS++ creates the folder skeleton (with a README in each) on first run, and
logs a content-status summary showing what's populated and what's still silent. These folders are
git-ignored — the assets are yours to supply.

!!! note "Voice control"
    You can steer the layer by voice: *"mute the chatter," "quiet the comms," "turn the music
    down," "stop the music," "silence all the background audio," "turn the ambient audio back on."*
    Your own replies are never affected.

## Settings

The audio layer lives under the `[audio]`, `[audio.buses.*]`, `[audio.comms]`, `[audio.voices]`,
`[music]`, and related sections. The Settings page has an **"Ambient audio"** group for the common
knobs — master and per-part enables, per-bus volumes, and the voice-cast provider — most of which
apply live. See the [Configuration reference](../configuration.md#ambient-audio-audio-music) for the
full set.
