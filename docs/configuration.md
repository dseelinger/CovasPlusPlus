# Configuration reference

Everything is driven by **`config.toml`** — the single, fully-commented source of default
settings, with **relative paths** so your checkout stays portable. Anything you change in the
[web Settings page](control-panel.md) or [by voice](using/settings.md) is written to
**`overrides.json`** and layered on top at runtime, so `config.toml` stays pristine.

!!! tip "Three ways to change a setting"
    Edit `config.toml` directly · toggle it on the [Settings page](control-panel.md) · or say it
    [by voice](using/settings.md). To **reset** a setting, delete its key from `overrides.json` or
    click **Reset** on the Settings page.

!!! warning "Enabling a capability applies on restart"
    Turning a whole feature on or off takes effect the next time you launch. A few settings (the
    Whisper model, bus volumes, some audio toggles) apply live.

This page groups the settings by their `config.toml` section. It's a reference — `config.toml`
itself carries a comment on every line.

---

## Voice input (`[keys]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `keys.push_to_talk` | `[` | The key you **hold** to talk; a brief **tap** cancels |
| `keys.tap_cancel_ms` | `400` | A press shorter than this (ms) counts as a cancel tap, not speech |
| `keys.cancel` | *(blank)* | Optional dedicated cancel key (blank = cancel via a tap of the talk key) |

You can bind a joystick/HOTAS button to the talk key with a tool like JoyToKey.

## Microphone & audio device (`[audio]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `audio.input_device` | *(your mic)* | Microphone, matched by name (blank = Windows default) |
| `audio.sample_rate` | `16000` | Mic sample rate — leave as-is (what the speech model expects) |
| `audio.tts_output_device` | *(blank)* | Output device (blank = default speakers/headset) |
| `audio.enabled` | `false` | **Master switch** for the [ambient audio layer](#ambient-audio-audio-music) |

## Speech-to-text (`[whisper]`)

Local speech recognition (faster-whisper) — nothing leaves your machine.

| Setting | Default | What it does |
|---------|---------|--------------|
| `whisper.model` | `small` | Model size: `tiny`, `base`, `small`, `medium`, `large-v3` (bigger = more accurate, slower) |
| `whisper.device` | `cpu` | `cpu` (safe everywhere) or `cuda` (needs an NVIDIA GPU) |
| `whisper.compute_type` | `int8` | `int8` (fast/low memory on CPU), `float16`, or `float32` |
| `whisper.language` | `en` | Force a language code, or blank to auto-detect |

## Language model (`[anthropic]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `anthropic.model` | `claude-sonnet-5` | Model used when the router is off (or as the pinned tier) |
| `anthropic.max_tokens` | `1024` | Hard cap on reply length — replies are spoken, so short is good |
| `anthropic.cache_ttl` | `1h` | Prompt-cache lifetime: `1h` survives long gaps between turns; `5m` is cheaper when chatting steadily |
| `anthropic.thinking.default` | `Off` | Extended-thinking depth: `Off`, `Low`, `Medium`, `High`, `Extra`, `Max` |

## Cost router (`[router]`)

Routes each turn to the cheapest capable model, escalating only when a turn earns it. See
[cost tiering](getting-started/voice-loop.md#cost-tiering-cheap-by-default-smart-when-it-matters).

| Setting | Default | What it does |
|---------|---------|--------------|
| `router.enabled` | `true` | Turn the tiering router on |
| `router.default_model` | `claude-haiku-4-5` | The cheap workhorse tier |
| `router.escalate_model` | `claude-sonnet-5` | The escalation tier (depth/analysis, current data) |
| `router.premium_model` | `claude-opus-4-8` | The premium tier (explicit ask only) |
| `router.pin` | *(blank)* | Force every turn to `haiku`, `sonnet`, or `opus`; blank = let the rules decide |
| `router.full_breakdown_max_tokens` | `2048` | Raised reply cap for an explicit "full breakdown" turn |

The escalation phrases (`escalate_phrases`, `depth_phrases`, `web_phrases`, `premium_phrases`,
`full_breakdown_phrases`) are lists you can tune — the words that bump a turn to a higher tier or
raise the length cap.

## Web search (`[web_search]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `web_search.enabled` | `true` | Let Claude search the web when it needs current info |
| `web_search.max_uses` | `3` | Max searches per reply (each one inflates later turns too — keep it low) |

## Personality (`[personality]`)

See [Personas & voice](using/personas-voice.md).

| Setting | Default | What it does |
|---------|---------|--------------|
| `personality.enabled` | `true` | Compose the in-character system prompt (Base + Persona + Campaign) |
| `personality.persona` | `Classic` | The selected persona (voice/register) |
| `personality.presets_file` | `personalities/presets.md` | Shipped, committed presets (no personal data) |
| `personality.custom_dir` | `personalities/custom` | Where your saved custom personas live (git-ignored) |
| `personality.campaign_file` | `campaign.txt` | Your personal Commander facts (git-ignored) |

## Text-to-speech (`[elevenlabs]`, `[tts]`, `[piper]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `tts.provider` | `elevenlabs` | Which voice speaks: `elevenlabs` (cloud) or `piper` (local, free) |
| `elevenlabs.model` | `eleven_flash_v2_5` | ElevenLabs TTS model (flash = low latency) |
| `elevenlabs.voice_id` | *(Sarah)* | Which ElevenLabs voice speaks |
| `elevenlabs.speed` | `1.0` | Speaking speed, clamped to `1.0`–`1.2` |
| `elevenlabs.output_format` | `pcm_16000` | Audio format (low-latency, cancellable — change only if you know why) |
| `piper.model` | *(blank)* | Path to a local Piper `.onnx` voice (for `tts.provider = "piper"`) |
| `elevenlabs.api_key_file` | `ElevenLabsAPIKey.txt` | Where the ElevenLabs key is read from (git-ignored) |

## Conversation (`[conversation]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `conversation.max_turns` | `20` | How many recent turns of history to keep for follow-ups |

## Sound cues (`[sound_cues]`)

Local sounds played instantly at each stage — see [The voice loop](getting-started/voice-loop.md#sound-cues).
Each is a list (one picked at random). Cue types: `listening`, `processing`, `done`, `failed`.
Files live in the git-ignored `sounds/` folder; the app runs fine without them.

## Checklist (`[checklist]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `checklist.file` | `ultimate_checklist.md` | Your checklist markdown file (git-ignored) |

## Elite Dangerous (`[elite]`)

Game-state monitoring — see [Game-state monitoring](elite/monitoring.md). **Enable this first**;
most Elite features depend on it.

| Setting | Default | What it does |
|---------|---------|--------------|
| `elite.enabled` | `true` | Tail the journal + `Status.json` for live context |
| `elite.journal_dir` | *(blank)* | Journal location (blank = the standard Saved Games path) |
| `elite.journal_poll_interval` | `0.5` | How often (s) to re-scan the journal for new lines |
| `elite.status_poll_interval` | `1.0` | How often (s) to poll `Status.json` for flag changes |
| `elite.recent_events_kept` | `25` | How many recent events feed "what just happened" |

The `context_wake`, `status_phrases`, and `log_phrases` lists control which spoken turns pull in
live telemetry (and the "context" wake word) — tune them from your own transcripts.

## Proactive callouts (`[proactive]`)

See [Proactive callouts](elite/proactive-callouts.md).

| Setting | Default | What it does |
|---------|---------|--------------|
| `proactive.enabled` | `true` | Let the companion volunteer short lines on notable events |
| `proactive.min_interval` | `20` | Minimum seconds between any two callouts |
| `proactive.cooldown` | `120` | Seconds before the same event type may re-announce |
| `proactive.max_tokens` | `120` | Reply length cap for a callout |
| `[proactive.events]` | *(see file)* | Per-event whitelist (`FSDJump`, `Docked`, `MissionCompleted`, `LowFuel`, `Overheating`, `Died`) |

## Route callouts (`[route]`)

See [Route callouts](elite/route-callouts.md). **Off by default.**

| Setting | Default | What it does |
|---------|---------|--------------|
| `route.enabled` | `false` | Heads-ups while flying a plotted route |
| `route.every_n` | `5` | Announce jumps-remaining every Nth jump |
| `route.callout_scoopable` | `true` | Announce whether the next star is scoopable |
| `route.callout_jumps_remaining` | `true` | Announce jumps remaining |
| `route.callout_arrival` | `true` | Announce arrival at the destination |

## Keybind automation (`[keybinds]`)

See [Keybind automation](automation/keybinds.md). **Off by default** — it sends real keypresses.

| Setting | Default | What it does |
|---------|---------|--------------|
| `keybinds.enabled` | `false` | Master switch |
| `keybinds.require_confirmation` | `true` | Require a separate spoken confirm before firing (leave on) |
| `keybinds.combat_guard` | `true` | Refuse during danger/interdiction or unknown status (leave on) |
| `keybinds.confirm_window` | `60` | Seconds an armed action stays confirmable |
| `keybinds.binds_file` | *(blank)* | Override the auto-detected bindings file |
| `keybinds.allowlist` | `["landing_gear"]` | The only macros the companion may run |

## Auto-honk (`[honk]`)

See [Auto-honk](automation/auto-honk.md). **Off by default** — it presses a fire button.

| Setting | Default | What it does |
|---------|---------|--------------|
| `honk.enabled` | `false` | Master switch |
| `honk.fire_group` | `-1` | Scanner's fire group (0-based); `-1` = don't cycle, just hold primary fire |
| `honk.trigger` | `primary` | Which fire button the scanner is on |
| `honk.hold_seconds` | `6.0` | How long to hold the fire button |
| `honk.combat_guard` | `true` | Refuse during danger/interdiction or unknown status (leave on) |

## Navigation & search (`[nav]`, `[star_systems]`, `[search]`)

The [voice search](search/index.md) categories.

| Setting | Default | What it does |
|---------|---------|--------------|
| `nav.enabled` | `true` | Outfitting **and** ship search |
| `nav.default_pad_size` | `L` | Default landing-pad size your ship needs (`S`/`M`/`L`/`any`) |
| `nav.search_size` | `50` | How many nearby stations to fetch before filtering |
| `nav.verify_stock` | `true` | (Ship search) verify current stock against EDSM before answering |
| `nav.require_confirmation` | `false` | Gate the (read-only) search behind a separate confirm turn |
| `star_systems.enabled` | `true` | Star-system search |
| `star_systems.search_size` | `50` | How many nearby matching systems to fetch |
| `search.enabled` | `true` | Station / faction / signal / state searches |
| `search.search_size` | `50` | How many nearby matches to fetch |

The `[nav]`, `[star_systems]`, and `[search]` sections also carry `base_url` / `user_agent` values
for the Spansh API — you rarely need to touch these.

## Community goals (`[cg]`)

See [Community goals](elite/community-goals.md).

| Setting | Default | What it does |
|---------|---------|--------------|
| `cg.source` | `inara` | `inara` (external feed) or `none` (journal-only) |
| `cg.inara_api_key` | *(blank)* | Free Inara key to also see CGs you haven't visited (not on the Settings page — it's a credential) |

## Ambient audio (`[audio]`, `[music]`)

The optional [atmospheric audio layer](audio/ambient-audio.md). **All off by default.**

| Setting | Default | What it does |
|---------|---------|--------------|
| `audio.enabled` | `false` | Master switch (restart to apply) |
| `audio.cues.enabled` | `false` | Space chatter & SFX |
| `audio.comms.enabled` | `true` | Comms voices (within the layer) |
| `music.enabled` | `false` | Ambient music (needs local track files) |
| `audio.interdiction.enabled` | `false` | The layered pirate-interdiction cue |
| `audio.buses.*.volume_db` | *(varies)* | Per-bus volume trims (COVAS, comms, ambient, music, alert) |
| `audio.voices.cast_provider` | `piper` | TTS for the NPC/comms cast: `piper` (local, free) or `elevenlabs` |
| `audio.comms.voices.{male,female,default}` | *(blank)* | ElevenLabs voices for comms lines |

The comms radio treatment (band limits, static, compression), the SFX/music track lists, and the
voice-cast pool live in the same sections — see the comments in `config.toml`.

## Control panel (`[ui]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `ui.host` | `127.0.0.1` | Interface the control panel binds to (restart to apply) |
| `ui.port` | `8765` | Port the control panel serves on (restart to apply) |

## Providers & developer (`[llm]`, `[tts]`, `[dev]`)

| Setting | Default | What it does |
|---------|---------|--------------|
| `llm.provider` | `anthropic` | `anthropic` (cloud) or `ollama` (local, out-of-game only) |
| `tts.provider` | `elevenlabs` | `elevenlabs` (cloud) or `piper` (local, free) |
| `dev.mock` | `false` | Swap LLM/TTS/STT for fakes — exercise the loop with zero API calls (restart to apply) |
