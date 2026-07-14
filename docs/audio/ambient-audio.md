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
knobs — master and per-part enables, per-bus volumes, and the comms voice pickers — most of which
apply live. See the [Configuration reference](../configuration.md#ambient-audio-audio-music) for the
full set.
