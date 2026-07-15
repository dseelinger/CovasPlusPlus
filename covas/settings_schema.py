"""Single source of truth for every user-facing setting (Prompt N1).

Both the web settings page (`web.py` + `templates/settings.html`) and the voice
settings layer (Prompt N2) project from THIS schema, so the two can never drift:
add a setting here once and both surfaces gain it. Each `Setting` declares

  * where it lives in `config.toml` (`path` into the nested config dict),
  * its `type` + constraints (`options` / `min` / `max`) for validation,
  * display metadata (`label`, `group`, `help`, `unit`) for the web page, and
  * `phrasings` + `example` for the spoken layer.

Defaults mirror `config.toml` (a unit test asserts they stay in lock-step, so a
value changed in one place fails loudly until the other matches). This module is
PURE — it never reads config or the network; callers pass values in. That keeps
`pytest` offline and lets the same validator serve web POSTs and voice commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# --- static option vocabularies (shared with the UI) -----------------------
# English-only ".en" variants sit beside the multilingual sizes: same size, more accurate for
# an English companion. small.en is the shipped default the first-run wizard installs.
WHISPER_SIZES = ["tiny", "tiny.en", "base", "base.en", "small", "small.en",
                 "medium", "medium.en", "large-v3"]
WHISPER_DEVICES = ["cpu", "cuda"]
WHISPER_COMPUTE = ["int8", "float16", "float32"]
THINKING_TIERS = ["Off", "Low", "Medium", "High", "Extra", "Max"]
CACHE_TTLS = ["5m", "1h"]
# Canonical tiers (issue #11) plus the Anthropic-flavored aliases the router still accepts.
ROUTER_PINS = ["", "cheap", "standard", "premium", "haiku", "sonnet", "opus"]
PAD_SIZES = ["S", "M", "L", "any"]
EL_FORMATS = ["pcm_16000", "pcm_22050", "pcm_24000", "mp3_44100_128"]
LLM_PROVIDERS = ["anthropic", "openai", "gemini", "ollama"]
# cartesia is PERSONA-only (a premium low-latency persona voice, #18) — it is intentionally NOT in
# CAST_PROVIDERS (not offered for the NPC/comms/chatter cast).
TTS_PROVIDERS = ["elevenlabs", "piper", "edge", "azure", "openai", "cartesia"]
CAST_PROVIDERS = ["piper", "elevenlabs", "edge", "azure", "openai"]

# Sentinels for enum options that can only be resolved at runtime (from config
# or a live API). The web/voice layer supplies the concrete list; when it can't
# (offline), validation falls back to a plain type check rather than guessing.
OPT_MODELS = "@anthropic_models"        # cfg[anthropic][available_models]
OPT_EL_MODELS = "@elevenlabs_models"    # live ElevenLabs model ids
OPT_EL_VOICES = "@elevenlabs_voices"    # live ElevenLabs voice ids


@dataclass(frozen=True)
class Setting:
    """One user-facing setting, declared once for every surface."""
    key: str                          # unique dotted id ("anthropic.model")
    path: tuple                       # location in the config dict
    type: str                         # bool | int | float | enum | string | path
    label: str                        # short display name
    group: str                        # UI section
    help: str                         # one-line inline explanation
    default: Any                      # mirrors config.toml (drift-tested)
    options: Optional[list] = None    # static enum options
    options_source: Optional[str] = None  # dynamic enum options (sentinel above)
    min: Optional[float] = None       # numeric lower bound (inclusive)
    max: Optional[float] = None       # numeric upper bound (inclusive)
    unit: str = ""                    # display suffix (ms, s, tokens)
    phrasings: tuple = ()             # spoken names for the voice layer
    example: str = ""                 # example spoken command
    hidden: bool = False              # tracked + settable, but not shown as a row


# The schema. Order here is the order groups first appear in the web page.
SCHEMA: list[Setting] = [
    # --- Voice input -------------------------------------------------------
    Setting("keys.push_to_talk", ("keys", "push_to_talk"), "string",
            "Push-to-talk key", "Voice input",
            "Keyboard key you HOLD to speak; a brief tap of it cancels.",
            default="[", phrasings=("push to talk key", "talk key"),
            example="set the talk key to right control"),
    Setting("keys.tap_cancel_ms", ("keys", "tap_cancel_ms"), "int",
            "Tap-cancel threshold", "Voice input",
            "A press shorter than this counts as a cancel tap, not speech.",
            default=400, min=50, max=2000, unit="ms",
            phrasings=("tap cancel time", "cancel tap threshold"),
            example="set the tap cancel time to 300 milliseconds"),
    Setting("keys.cancel", ("keys", "cancel"), "string",
            "Separate cancel key", "Voice input",
            "Optional dedicated cancel key. Blank = cancel via a tap of the talk key.",
            default="", phrasings=("cancel key",),
            example="set the cancel key to escape"),

    # --- Speech-to-text ----------------------------------------------------
    Setting("whisper.model", ("whisper", "model"), "enum",
            "Whisper model", "Speech-to-text",
            "Local STT model. Bigger = more accurate but slower.",
            default="small", options=WHISPER_SIZES,
            phrasings=("whisper model", "transcription model", "speech model"),
            example="set the whisper model to medium"),
    Setting("whisper.device", ("whisper", "device"), "enum",
            "Whisper device", "Speech-to-text",
            "cpu is safe everywhere; cuda needs an NVIDIA GPU.",
            default="cpu", options=WHISPER_DEVICES,
            phrasings=("whisper device",),
            example="set the whisper device to cpu"),
    Setting("whisper.compute_type", ("whisper", "compute_type"), "enum",
            "Whisper compute type", "Speech-to-text",
            "int8 = fast + low memory on CPU; float16 for cuda.",
            default="int8", options=WHISPER_COMPUTE,
            phrasings=("whisper compute type",)),
    Setting("whisper.language", ("whisper", "language"), "string",
            "Whisper language", "Speech-to-text",
            "Force a language code (en), or blank to auto-detect.",
            default="en", phrasings=("whisper language", "transcription language"),
            example="set the whisper language to auto"),

    # --- Language model ----------------------------------------------------
    Setting("anthropic.model", ("anthropic", "model"), "enum",
            "Claude model", "Language model",
            "Model used when the cost router is OFF (or as the pinned tier).",
            default="claude-sonnet-5", options_source=OPT_MODELS,
            phrasings=("claude model", "language model", "the model"),
            example="switch to opus"),
    Setting("anthropic.max_tokens", ("anthropic", "max_tokens"), "int",
            "Max reply tokens", "Language model",
            "Hard cap on reply length. Replies are spoken, so short is good.",
            default=1024, min=128, max=8192, unit="tokens",
            phrasings=("max tokens", "reply length cap"),
            example="set max tokens to 2000"),
    Setting("anthropic.cache_ttl", ("anthropic", "cache_ttl"), "enum",
            "Prompt cache lifetime", "Language model",
            "1h survives long gaps between voice turns; 5m is cheaper when chatting steadily.",
            default="1h", options=CACHE_TTLS,
            phrasings=("cache lifetime", "prompt cache")),
    Setting("anthropic.thinking.default", ("anthropic", "thinking", "default"), "enum",
            "Thinking depth", "Language model",
            "Extended-thinking tier. Off is the cheap, fast default.",
            default="Off", options=THINKING_TIERS,
            phrasings=("thinking", "thinking depth", "reasoning effort"),
            example="set thinking to high"),

    # --- Cost router -------------------------------------------------------
    Setting("router.enabled", ("router", "enabled"), "bool",
            "Cost router", "Cost router",
            "Route each turn to the cheapest capable model, escalating only when earned.",
            default=True, phrasings=("cost router", "the router"),
            example="turn the router on"),
    Setting("router.pin", ("router", "pin"), "enum",
            "Force a tier", "Cost router",
            "Pin every turn to one tier: cheap/standard/premium (aliases haiku/sonnet/opus). "
            "Blank = let the rules decide.",
            default="", options=ROUTER_PINS,
            phrasings=("tier pin", "force a tier", "pin the tier"),
            example="pin the tier to standard"),
    Setting("router.full_breakdown_max_tokens", ("router", "full_breakdown_max_tokens"), "int",
            "Full-breakdown tokens", "Cost router",
            "Raised reply cap for an explicit 'full breakdown' request.",
            default=2048, min=256, max=8192, unit="tokens",
            phrasings=("full breakdown tokens",)),

    # --- Web search --------------------------------------------------------
    Setting("web_search.enabled", ("web_search", "enabled"), "bool",
            "Web search", "Web search",
            "Let Claude search the web when it needs current info.",
            default=True, phrasings=("web search", "internet search"),
            example="turn web search off"),
    Setting("web_search.max_uses", ("web_search", "max_uses"), "int",
            "Max searches per reply", "Web search",
            "Each search inflates the cost of every later turn too — keep it low.",
            default=3, min=0, max=10, unit="searches",
            phrasings=("max searches", "web search limit")),

    # --- Personality -------------------------------------------------------
    Setting("personality.enabled", ("personality", "enabled"), "bool",
            "Personality", "Personality",
            "Load personality.txt as the system prompt (Commander address + campaign context).",
            default=True, phrasings=("personality", "character"),
            example="turn personality off"),

    # --- Text-to-speech ----------------------------------------------------
    Setting("elevenlabs.model", ("elevenlabs", "model"), "enum",
            "ElevenLabs model", "Text-to-speech",
            "TTS model. flash is the low-latency default.",
            default="eleven_flash_v2_5", options_source=OPT_EL_MODELS,
            phrasings=("voice model", "tts model", "elevenlabs model")),
    Setting("elevenlabs.voice_id", ("elevenlabs", "voice_id"), "enum",
            "ElevenLabs voice", "Text-to-speech",
            "Which voice speaks. Pick from your ElevenLabs library.",
            default="EXAVITQu4vr4xnSDxMaL", options_source=OPT_EL_VOICES,
            phrasings=("voice", "the voice", "tts voice"),
            example="use the George voice"),
    Setting("elevenlabs.voice_name", ("elevenlabs", "voice_name"), "string",
            "ElevenLabs voice name", "Text-to-speech",
            "Display name paired with the selected voice id.",
            default="Sarah", hidden=True),
    Setting("elevenlabs.speed", ("elevenlabs", "speed"), "float",
            "Voice speed", "Text-to-speech",
            "How fast COVAS speaks (ElevenLabs native speed). 1.0 = normal.",
            default=1.0, min=1.0, max=1.2, unit="×",
            phrasings=("voice speed", "speaking speed", "talk speed"),
            example="set the voice speed to 1.1"),
    Setting("elevenlabs.output_format", ("elevenlabs", "output_format"), "enum",
            "Audio format", "Text-to-speech",
            "pcm_16000 = low-latency, cancellable. Change only if you know why.",
            default="pcm_16000", options=EL_FORMATS,
            phrasings=("audio format", "output format")),

    # --- Conversation ------------------------------------------------------
    Setting("conversation.max_turns", ("conversation", "max_turns"), "int",
            "History turns kept", "Conversation",
            "Rolling in-session history so follow-ups work; older turns are trimmed.",
            default=20, min=2, max=100, unit="turns",
            phrasings=("history turns", "conversation memory", "how many turns")),

    # --- Elite Dangerous ---------------------------------------------------
    Setting("elite.enabled", ("elite", "enabled"), "bool",
            "ED game-state monitoring", "Elite Dangerous",
            "Tail ED's journal + Status.json for live context (where am I, ship status).",
            default=True, phrasings=("game monitoring", "elite monitoring", "game state"),
            example="turn game monitoring on"),
    Setting("elite.journal_dir", ("elite", "journal_dir"), "path",
            "Journal directory", "Elite Dangerous",
            "Blank = the standard Saved Games location. Set only if yours differs.",
            default="", phrasings=("journal directory", "journal folder")),
    Setting("elite.journal_poll_interval", ("elite", "journal_poll_interval"), "float",
            "Journal poll interval", "Elite Dangerous",
            "How often to re-scan the journal for new lines.",
            default=0.5, min=0.1, max=10.0, unit="s",
            phrasings=("journal poll interval",)),
    Setting("elite.status_poll_interval", ("elite", "status_poll_interval"), "float",
            "Status poll interval", "Elite Dangerous",
            "How often to poll Status.json for flag transitions.",
            default=1.0, min=0.1, max=10.0, unit="s",
            phrasings=("status poll interval",)),
    Setting("elite.recent_events_kept", ("elite", "recent_events_kept"), "int",
            "Recent events kept", "Elite Dangerous",
            "How many recent notable events feed 'what just happened'.",
            default=25, min=1, max=200, unit="events",
            phrasings=("recent events kept", "event history")),

    # --- Proactive callouts ------------------------------------------------
    Setting("proactive.enabled", ("proactive", "enabled"), "bool",
            "Proactive callouts", "Proactive callouts",
            "Let the companion initiate short lines on notable ED events. Needs ED monitoring.",
            default=True, phrasings=("proactive callouts", "callouts"),
            example="turn callouts off"),
    Setting("proactive.min_interval", ("proactive", "min_interval"), "int",
            "Min interval between callouts", "Proactive callouts",
            "No two callouts fire within this many seconds.",
            default=20, min=0, max=600, unit="s",
            phrasings=("callout interval", "minimum callout interval")),
    Setting("proactive.cooldown", ("proactive", "cooldown"), "int",
            "Same-event cooldown", "Proactive callouts",
            "The same event type won't re-announce within this many seconds.",
            default=120, min=0, max=3600, unit="s",
            phrasings=("callout cooldown",)),
    Setting("proactive.max_tokens", ("proactive", "max_tokens"), "int",
            "Callout token cap", "Proactive callouts",
            "A callout is one sentence — keep it tight and cheap.",
            default=120, min=32, max=512, unit="tokens",
            phrasings=("callout length",)),

    # --- Route callouts ----------------------------------------------------
    Setting("route.enabled", ("route", "enabled"), "bool",
            "Route callouts", "Route callouts",
            "Proactive heads-ups while flying a plotted route (scoopable star, jumps "
            "remaining, arrival). Needs ED monitoring.",
            default=True, phrasings=("route callouts", "jump callouts"),
            example="turn route callouts on"),
    Setting("route.every_n", ("route", "every_n"), "int",
            "Jumps-remaining cadence", "Route callouts",
            "Announce jumps remaining every Nth jump. Lower is chattier.",
            default=5, min=1, max=50, unit="jumps",
            phrasings=("route callout cadence", "jumps remaining cadence")),
    Setting("route.callout_scoopable", ("route", "callout_scoopable"), "bool",
            "Announce scoopable star", "Route callouts",
            "Call out whether the next star can be fuel-scooped as you lock each jump.",
            default=True, phrasings=("scoopable callouts", "scoopable star callout")),
    Setting("route.callout_jumps_remaining", ("route", "callout_jumps_remaining"), "bool",
            "Announce jumps remaining", "Route callouts",
            "Call out jumps remaining to the destination, every Nth jump.",
            default=True, phrasings=("jumps remaining callouts",)),
    Setting("route.callout_arrival", ("route", "callout_arrival"), "bool",
            "Announce arrival", "Route callouts",
            "Call out arrival at the final system when the route completes.",
            default=True, phrasings=("arrival callouts",)),

    # --- Keybinds ----------------------------------------------------------
    Setting("keybinds.enabled", ("keybinds", "enabled"), "bool",
            "Keybind automation", "Keybinds",
            "Let the companion press ONE ship control (landing gear) behind a safety layer.",
            default=True, phrasings=("keybind automation", "ship controls"),
            example="turn keybind automation on"),
    Setting("keybinds.require_confirmation", ("keybinds", "require_confirmation"), "bool",
            "Require confirmation", "Keybinds",
            "Arming an action needs a SEPARATE spoken confirm before it fires. Leave ON.",
            default=True, phrasings=("keybind confirmation",)),
    Setting("keybinds.combat_guard", ("keybinds", "combat_guard"), "bool",
            "Combat guard", "Keybinds",
            "Refuse to touch controls during danger/interdiction (or unknown status). Leave ON.",
            default=True, phrasings=("combat guard",)),
    Setting("keybinds.confirm_window", ("keybinds", "confirm_window"), "int",
            "Confirm window", "Keybinds",
            "Seconds an armed action stays confirmable before it expires.",
            default=60, min=5, max=300, unit="s",
            phrasings=("confirm window",)),

    # --- Auto-honk ---------------------------------------------------------
    Setting("honk.enabled", ("honk", "enabled"), "bool",
            "Auto-honk", "Auto-honk",
            "Fire the Discovery Scanner automatically on arrival in a new system. "
            "Needs ED monitoring; combat-gated.",
            default=True, phrasings=("auto honk", "auto discovery scan", "honk"),
            example="turn auto honk on"),
    Setting("honk.trigger", ("honk", "trigger"), "enum",
            "Scanner fire button", "Auto-honk",
            "Which fire button the Discovery Scanner is on.",
            default="primary", options=["primary", "secondary"],
            phrasings=("scanner trigger", "honk trigger")),
    Setting("honk.hold_seconds", ("honk", "hold_seconds"), "float",
            "Honk hold time", "Auto-honk",
            "How long to hold the fire button to complete the scan.",
            default=5.0, min=1.0, max=10.0, unit="s",
            phrasings=("honk hold time", "honk duration")),
    Setting("honk.combat_guard", ("honk", "combat_guard"), "bool",
            "Combat guard", "Auto-honk",
            "Refuse to honk during danger/interdiction (or unknown status). Leave ON.",
            default=True, phrasings=("honk combat guard",)),

    # --- Navigation & search ----------------------------------------------
    Setting("nav.enabled", ("nav", "enabled"), "bool",
            "Find closest module", "Navigation & search",
            "Voice: 'find the closest station that sells module X'.",
            default=True, phrasings=("find closest module", "module search")),
    Setting("nav.default_pad_size", ("nav", "default_pad_size"), "enum",
            "Default landing pad", "Navigation & search",
            "Pad size your ship needs; a voice request can override per search.",
            default="L", options=PAD_SIZES,
            phrasings=("landing pad size", "pad size"),
            example="set the pad size to medium"),
    Setting("nav.search_size", ("nav", "search_size"), "int",
            "Module search size", "Navigation & search",
            "How many nearest stations to fetch before filtering to the closest match.",
            default=50, min=1, max=500, unit="results",
            phrasings=("module search size",)),
    Setting("nav.require_confirmation", ("nav", "require_confirmation"), "bool",
            "Confirm before module search", "Navigation & search",
            "Gate the (read-only) search behind a separate confirm turn.",
            default=False, phrasings=("module search confirmation",)),
    Setting("star_systems.enabled", ("star_systems", "enabled"), "bool",
            "Star-system search", "Navigation & search",
            "Voice: 'find the nearest Empire system with high security'.",
            default=True, phrasings=("system search", "star system search")),
    Setting("star_systems.search_size", ("star_systems", "search_size"), "int",
            "System search size", "Navigation & search",
            "How many nearest matching systems to fetch; the closest is the answer.",
            default=50, min=1, max=500, unit="results",
            phrasings=("system search size",)),
    Setting("search.enabled", ("search", "enabled"), "bool",
            "Station/faction/signal search", "Navigation & search",
            "The remaining voice search categories (stations, factions, signals, states).",
            default=True, phrasings=("station search", "faction search", "signal search")),
    Setting("search.search_size", ("search", "search_size"), "int",
            "Category search size", "Navigation & search",
            "How many nearest matches to fetch; the closest is the answer.",
            default=50, min=1, max=500, unit="results",
            phrasings=("category search size",)),
    Setting("route_plan.enabled", ("route_plan", "enabled"), "bool",
            "Trade-route planner", "Navigation & search",
            "Voice: 'plan me a trade route from here'. Plans a Spansh trade loop and copies the "
            "next stop to your clipboard for the galaxy map.",
            default=False, phrasings=("trade route planner", "route planner")),
    Setting("route_plan.default_max_hops", ("route_plan", "default_max_hops"), "int",
            "Trade route hops", "Navigation & search",
            "Default number of hops when planning a trade loop (if you don't say).",
            default=4, min=1, max=20, unit="hops",
            phrasings=("trade route hops",)),
    Setting("neutron_plan.enabled", ("neutron_plan", "enabled"), "bool",
            "Neutron-route planner", "Navigation & search",
            "Voice: 'plot a neutron route to Colonia'. Plots a long-range neutron-highway route to "
            "a distant system and copies the first waypoint to your clipboard for the galaxy map.",
            default=False, phrasings=("neutron route planner", "long range route planner",
                                      "galaxy route planner")),
    Setting("neutron_plan.default_efficiency", ("neutron_plan", "default_efficiency"), "int",
            "Neutron route efficiency", "Navigation & search",
            "Default Spansh efficiency (1-100) when you don't say — higher trades longer neutron "
            "detours for fewer total jumps.",
            default=60, min=1, max=100, unit="%",
            phrasings=("neutron route efficiency",)),

    # --- Providers ---------------------------------------------------------
    Setting("llm.provider", ("llm", "provider"), "enum",
            "LLM provider", "Providers",
            "Which LLM answers. anthropic (cloud, Claude), openai (any OpenAI-compatible cloud: "
            "OpenAI/Groq/DeepSeek/OpenRouter), gemini (Google Gemini native — function calling + "
            "Search grounding), or ollama (local, out-of-game only).",
            default="anthropic", options=LLM_PROVIDERS,
            phrasings=("llm provider",)),
    Setting("openai.base_url", ("openai", "base_url"), "string",
            "OpenAI LLM base URL", "Providers",
            "OpenAI-compatible chat/completions endpoint when LLM provider = openai. Default is "
            "OpenAI; point it at Groq/DeepSeek/OpenRouter. Key comes from the key file.",
            default="https://api.openai.com/v1", phrasings=("openai llm base url",)),
    Setting("openai.model", ("openai", "model"), "string",
            "OpenAI LLM model", "Providers",
            "Model when LLM provider = openai and the router is off/unset, e.g. gpt-4o-mini. Per-tier "
            "models live in [openai.tiers] in config.toml.",
            default="gpt-4o-mini", phrasings=("openai llm model", "openai model")),
    Setting("gemini.model", ("gemini", "model"), "string",
            "Gemini model", "Providers",
            "Model when LLM provider = gemini and the router is off/unset, e.g. gemini-2.5-flash. "
            "Per-tier models (Flash/Pro) live in [gemini.tiers] in config.toml.",
            default="gemini-2.5-flash", phrasings=("gemini model",)),
    Setting("tts.provider", ("tts", "provider"), "enum",
            "TTS provider", "Providers",
            "Which voice speaks. edge (free edge-tts neural voices — the default; no SLA, falls "
            "back to piper), azure (official Azure Neural, free tier + SLA), openai (cheap cloud, "
            "OpenAI-compatible), cartesia (low-latency premium persona), elevenlabs (cloud, "
            "premium), or piper (local, offline, free).",
            default="edge", options=TTS_PROVIDERS,
            phrasings=("tts provider", "voice provider")),
    Setting("edge.voice", ("edge", "voice"), "string",
            "Edge voice", "Providers",
            "Edge (edge-tts) persona voice ShortName when TTS provider = edge, e.g. "
            "en-US-AriaNeural. List them with: python -m edge_tts --list-voices.",
            default="en-US-AriaNeural", phrasings=("edge voice", "edge tts voice")),
    Setting("azure.region", ("azure", "region"), "string",
            "Azure region", "Providers",
            "Azure Speech resource region when TTS provider = azure, e.g. eastus, westus2, uksouth. "
            "Must match your resource. Key comes from [azure].api_key_file.",
            default="eastus", phrasings=("azure region", "azure speech region")),
    Setting("azure.voice", ("azure", "voice"), "string",
            "Azure voice", "Providers",
            "Azure Neural persona voice ShortName when TTS provider = azure (same names as Edge), "
            "e.g. en-US-AriaNeural.",
            default="en-US-AriaNeural", phrasings=("azure voice", "azure tts voice")),
    Setting("azure.style", ("azure", "style"), "string",
            "Azure speaking style", "Providers",
            "Optional SSML speaking style/emotion for the Azure voice (voice-dependent), e.g. "
            "cheerful, newscast, chat. Blank = the voice's neutral default.",
            default="", phrasings=("azure style", "azure speaking style", "voice style")),
    Setting("openai_tts.base_url", ("openai_tts", "base_url"), "string",
            "OpenAI TTS base URL", "Providers",
            "OpenAI-compatible audio/speech endpoint when TTS provider = openai. Default is OpenAI; "
            "point it at any compatible endpoint. Key comes from the key file.",
            default="https://api.openai.com/v1", phrasings=("openai base url", "openai tts url")),
    Setting("openai_tts.model", ("openai_tts", "model"), "string",
            "OpenAI TTS model", "Providers",
            "TTS model when TTS provider = openai. gpt-4o-mini-tts (cheap, default) or tts-1.",
            default="gpt-4o-mini-tts", phrasings=("openai tts model", "openai voice model")),
    Setting("openai_tts.voice", ("openai_tts", "voice"), "string",
            "OpenAI TTS voice", "Providers",
            "Voice name when TTS provider = openai: alloy, ash, ballad, coral, echo, fable, nova, "
            "onyx, sage, shimmer, verse.",
            default="alloy", phrasings=("openai voice", "openai tts voice")),
    Setting("openai_tts.instructions", ("openai_tts", "instructions"), "string",
            "OpenAI TTS instructions", "Providers",
            "Optional free-text tone/delivery steer, honored by newer models (gpt-4o-mini-tts), "
            "ignored by older (tts-1). Blank = the voice's default.",
            default="", phrasings=("openai instructions", "openai tone")),
    Setting("cartesia.model", ("cartesia", "model"), "string",
            "Cartesia model", "Providers",
            "Cartesia Sonic model when TTS provider = cartesia (a low-latency premium PERSONA "
            "voice), e.g. sonic-2. Key comes from the key file.",
            default="sonic-2", phrasings=("cartesia model", "sonic model")),
    Setting("cartesia.voice", ("cartesia", "voice"), "string",
            "Cartesia voice id", "Providers",
            "Cartesia voice id when TTS provider = cartesia (required — get one from the Cartesia "
            "voice library at play.cartesia.ai or GET /voices).",
            default="", phrasings=("cartesia voice", "sonic voice")),
    Setting("cartesia.language", ("cartesia", "language"), "string",
            "Cartesia language", "Providers",
            "Synthesis language (BCP-47 primary subtag) for the Cartesia voice, e.g. en.",
            default="en", phrasings=("cartesia language",)),

    # --- Control panel -----------------------------------------------------
    Setting("ui.host", ("ui", "host"), "string",
            "Control panel host", "Control panel",
            "Interface the local control panel binds to. Restart to apply.",
            default="127.0.0.1", phrasings=("control panel host",)),
    Setting("ui.port", ("ui", "port"), "int",
            "Control panel port", "Control panel",
            "Port the local control panel serves on. Restart to apply.",
            default=8765, min=1, max=65535, phrasings=("control panel port",)),

    # --- Sound cues --------------------------------------------------------
    Setting("audio.thinking_bed", ("audio", "thinking_bed"), "bool",
            "Thinking sound", "Sound cues",
            "Soft looping sound that fills the wait while COVAS transcribes/thinks/searches, so a "
            "slow turn doesn't feel ignored. Off = just the single processing tick. Stops when the "
            "reply starts or you cancel.",
            default=True, phrasings=("thinking sound", "working sound", "thinking bed",
                                     "the thinking cue"),
            example="turn the thinking sound off"),

    # --- Ambient audio (C1-C9) --------------------------------------------
    # The atmospheric audio layer: a shared bus mixer + space chatter, comms voices, and music.
    # The master switch is restart-level (it builds the mixer at launch); the rest apply live.
    Setting("audio.enabled", ("audio", "enabled"), "bool",
            "Audio layer", "Ambient audio",
            "Master switch for the atmospheric audio layer (bus mixer + chatter/comms/music). "
            "Restart to apply — it changes how the audio device is opened.",
            default=True, phrasings=("audio layer", "ambient audio", "the audio layer"),
            example="turn the audio layer on"),
    Setting("audio.cues.enabled", ("audio", "cues", "enabled"), "bool",
            "Space chatter & SFX", "Ambient audio",
            "Context-driven ambient chatter and SFX cues (needs the audio layer + ED monitoring).",
            default=True, phrasings=("space chatter", "the chatter", "ambient chatter"),
            example="turn the space chatter on"),
    Setting("audio.comms.enabled", ("audio", "comms", "enabled"), "bool",
            "Comms voices", "Ambient audio",
            "Voice ED comms panel lines (NPC/station + direct player DMs) on the radio bus.",
            default=True, phrasings=("comms voices", "the comms", "radio comms"),
            example="turn the comms voices off"),
    Setting("music.enabled", ("music", "enabled"), "bool",
            "Ambient music", "Ambient audio",
            "Context-crossfaded ambient music (needs local track files in [music.tracks]).",
            default=True, phrasings=("ambient music", "the music", "background music"),
            example="turn the music on"),
    Setting("audio.interdiction.enabled", ("audio", "interdiction", "enabled"), "bool",
            "Interdiction cue", "Ambient audio",
            "The layered pirate-interdiction moment (warning sting + threat line + pirate line).",
            default=True, phrasings=("interdiction cue", "the interdiction alert"),
            example="turn the interdiction cue on"),
    Setting("audio.buses.covas.volume_db", ("audio", "buses", "covas", "volume_db"), "float",
            "COVAS volume", "Ambient audio",
            "Level of your assistant's own voice on the clean bus (decibels).",
            default=0.0, min=-40.0, max=6.0, unit="dB",
            phrasings=("covas volume", "assistant volume")),
    Setting("audio.buses.comms.volume_db", ("audio", "buses", "comms", "volume_db"), "float",
            "Comms volume", "Ambient audio",
            "Level of the radio comms bus (decibels).",
            default=-3.0, min=-40.0, max=6.0, unit="dB",
            phrasings=("comms volume", "radio volume")),
    Setting("audio.buses.ambient.volume_db", ("audio", "buses", "ambient", "volume_db"), "float",
            "Ambient/SFX volume", "Ambient audio",
            "Level of the ambient SFX bus (decibels).",
            default=-6.0, min=-40.0, max=6.0, unit="dB",
            phrasings=("ambient volume", "sfx volume")),
    Setting("audio.buses.music.volume_db", ("audio", "buses", "music", "volume_db"), "float",
            "Music volume", "Ambient audio",
            "Level of the music bus (decibels).",
            default=-12.0, min=-40.0, max=6.0, unit="dB",
            phrasings=("music volume", "the music level"),
            example="turn the music volume up"),
    Setting("audio.buses.alert.volume_db", ("audio", "buses", "alert", "volume_db"), "float",
            "Alert volume", "Ambient audio",
            "Level of the alert/stinger bus (decibels).",
            default=0.0, min=-40.0, max=6.0, unit="dB",
            phrasings=("alert volume", "sting volume")),
    Setting("audio.voices.cast_provider", ("audio", "voices", "cast_provider"), "enum",
            "Cast provider", "Ambient audio",
            "Default TTS for the NPC/comms/chatter voice cast: 'elevenlabs' (random voices, burns "
            "credits), 'piper' (local, free), 'edge' (free edge-tts neural voices — no key, no SLA), "
            "'azure' (official Azure Neural, free tier + SLA), or 'openai' (cheap cloud). Per-role "
            "overrides live in [audio.voices.providers]. COVAS uses your persona ([tts].provider).",
            default="elevenlabs", options=CAST_PROVIDERS,
            phrasings=("cast provider", "voice cast provider", "npc voice provider"),
            example="set the cast provider to piper"),
    Setting("audio.voices.random_el", ("audio", "voices", "random_el"), "bool",
            "Random ElevenLabs voices", "Ambient audio",
            "When no voice pool is configured, cast comms/chatter from RANDOM voices in your "
            "ElevenLabs library (minus the COVAS voice). Off = a single voice unless you set a pool.",
            default=True, phrasings=("random voices", "random elevenlabs voices", "random cast"),
            example="turn random voices off"),
    Setting("audio.voices.player_ref", ("audio", "voices", "player_ref"), "string",
            "Player-DM voice", "Ambient audio",
            "Fixed voice for direct player DMs — a Piper .onnx path or an ElevenLabs voice id. "
            "Blank = each player keeps a random session voice (last 25 remembered).",
            default="", phrasings=("player dm voice", "player comms voice")),
    Setting("audio.chatter.min_seconds", ("audio", "chatter", "min_seconds"), "float",
            "Chatter min gap", "Ambient audio",
            "Shortest gap between space-chatter lines, used in the BUSIEST populated systems. "
            "Chatter is populated-only and scales toward the max gap as population thins.",
            default=45.0, min=5.0, max=600.0, unit="s",
            phrasings=("chatter minimum gap", "fastest chatter", "chatter min seconds"),
            example="set the chatter min gap to 30 seconds"),
    Setting("audio.chatter.max_seconds", ("audio", "chatter", "max_seconds"), "float",
            "Chatter max gap", "Ambient audio",
            "Longest gap between space-chatter lines, used in barely-populated systems.",
            default=240.0, min=5.0, max=1800.0, unit="s",
            phrasings=("chatter maximum gap", "slowest chatter", "chatter max seconds"),
            example="set the chatter max gap to 5 minutes"),
    Setting("audio.chatter.full_population", ("audio", "chatter", "full_population"), "int",
            "Chatter full-population", "Ambient audio",
            "Population at/above which chatter runs at the min gap. Lower it to make more systems "
            "feel busy (the scale is logarithmic).",
            default=1000000000, min=1000, max=100000000000, unit="people",
            phrasings=("chatter full population", "chatter population threshold")),

    # --- Developer ---------------------------------------------------------
    Setting("dev.mock", ("dev", "mock"), "bool",
            "Dev mock mode", "Developer",
            "Swap LLM/TTS/STT for fakes: exercise the loop with zero API calls. Restart to apply.",
            default=False, phrasings=("dev mock", "mock mode"),
            example="turn mock mode on"),
]

# Fast lookup by dotted key. Also guards against a duplicate key slipping in.
by_key: dict[str, Setting] = {}
for _s in SCHEMA:
    if _s.key in by_key:  # pragma: no cover - authoring guard
        raise ValueError(f"duplicate setting key in schema: {_s.key}")
    by_key[_s.key] = _s


# --- value helpers ---------------------------------------------------------
def get_value(cfg: dict, setting: Setting) -> Any:
    """Read a setting's current value out of a config dict (None if absent)."""
    node: Any = cfg
    for p in setting.path:
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node


def set_value(patch: dict, setting: Setting, value: Any) -> dict:
    """Write `value` into `patch` at the setting's nested path, creating dicts."""
    node = patch
    for p in setting.path[:-1]:
        node = node.setdefault(p, {})
    node[setting.path[-1]] = value
    return patch


def is_overridden(overrides: dict, setting: Setting) -> bool:
    """Whether the setting currently has an entry in overrides.json."""
    node: Any = overrides
    for p in setting.path[:-1]:
        if not isinstance(node, dict) or p not in node:
            return False
        node = node[p]
    return isinstance(node, dict) and setting.path[-1] in node


def resolve_options(setting: Setting, dynamic: Optional[dict] = None) -> Optional[list]:
    """The concrete option list for an enum: static options, or a dynamic list
    supplied by the caller for an `options_source`. None when a dynamic source
    is declared but unavailable (offline) — validation then type-checks only."""
    if setting.options is not None:
        return list(setting.options)
    if setting.options_source:
        if dynamic and setting.options_source in dynamic:
            return list(dynamic[setting.options_source])
        return None
    return None


# Values accepted (case-insensitively) as booleans from string/JSON input.
_TRUE = {"true", "on", "yes", "1"}
_FALSE = {"false", "off", "no", "0"}


def validate_value(setting: Setting, value: Any,
                   options: Optional[list] = None) -> tuple[Any, Optional[str]]:
    """Validate + coerce a proposed value against a setting.

    Returns ``(coerced_value, None)`` on success, or ``(None, error_message)``
    on failure. The message names the setting and (for enums/ranges) the valid
    inputs, so both the web page and the voice layer can echo *why* it was
    rejected instead of silently widening or guessing. Pure — no I/O.
    """
    t = setting.type

    if t == "bool":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            s = value.strip().lower()
            if s in _TRUE:
                return True, None
            if s in _FALSE:
                return False, None
        return None, f"{setting.label} must be true or false"

    if t in ("int", "float"):
        if isinstance(value, bool):  # bool is an int subclass — reject explicitly
            return None, f"{setting.label} must be a number"
        try:
            num = int(value) if t == "int" else float(value)
        except (TypeError, ValueError):
            return None, f"{setting.label} must be a{'n integer' if t == 'int' else ' number'}"
        if setting.min is not None and num < setting.min:
            return None, f"{setting.label} must be at least {_fmt(setting.min)}"
        if setting.max is not None and num > setting.max:
            return None, f"{setting.label} must be at most {_fmt(setting.max)}"
        return num, None

    if t == "enum":
        opts = options if options is not None else resolve_options(setting)
        sval = value if isinstance(value, str) else str(value)
        if opts is None:
            # Dynamic options unavailable (e.g. offline ElevenLabs) — accept as a
            # string rather than reject a value we simply can't check right now.
            return sval, None
        if sval not in opts:
            shown = ", ".join(repr(o) for o in opts)
            return None, f"{setting.label}: '{sval}' is not one of [{shown}]"
        return sval, None

    if t in ("string", "path"):
        if not isinstance(value, str):
            return None, f"{setting.label} must be text"
        return value, None

    return None, f"{setting.label}: unknown setting type {t!r}"  # pragma: no cover


def _fmt(n: float) -> str:
    """Trim a whole-number float to an int for tidy messages (400.0 -> 400)."""
    return str(int(n)) if float(n).is_integer() else str(n)


def public_schema(cfg: dict, overrides: dict,
                  dynamic: Optional[dict] = None) -> list[dict]:
    """Serialize the (visible) schema into groups for the web page, folding in
    each setting's current value, resolved options, and overridden flag."""
    groups: list[dict] = []
    index: dict[str, dict] = {}
    for s in SCHEMA:
        if s.hidden:
            continue
        grp = index.get(s.group)
        if grp is None:
            grp = {"name": s.group, "settings": []}
            index[s.group] = grp
            groups.append(grp)
        grp["settings"].append({
            "key": s.key,
            "type": s.type,
            "label": s.label,
            "help": s.help,
            "options": resolve_options(s, dynamic),
            "options_source": s.options_source,
            "min": s.min,
            "max": s.max,
            "unit": s.unit,
            "value": get_value(cfg, s),
            "default": s.default,
            "overridden": is_overridden(overrides, s),
            "example": s.example,
        })
    return groups
