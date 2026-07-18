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
WHISPER_DEVICES = ["cpu"]  # CPU-only (issue #128): no local GPU ML compute competes with ED
WHISPER_COMPUTE = ["int8", "float16", "float32"]
THINKING_TIERS = ["Off", "Low", "Medium", "High", "Extra", "Max"]
CACHE_TTLS = ["5m", "1h"]
# Canonical tiers (issue #11) plus the Anthropic-flavored aliases the router still accepts.
ROUTER_PINS = ["", "cheap", "standard", "premium", "haiku", "sonnet", "opus"]
PAD_SIZES = ["S", "M", "L", "any", "match"]
EL_FORMATS = ["pcm_16000", "pcm_22050", "pcm_24000", "mp3_44100_128"]
LLM_PROVIDERS = ["anthropic", "openai", "gemini"]
# Capability/token optimization levels (issue #84): "auto" (per-provider default) + the 5 named
# budget presets, richest -> leanest. Mirrors covas/tiering.py LEVEL_NAMES (a unit test asserts the
# two stay in lock-step so this list can't drift from the real levels).
OPTIMIZATION_LEVELS = ["auto", "Full", "Standard", "Lean", "Minimal", "Bare"]
# cartesia is PERSONA-only (a premium low-latency persona voice, #18) — it is intentionally NOT in
# CAST_PROVIDERS (not offered for the NPC/comms/chatter cast).
TTS_PROVIDERS = ["elevenlabs", "piper", "edge", "azure", "openai", "cartesia"]
CAST_PROVIDERS = ["piper", "elevenlabs", "edge", "azure", "openai"]

# --- quick-panel per-provider descriptors (issue #86) ----------------------
# The control panel's LLM/Speech quick blocks MIRROR the active [llm]/[tts].provider: they render
# just the ACTIVE provider's fields listed here, GENERICALLY from the schema — no hardcoded element
# ids in index.html. Switching providers stays on the Settings page (reflect-don't-switch, v1).
#   * `fields`  — the schema keys that provider exposes as quick controls, in display order.
#   * `readonly`— of those, the ones shown but not editable on the quick panel (base_url is a
#                 Settings-page concern; the panel just reflects which endpoint is active).
#   * `supports_thinking` — LLM only: a capability flag gating the extended-thinking control
#                 (Anthropic-only in v1). The panel checks THIS flag, never `if provider ==
#                 "anthropic"`, so adding thinking to another provider is a one-line schema change.
class ProviderPanel:
    __slots__ = ("fields", "readonly", "supports_thinking")

    def __init__(self, fields, readonly=(), supports_thinking=False):
        self.fields = tuple(fields)
        self.readonly = frozenset(readonly)
        self.supports_thinking = bool(supports_thinking)


# The Anthropic-only Thinking-depth control, appended to the LLM block when the active provider's
# panel sets supports_thinking (decision 4). Kept out of `fields` so the gate is the flag, not order.
THINKING_FIELD = "anthropic.thinking.default"

LLM_PANELS: dict[str, ProviderPanel] = {
    "anthropic": ProviderPanel(("anthropic.model",), supports_thinking=True),
    "openai": ProviderPanel(("openai.base_url", "openai.model"), readonly=("openai.base_url",)),
    "gemini": ProviderPanel(("gemini.model",)),
}

TTS_PANELS: dict[str, ProviderPanel] = {
    # elevenlabs: the voice field carries the #26 filter + #94 search palette in the template (its
    # catalog is 100+ voices). Voice speed is the ONE normalized, provider-agnostic `tts.speed`
    # field (#99) — shown on EVERY provider's panel and rendered GENERICALLY off its schema min/max
    # (never hardcode 1.0-1.2), so any TTS backend gets its real speed range.
    "elevenlabs": ProviderPanel(("elevenlabs.model", "elevenlabs.voice_id", "tts.speed")),
    "edge": ProviderPanel(("edge.voice", "tts.speed")),
    "azure": ProviderPanel(("azure.region", "azure.voice", "azure.style", "tts.speed")),
    "openai": ProviderPanel(
        ("openai_tts.model", "openai_tts.voice", "openai_tts.instructions", "tts.speed")),
    "cartesia": ProviderPanel(("cartesia.model", "cartesia.voice", "cartesia.language", "tts.speed")),
    "piper": ProviderPanel(("piper.model", "tts.speed")),
}

# Sentinels for enum options that can only be resolved at runtime (from config
# or a live API). The web/voice layer supplies the concrete list; when it can't
# (offline), validation falls back to a plain type check rather than guessing.
OPT_MODELS = "@anthropic_models"        # cfg[anthropic][available_models]
OPT_EL_MODELS = "@elevenlabs_models"    # live ElevenLabs model ids
OPT_EL_VOICES = "@elevenlabs_voices"    # live ElevenLabs voice ids
# Fetched-catalog sources added by issue #92 (+#88). Each is resolved by `covas/catalog.py`
# from the provider's live list (fail-soft) and rendered as an EDITABLE COMBOBOX (combobox=True
# below) so a value outside the fetched set — the "custom, at your own risk" escape hatch — stays
# valid, and the current value is never lost when the fetch fails/offline/no-key.
OPT_OPENAI_MODELS = "@openai_models"        # GET {openai.base_url}/models (OpenAI/Groq/DeepSeek/OpenRouter)
OPT_GEMINI_MODELS = "@gemini_models"        # GET {gemini.base_url}/models (#91 reuse)
OPT_ANTHROPIC_MODELS_LIVE = "@anthropic_models_live"  # GET /v1/models, static available_models fallback
OPT_OPENAI_BASE_URLS = "@openai_base_urls"  # preset OpenAI/Groq/DeepSeek/OpenRouter + Custom…
OPT_EDGE_VOICES = "@edge_voices"            # list_edge_voices() — no key (#88)
OPT_AZURE_VOICES = "@azure_voices"          # list_azure_voices(key, region) — key+region gated (#88)
OPT_CARTESIA_VOICES = "@cartesia_voices"    # GET {cartesia.base_url}/voices — key gated
OPT_PIPER_VOICES = "@piper_voices"          # scan the local Piper voices dir for *.onnx (#120) — no key
OPT_INPUT_DEVICES = "@input_devices"        # firstrun.list_input_devices() — local, no key (#89)

# Small static option vocabularies for TTS model/voice fields (issue #92).
OPENAI_TTS_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable",
                     "nova", "onyx", "sage", "shimmer", "verse"]
OPENAI_TTS_MODELS = ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"]
CARTESIA_MODELS = ["sonic-2", "sonic", "sonic-turbo"]

# Sentinels whose value is NOT in the fetched list is still ACCEPTED (editable combobox / custom
# escape hatch). Used by validate_value so a custom base_url or an unlisted-but-valid model/voice id
# is never rejected — the UI flags it as unsupported instead of blocking the save.
_COMBOBOX_SOURCES = frozenset({
    OPT_OPENAI_MODELS, OPT_GEMINI_MODELS, OPT_ANTHROPIC_MODELS_LIVE,
    OPT_OPENAI_BASE_URLS, OPT_EDGE_VOICES, OPT_AZURE_VOICES, OPT_CARTESIA_VOICES, OPT_PIPER_VOICES,
    # The mic picker (#89) is a combobox too: a saved device may be unplugged when the page loads,
    # and blank = system default — both must stay valid rather than be rejected against a live list.
    OPT_INPUT_DEVICES,
})


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
    allow_custom: bool = False        # enum: accept a value outside options (like a combobox source)
    doc_url: Optional[str] = None     # optional "Setup guide →" link shown under the help (#121)


# The schema. Order here is the order groups first appear in the web page.
SCHEMA: list[Setting] = [

    # --- Providers ---------------------------------------------------------
    Setting("llm.provider", ("llm", "provider"), "enum",
            "LLM provider", "Providers",
            "Which LLM answers. anthropic (cloud, Claude), openai (any OpenAI-compatible cloud: "
            "OpenAI/Groq/DeepSeek/OpenRouter), or gemini (Google Gemini native — function calling + "
            "Search grounding). All cloud: cost is handled by the tiering router, not a local model.",
            default="anthropic", options=LLM_PROVIDERS,
            phrasings=("llm provider",)),
    Setting("openai.base_url", ("openai", "base_url"), "enum",
            "OpenAI LLM base URL", "Providers",
            "OpenAI-compatible chat/completions endpoint when LLM provider = openai. Pick a preset "
            "(OpenAI/Groq/DeepSeek/OpenRouter) or type a custom URL. Key comes from the key file.",
            default="https://api.openai.com/v1", options_source=OPT_OPENAI_BASE_URLS,
            phrasings=("openai llm base url",)),
    Setting("openai.model", ("openai", "model"), "enum",
            "OpenAI LLM model", "Providers",
            "Model when LLM provider = openai and the router is off/unset, e.g. gpt-4o-mini. Pick from "
            "the endpoint's live catalog or type a custom id. Per-tier models live in [openai.tiers].",
            default="gpt-4o-mini", options_source=OPT_OPENAI_MODELS,
            phrasings=("openai llm model", "openai model")),
    Setting("gemini.model", ("gemini", "model"), "enum",
            "Gemini model", "Providers",
            "Model when LLM provider = gemini and the router is off/unset. Defaults to the "
            "deprecation-proof alias gemini-flash-lite-latest (always resolves to Google's current GA "
            "Flash-Lite). Pick from Google's live catalog or type a custom id/alias. Per-tier models "
            "(Flash-Lite/Flash/Pro aliases) live in [gemini.tiers] in config.toml.",
            default="gemini-flash-lite-latest", options_source=OPT_GEMINI_MODELS,
            phrasings=("gemini model",)),
    Setting("tts.provider", ("tts", "provider"), "enum",
            "TTS provider", "Providers",
            "Which voice speaks. edge (free edge-tts neural voices — the default; no SLA, falls "
            "back to piper), azure (official Azure Neural, free tier + SLA), openai (cheap cloud, "
            "OpenAI-compatible), cartesia (low-latency premium persona), elevenlabs (cloud, "
            "premium), or piper (local, offline, free).",
            default="edge", options=TTS_PROVIDERS,
            phrasings=("tts provider", "voice provider")),
    Setting("edge.voice", ("edge", "voice"), "enum",
            "Edge voice", "Providers",
            "Edge (edge-tts) persona voice ShortName when TTS provider = edge, e.g. "
            "en-US-AriaNeural. Pick from the live voice catalog (no key needed) or type a ShortName.",
            default="en-US-AriaNeural", options_source=OPT_EDGE_VOICES,
            phrasings=("edge voice", "edge tts voice")),
    Setting("azure.region", ("azure", "region"), "string",
            "Azure region", "Providers",
            "Azure Speech resource region when TTS provider = azure, e.g. eastus, westus2, uksouth. "
            "Must match your resource. Key comes from [azure].api_key_file.",
            default="eastus", phrasings=("azure region", "azure speech region")),
    Setting("azure.voice", ("azure", "voice"), "enum",
            "Azure voice", "Providers",
            "Azure Neural persona voice ShortName when TTS provider = azure (same names as Edge), "
            "e.g. en-US-AriaNeural. Pick from the live catalog (needs the Azure key + region) or "
            "type a ShortName.",
            default="en-US-AriaNeural", options_source=OPT_AZURE_VOICES,
            phrasings=("azure voice", "azure tts voice")),
    Setting("azure.style", ("azure", "style"), "string",
            "Azure speaking style", "Providers",
            "Optional SSML speaking style/emotion for the Azure voice (voice-dependent), e.g. "
            "cheerful, newscast, chat. Blank = the voice's neutral default.",
            default="", phrasings=("azure style", "azure speaking style", "voice style")),
    Setting("openai_tts.base_url", ("openai_tts", "base_url"), "enum",
            "OpenAI TTS base URL", "Providers",
            "OpenAI-compatible audio/speech endpoint when TTS provider = openai. Pick a preset or "
            "type a custom URL. Key comes from the key file.",
            default="https://api.openai.com/v1", options_source=OPT_OPENAI_BASE_URLS,
            phrasings=("openai base url", "openai tts url")),
    Setting("openai_tts.model", ("openai_tts", "model"), "enum",
            "OpenAI TTS model", "Providers",
            "TTS model when TTS provider = openai. gpt-4o-mini-tts (cheap, default), tts-1, or tts-1-hd.",
            default="gpt-4o-mini-tts", options=OPENAI_TTS_MODELS,
            phrasings=("openai tts model", "openai voice model")),
    Setting("openai_tts.voice", ("openai_tts", "voice"), "enum",
            "OpenAI TTS voice", "Providers",
            "Voice name when TTS provider = openai: alloy, ash, ballad, coral, echo, fable, nova, "
            "onyx, sage, shimmer, verse.",
            default="alloy", options=OPENAI_TTS_VOICES,
            phrasings=("openai voice", "openai tts voice")),
    Setting("openai_tts.instructions", ("openai_tts", "instructions"), "string",
            "OpenAI TTS instructions", "Providers",
            "Optional free-text tone/delivery steer, honored by newer models (gpt-4o-mini-tts), "
            "ignored by older (tts-1). Blank = the voice's default.",
            default="", phrasings=("openai instructions", "openai tone")),
    Setting("cartesia.model", ("cartesia", "model"), "enum",
            "Cartesia model", "Providers",
            "Cartesia Sonic model when TTS provider = cartesia (a low-latency premium PERSONA "
            "voice), e.g. sonic-2. Key comes from the key file.",
            default="sonic-2", options=CARTESIA_MODELS,
            phrasings=("cartesia model", "sonic model")),
    Setting("cartesia.voice", ("cartesia", "voice"), "enum",
            "Cartesia voice id", "Providers",
            "Cartesia voice id when TTS provider = cartesia (required). Pick from your Cartesia voice "
            "library (needs the key) or type a voice id from play.cartesia.ai.",
            default="", options_source=OPT_CARTESIA_VOICES,
            phrasings=("cartesia voice", "sonic voice")),
    Setting("cartesia.language", ("cartesia", "language"), "string",
            "Cartesia language", "Providers",
            "Synthesis language (BCP-47 primary subtag) for the Cartesia voice, e.g. en.",
            default="en", phrasings=("cartesia language",)),
    Setting("piper.model", ("piper", "model"), "enum",
            "Piper voice", "Providers",
            "Local Piper voice .onnx path when TTS provider = piper (offline, free). Pick from the "
            "voices found next to your current one, or type a path (the escape hatch). Download voices "
            "with `python -m piper.download_voices en_US-lessac-medium`; the .onnx.json must sit "
            "beside it. Relative paths resolve against the project root.",
            default="", options_source=OPT_PIPER_VOICES, allow_custom=True,
            phrasings=("piper voice", "piper model", "local voice")),

    # --- Language model ----------------------------------------------------
    Setting("anthropic.model", ("anthropic", "model"), "enum",
            "Claude model", "Language model",
            "Model used when the cost router is OFF (or as the pinned tier).",
            default="claude-sonnet-5", options_source=OPT_MODELS,
            phrasings=("claude model", "language model", "the model"),
            example="switch to opus"),
    Setting("llm.optimization_level", ("llm", "optimization_level"), "enum",
            "Optimization level", "Language model",
            "How many tool clusters COVAS advertises each turn (the full set is ~10K tokens) and "
            "whether background LLM calls (proactive callouts, chatter flavor, comms variants) run. "
            "'auto' (default) picks per provider — Anthropic/Gemini/OpenAI/DeepSeek/OpenRouter get "
            "Full; Groq's token-starved free tier gets Minimal (a PAID Groq user picks Full). "
            "Manual: Full (everything), Standard (drop Search + Engineering, background off except "
            "proactive), Lean (core + checklist + Commander-state + settings, no background), "
            "Minimal (core + checklist only — safe for a 12K-TPM free tier), Bare (no tools). "
            "Chosen at startup and held for the session.",
            default="auto", options=OPTIMIZATION_LEVELS,
            phrasings=("optimization level", "tool level", "capability level"),
            example="set the optimization level to minimal"),
    Setting("llm.speak_config_errors", ("llm", "speak_config_errors"), "bool",
            "Speak misconfiguration heads-up", "Language model",
            "When the LLM fails because of a bad model id, a wrong/missing API key, or a bad "
            "request (persistent, user-fixable — not a transient blip), speak a short "
            "\"check the AI settings\" line naming the likely fix on every failed turn. Off = keep "
            "the old silent cue+log-only behavior for these.",
            default=True, phrasings=("speak config errors", "misconfiguration warning"),
            example="turn off the misconfiguration warning"),
    Setting("llm.custom_tpm", ("llm", "custom_tpm"), "int",
            "Custom endpoint tokens/min", "Language model",
            "Tokens-per-minute for a CUSTOM/unknown LLM endpoint, used only in 'auto' mode to "
            "right-size the tool budget (<15K -> Minimal, <30K -> Lean, <60K -> Standard, else "
            "Full). 0 = ignore. Leave 0 for known providers.",
            default=0, min=0, max=100000000, unit="TPM",
            phrasings=("custom tpm", "tokens per minute", "endpoint token limit")),
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

    # --- Text-to-speech ----------------------------------------------------
    # ONE normalized, provider-agnostic voice speed (issue #99): 1.0 = normal, <1.0 slower, >1.0
    # faster. Each TTS adapter maps this single value into its OWN native mechanism (ElevenLabs
    # voice_settings.speed, Edge/Azure SSML rate, OpenAI speed, Cartesia speed, Piper length_scale)
    # and clamps to that backend's real limits — so a stored value a provider can't reach is capped,
    # never errored, and a provider switch can't carry an out-of-range value across. This is the
    # quick-config "Voice speed" control (web.py maps the friendly `speed` key here).
    Setting("tts.speed", ("tts", "speed"), "float",
            "Voice speed", "Text-to-speech",
            "How fast COVAS speaks, as a normalized multiplier — 1.0 = the voice's normal pace, "
            "below 1.0 slower, above 1.0 faster. Applies to whichever TTS provider is active; each "
            "maps it into its own speed control and clamps to that voice's real range (ElevenLabs "
            "0.7–1.2; Edge/Azure/OpenAI/Cartesia/Piper go wider), so a value a provider can't reach "
            "is safely capped rather than erroring the request.",
            default=1.0, min=0.5, max=2.0, unit="×",
            phrasings=("voice speed", "speaking speed", "talk speed"),
            example="set the voice speed to 1.5"),
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
    Setting("elevenlabs.output_format", ("elevenlabs", "output_format"), "enum",
            "Audio format", "Text-to-speech",
            "pcm_16000 = low-latency, cancellable. Change only if you know why.",
            default="pcm_16000", options=EL_FORMATS,
            phrasings=("audio format", "output format")),

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

    # --- Conversation ------------------------------------------------------
    Setting("conversation.max_turns", ("conversation", "max_turns"), "int",
            "History turns kept", "Conversation",
            "Rolling in-session history so follow-ups work; older turns are trimmed.",
            default=20, min=2, max=100, unit="turns",
            phrasings=("history turns", "conversation memory", "how many turns")),
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
    Setting("audio.input_device", ("audio", "input_device"), "enum",
            "Microphone", "Voice input",
            "Which capture device COVAS listens to. Pick from your input devices (blank = the "
            "Windows default mic). Prefer the FULL-name entry over a truncated duplicate — the "
            "short copy is often a silent MME clone. Stored by NAME, so it survives reconnects; "
            "changing it applies live (the recorder + hands-free listener re-open on the new mic).",
            default="", options_source=OPT_INPUT_DEVICES,
            phrasings=("microphone", "mic", "input device", "capture device"),
            example="set the microphone to headset"),

    # --- Activation mode (issue #63) --------------------------------------
    # Hands-free continuous listening vs push-to-talk. Switching this applies LIVE
    # (the app starts/stops the VAD mic listener via _after_settings_change).
    Setting("listen.mode", ("listen", "mode"), "enum",
            "Activation mode", "Voice input",
            "How a turn starts: 'ptt' (push-to-talk, the default — hold the talk key) or "
            "'continuous' (hands-free — a voice-activity gate captures you when you start "
            "talking and stops after a short silence). Continuous is local-only (same local "
            "Whisper, no extra cloud cost) and keeps barge-in; PTT still works in either mode.",
            default="ptt", options=["ptt", "continuous"],
            phrasings=("listening mode", "activation mode", "hands free", "hands-free",
                       "continuous listening"),
            example="switch to continuous listening"),
    Setting("listen.energy_threshold", ("listen", "energy_threshold"), "float",
            "Voice-detect sensitivity", "Voice input",
            "Hands-free only: loudness (RMS, ~0-1) a mic frame must reach to count as speech. "
            "Raise it if background noise keeps opening a capture; lower it if quiet speech is "
            "missed.",
            default=0.02, min=0.0, max=1.0,
            phrasings=("voice detection sensitivity", "vad threshold", "mic sensitivity"),
            example="set the voice detect sensitivity to 0.03"),
    Setting("listen.start_ms", ("listen", "start_ms"), "float",
            "Speech-onset debounce", "Voice input",
            "Hands-free only: voiced time needed to confirm you've started talking, so a click "
            "can't open a capture. Milliseconds.",
            default=120.0, min=0.0, max=2000.0, unit="ms",
            phrasings=("onset debounce", "speech start debounce")),
    Setting("listen.min_speech_ms", ("listen", "min_speech_ms"), "float",
            "Minimum utterance", "Voice input",
            "Hands-free only: shortest voiced capture that counts as a real utterance; briefer "
            "blips are dropped as noise. Milliseconds.",
            default=250.0, min=0.0, max=5000.0, unit="ms",
            phrasings=("minimum utterance", "min speech length")),
    Setting("listen.hangover_ms", ("listen", "hangover_ms"), "float",
            "End-of-speech silence", "Voice input",
            "Hands-free only: trailing silence that ends an utterance. Longer tolerates "
            "mid-sentence pauses but reacts slower; shorter is snappier but may cut you off. "
            "Milliseconds.",
            default=700.0, min=100.0, max=5000.0, unit="ms",
            phrasings=("end of speech silence", "hangover time", "trailing silence")),
    Setting("listen.frame_ms", ("listen", "frame_ms"), "float",
            "VAD frame size", "Voice input",
            "Hands-free only: length of each analysis frame the voice-activity gate scores. "
            "Rarely needs changing. Milliseconds.",
            default=30.0, min=10.0, max=100.0, unit="ms",
            phrasings=("vad frame size", "listen frame size")),

    # --- Wake word (issue #64) --------------------------------------------
    # Optional arming phrase for hands-free mode. OFF by default (blank). PTT is never gated.
    Setting("listen.wake_word", ("listen", "wake_word"), "string",
            "Wake word", "Voice input",
            "Hands-free only: an optional arming phrase (e.g. 'COVAS'). Blank (default) = off — "
            "continuous mode runs a turn on any capture. Set it and a hands-free capture only "
            "becomes a turn if you say the phrase; it's stripped before your words reach the "
            "model. Push-to-talk is never gated.",
            default="", phrasings=("wake word", "wake phrase", "activation word", "trigger word"),
            example="set the wake word to COVAS"),
    Setting("listen.wake_word_fuzzy", ("listen", "wake_word_fuzzy"), "bool",
            "Wake-word fuzzy match", "Voice input",
            "Hands-free only: tolerate small speech-to-text slips of the wake word (e.g. "
            "'Kovas'/'Covis' for 'COVAS'), so a one-letter mistranscription doesn't swallow "
            "your command. Off = require an exact (still case-insensitive) match.",
            default=True, phrasings=("wake word fuzzy match", "fuzzy wake word"),
            example="turn off wake word fuzzy match"),

    # --- Speech-to-text ----------------------------------------------------
    Setting("whisper.model", ("whisper", "model"), "enum",
            "Whisper model", "Speech-to-text",
            "Local STT model. Bigger = more accurate but slower.",
            default="small", options=WHISPER_SIZES,
            phrasings=("whisper model", "transcription model", "speech model"),
            example="set the whisper model to medium"),
    Setting("whisper.device", ("whisper", "device"), "enum",
            "Whisper device", "Speech-to-text",
            "Whisper runs on the CPU — no GPU needed, and nothing competes with Elite for the GPU.",
            default="cpu", options=WHISPER_DEVICES,
            phrasings=("whisper device",),
            example="set the whisper device to cpu"),
    Setting("whisper.compute_type", ("whisper", "compute_type"), "enum",
            "Whisper compute type", "Speech-to-text",
            "int8 = fast + low memory on CPU (the recommended default).",
            default="int8", options=WHISPER_COMPUTE,
            phrasings=("whisper compute type",)),
    Setting("whisper.language", ("whisper", "language"), "string",
            "Whisper language", "Speech-to-text",
            "Force a language code (en), or blank to auto-detect.",
            default="en", phrasings=("whisper language", "transcription language"),
            example="set the whisper language to auto"),

    # --- Personality -------------------------------------------------------
    Setting("personality.enabled", ("personality", "enabled"), "bool",
            "Personality", "Personality",
            "Load personality.txt as the system prompt (Commander address + campaign context).",
            default=True, phrasings=("personality", "character"),
            example="turn personality off"),
    # --- Crew (issues #69, #70) --------------------------------------------
    Setting("crew.enabled", ("crew", "enabled"), "bool",
            "Interactive crew", "Personality",
            "Let replies voice a NAMED crew member: the model may start a line with '[Name]' and "
            "that line is spoken in its own distinct, radio-filtered voice (the ship persona still "
            "speaks every unprefixed line). Define each character's role, personality, and voice on "
            "the Crew tab of the control panel — where you can also ADOPT a hired NPC fighter pilot "
            "from your journal (name + role + a generated personality). A character left on Auto "
            "with a written personality gets a BEST-FIT voice (an LLM casts it against your voice "
            "catalog, in the background, cached); persona-less or pairing-unavailable members keep "
            "the deterministic per-name fallback. A pinned voice always overrides Auto. Gated by "
            "[personality].auto_voice_pairing. Crew also come ALIVE two more ways (issue #126): "
            "when you ADDRESS a member by name ('Nyx, how are we looking?') they answer in their "
            "own voice, in character; and roster members occasionally speak a brief, in-character "
            "AMBIENT line grounded in their role + the live situation (needs the ambient audio "
            "layer + [audio.cues].flavor; paced by [crew].chatter_min/max_seconds). Off by default.",
            default=False, phrasings=("crew", "crew voices", "the crew"),
            example="turn crew on"),
    Setting("crew.limit_to_seats", ("crew", "limit_to_seats"), "bool",
            "Limit crew to ship seats", "Personality",
            "Cap each SHIP-SPECIFIC crew roster at that hull's real multicrew SEAT count (from the "
            "bundled ship-spec table) instead of the generic maximum — so a small hull can't carry a "
            "full three-person cast. Per-ship rosters only; the Default roster (issue #127) is never "
            "seat-capped. An unknown hull falls back to the generic cap. Off by default (opt-in "
            "realism) — no existing roster is silently truncated.",
            default=False, phrasings=("crew seats", "limit crew to seats", "seat cap"),
            example="limit crew to ship seats"),

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
    Setting("proactive.place_cooldown", ("proactive", "place_cooldown"), "int",
            "Place/history remark cooldown", "Proactive callouts",
            "How long before another place-aware or visit-history remark (engineer base, your "
            "carrier, 'tenth time here today') may ride an arrival callout. Keeps them occasional.",
            default=900, min=0, max=7200, unit="s",
            phrasings=("place callout cooldown", "history callout cooldown")),
    Setting("proactive.long_jump_enabled", ("proactive", "long_jump_enabled"), "bool",
            "Long-jump flavor remark", "Proactive callouts",
            "On a longer-than-normal hyperspace jump, pass the tunnel time with a short, varied, "
            "in-character remark. Pure atmosphere — asserts no game facts.",
            default=True, phrasings=("long jump remark", "hyperspace flavor")),
    Setting("proactive.long_jump_ly", ("proactive", "long_jump_ly"), "float",
            "Long-jump threshold", "Proactive callouts",
            "How far (light-years) a plotted jump must be to count as 'longer than normal' and "
            "trigger a flavor remark. Ordinary shorter jumps stay quiet.",
            default=50.0, min=10.0, max=500.0, unit="ly",
            phrasings=("long jump distance", "long jump threshold")),
    Setting("proactive.long_jump_cooldown", ("proactive", "long_jump_cooldown"), "int",
            "Long-jump remark cooldown", "Proactive callouts",
            "At most one long-jump flavor remark per this many seconds, so back-to-back long hops "
            "don't each get a line.",
            default=300, min=0, max=3600, unit="s",
            phrasings=("long jump cooldown",)),

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
            "Call out whether the star you're arriving at can be fuel-scooped (and the one "
            "after it, when that's the useful thing to know).",
            default=True, phrasings=("scoopable callouts", "scoopable star callout")),
    Setting("route.callout_hazard", ("route", "callout_hazard"), "bool",
            "Announce hazard star", "Route callouts",
            "Warn when the arriving star is a neutron star or white dwarf (exclusion-zone "
            "jets, can't be scooped). Replaces the plain 'not scoopable' line for those.",
            default=True, phrasings=("hazard callouts", "neutron star warning",
                                      "white dwarf warning")),
    Setting("route.callout_jumps_remaining", ("route", "callout_jumps_remaining"), "bool",
            "Announce jumps remaining", "Route callouts",
            "Call out jumps remaining to the destination, every Nth jump.",
            default=True, phrasings=("jumps remaining callouts",)),
    Setting("route.callout_arrival", ("route", "callout_arrival"), "bool",
            "Announce arrival", "Route callouts",
            "Call out arrival at the final system when the route completes.",
            default=True, phrasings=("arrival callouts",)),

    # --- Navigation & search ----------------------------------------------
    Setting("nav.enabled", ("nav", "enabled"), "bool",
            "Find closest module", "Navigation & search",
            "Voice: 'find the closest station that sells module X'.",
            default=True, phrasings=("find closest module", "module search")),
    Setting("nav.default_pad_size", ("nav", "default_pad_size"), "enum",
            "Default landing pad", "Navigation & search",
            "Pad size your ship needs; a voice request can override per search. 'match' "
            "(Match Current Ship Size) filters using whatever ship you're CURRENTLY flying, "
            "read live from Elite Dangerous — falls back to Large if the ship isn't known yet "
            "(e.g. before the first Loadout event), so a search never sends you somewhere you "
            "can't dock.",
            default="L", options=PAD_SIZES,
            phrasings=("landing pad size", "pad size"),
            example="set the pad size to match my ship"),
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
    Setting("riches_plan.enabled", ("riches_plan", "enabled"), "bool",
            "Road-to-Riches planner", "Navigation & search",
            "Voice: 'plan me a Road to Riches route'. Plans a Spansh route of nearby high-value "
            "unscanned bodies to first-discovery-scan for credits and copies the first system to "
            "your clipboard for the galaxy map.",
            default=False, phrasings=("road to riches", "riches planner", "exploration route")),
    Setting("riches_plan.default_max_results", ("riches_plan", "default_max_results"), "int",
            "Road-to-Riches systems", "Navigation & search",
            "Default number of systems in the route when you don't say.",
            default=25, min=1, max=250, unit="systems",
            phrasings=("road to riches systems",)),

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
    Setting("keybinds.focus_before_inject", ("keybinds", "focus_before_inject"), "bool",
            "Focus Elite before injecting", "Keybinds",
            "Bring the Elite Dangerous window to the front right before pressing a ship control or "
            "sending a comms message, so the keypress can't misfire into another window. A no-op "
            "when Elite is already focused. Leave ON.",
            default=True, phrasings=("focus before injecting", "auto focus elite",
                                     "auto focus game")),
    Setting("keybinds.confirm_window", ("keybinds", "confirm_window"), "int",
            "Confirm window", "Keybinds",
            "Seconds an armed action stays confirmable before it expires.",
            default=60, min=5, max=300, unit="s",
            phrasings=("confirm window",)),

    # --- Custom macros (#50) ----------------------------------------------
    Setting("macros.enabled", ("macros", "enabled"), "bool",
            "Custom macros", "Custom macros",
            "Let the Commander author their OWN named, triggerable macros by voice/UI. A macro "
            "may only use actions in the Keybinds allowlist, so it can't do anything you haven't "
            "already enabled. Needs Keybinds + ED monitoring to run.",
            default=False, phrasings=("custom macros", "macros", "macro authoring"),
            example="turn custom macros on"),
    Setting("macros.require_confirmation", ("macros", "require_confirmation"), "bool",
            "Require confirmation", "Custom macros",
            "A consequential macro needs a SEPARATE spoken confirm before it runs. Leave ON.",
            default=True, phrasings=("macro confirmation",)),
    Setting("macros.combat_guard", ("macros", "combat_guard"), "bool",
            "Combat guard", "Custom macros",
            "Refuse to run a macro during danger/interdiction (or unknown status). Leave ON.",
            default=True, phrasings=("macro combat guard",)),
    Setting("macros.mode_guard", ("macros", "mode_guard"), "bool",
            "Mode guard", "Custom macros",
            "Only run a macro whose actions are valid for your current game mode. Leave ON.",
            default=True, phrasings=("macro mode guard",)),
    Setting("macros.confirm_window", ("macros", "confirm_window"), "int",
            "Confirm window", "Custom macros",
            "Seconds an armed macro stays confirmable before it expires.",
            default=60, min=5, max=300, unit="s",
            phrasings=("macro confirm window",)),

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

    # --- Combat reflexes (Tier-2) -----------------------------------------
    # The second push-to-talk for the local phrase-spotter fast path (issue #38). A capture on
    # this key is matched against a fixed combat vocabulary and fires the reflex WITHOUT the LLM.
    # Blank = disabled (no second hook installed). Requires [reflex].enabled + an allowlisted
    # reflex to actually fire; the combat-permissive guard + hard abort still apply (#36).
    Setting("reflex.ptt", ("reflex", "ptt"), "string",
            "Reflex fast-PTT key", "Combat reflexes",
            "Second push-to-talk for the instant reflex fast path — a snap 'chaff!' on this key "
            "fires locally with no LLM round-trip. Bind a DIFFERENT key than the talk key; blank "
            "disables it. A non-combat phrase on it falls through to a normal turn.",
            default="", phrasings=("reflex key", "reflex push to talk", "combat reflex key"),
            example="set the reflex key to right bracket"),
    # The AMBIENT (no-voice) auto-reflex layer (#37): fire the same reflexes off ED Status/journal
    # thresholds. Off at BOTH levels by default — the master switch AND every per-reflex enable.
    # Shares [reflex].combat_guard (the guard toggle isn't duplicated here).
    Setting("reflex.auto.enabled", ("reflex", "auto", "enabled"), "bool",
            "Auto-reflexes", "Combat reflexes",
            "Fire defensive reflexes AUTOMATICALLY (no voice) when ED status crosses a threshold — "
            "a heat sink on overheat, chaff when targeted. Off by default; needs ED monitoring, "
            "[reflex].enabled, and a per-reflex enable below. Same combat-permissive guard as the "
            "spoken reflexes.",
            default=False, phrasings=("auto reflexes", "automatic reflexes", "reflex automation"),
            example="turn auto reflexes on"),
    Setting("reflex.auto.min_interval", ("reflex", "auto", "min_interval"), "float",
            "Auto-reflex min interval", "Combat reflexes",
            "Global governor: no two auto-reflexes fire within this many seconds.",
            default=3.0, min=0.0, max=60.0, unit="s",
            phrasings=("auto reflex interval", "reflex min interval")),
    Setting("reflex.auto.heat_sink.enabled", ("reflex", "auto", "heat_sink", "enabled"), "bool",
            "Auto heat sink", "Combat reflexes",
            "Automatically deploy a heat sink when the ship overheats. Needs DeployHeatSink bound "
            "to a key in ED.",
            default=False, phrasings=("auto heat sink", "automatic heat sink")),
    Setting("reflex.auto.heat_sink.threshold", ("reflex", "auto", "heat_sink", "threshold"),
            "float", "Heat-sink threshold", "Combat reflexes",
            "Heat percent to react at. ED signals overheating at >100%, so 100 fires on that flag; "
            "a value above 100 disables the reaction by threshold.",
            default=100.0, min=0.0, max=200.0, unit="%",
            phrasings=("heat sink threshold", "heat threshold")),
    Setting("reflex.auto.heat_sink.cooldown", ("reflex", "auto", "heat_sink", "cooldown"), "float",
            "Heat-sink cooldown", "Combat reflexes",
            "Minimum seconds between automatic heat-sink deployments.",
            default=10.0, min=0.0, max=300.0, unit="s",
            phrasings=("heat sink cooldown",)),
    Setting("reflex.auto.chaff.enabled", ("reflex", "auto", "chaff", "enabled"), "bool",
            "Auto chaff", "Combat reflexes",
            "Automatically fire chaff when a hostile locks on or you're interdicted. Needs "
            "FireChaffLauncher bound to a key in ED.",
            default=False, phrasings=("auto chaff", "automatic chaff")),
    Setting("reflex.auto.chaff.cooldown", ("reflex", "auto", "chaff", "cooldown"), "float",
            "Chaff cooldown", "Combat reflexes",
            "Minimum seconds between automatic chaff bursts.",
            default=20.0, min=0.0, max=300.0, unit="s",
            phrasings=("chaff cooldown",)),

    # --- Send in-game comms (issue #49) -----------------------------------
    Setting("comms_send.enabled", ("comms_send", "enabled"), "bool",
            "Send in-game messages", "Comms",
            "Compose + send Elite Dangerous chat (local/wing/squadron/direct) by voice. "
            "Always reads the message back and sends only on a separate confirm.",
            default=False, phrasings=("send messages", "in-game messages", "voice comms"),
            example="turn on sending in-game messages"),
    Setting("comms_send.confirm_window", ("comms_send", "confirm_window"), "int",
            "Confirm window", "Comms",
            "Seconds a composed message stays confirmable before it expires.",
            default=60, min=5, max=300, unit="s",
            phrasings=("comms confirm window",)),
    Setting("comms_send.open_bind", ("comms_send", "open_bind"), "string",
            "Open-comms bind", "Comms",
            "ED action token that opens the chat text box (bind it to a KEY in-game).",
            default="QuickCommsPanel", phrasings=("comms open bind",)),
    Setting("comms_send.channel_local", ("comms_send", "channel_local"), "string",
            "Local chat bind", "Comms",
            "ED action token that selects the local chat channel before sending. Blank = send on "
            "your currently-selected channel. Bind it to a key in Elite Dangerous.",
            default="", phrasings=("local chat bind", "local comms bind")),
    Setting("comms_send.channel_wing", ("comms_send", "channel_wing"), "string",
            "Wing chat bind", "Comms",
            "ED action token that selects the wing chat channel before sending. Blank = send on "
            "your currently-selected channel. Bind it to a key in Elite Dangerous.",
            default="", phrasings=("wing chat bind", "wing comms bind")),
    Setting("comms_send.channel_squadron", ("comms_send", "channel_squadron"), "string",
            "Squadron chat bind", "Comms",
            "ED action token that selects the squadron chat channel before sending. Blank = send on "
            "your currently-selected channel. Bind it to a key in Elite Dangerous.",
            default="", phrasings=("squadron chat bind", "squadron comms bind")),
    Setting("comms_send.channel_direct", ("comms_send", "channel_direct"), "string",
            "Direct-message bind", "Comms",
            "ED action token that selects the direct-message chat channel before sending. Blank = send "
            "on your currently-selected channel. Bind it to a key in Elite Dangerous.",
            default="", phrasings=("direct message bind", "direct chat bind", "dm bind")),
    Setting("comms_send.settle_seconds", ("comms_send", "settle_seconds"), "float",
            "Comms settle delay", "Comms",
            "Pause after focusing the chat box, selecting a channel, or pasting, so the field keeps up "
            "before the next keystroke. Raise it if messages come out garbled.",
            default=0.15, min=0.0, max=2.0, unit="s",
            phrasings=("comms settle delay", "comms settle seconds")),

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
    Setting("audio.voices.player_ref", ("audio", "voices", "player_ref"), "enum",
            "Player-DM voice", "Ambient audio",
            "Fixed voice for direct player DMs. Pick a voice from your ElevenLabs library, or type a "
            "Piper .onnx path / any voice id (the escape hatch). "
            "Blank = each player keeps a random session voice (last 25 remembered).",
            default="", options_source=OPT_EL_VOICES, allow_custom=True,
            phrasings=("player dm voice", "player comms voice")),
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

    # --- Companion HUD -----------------------------------------------------
    Setting("hud.enabled", ("hud", "enabled"), "bool",
            "Companion HUD overlay", "Companion HUD",
            "Show a small transparent, always-on-top overlay with my voice-loop state, your "
            "current checklist step, and route progress. Off by default; needs a desktop "
            "(no effect headless).",
            default=False, phrasings=("hud", "the hud", "overlay", "hud overlay"),
            example="turn the HUD on",
            doc_url="https://dseelinger.github.io/CovasPlusPlus/using/hud/#turning-it-on-and-off"),
    Setting("hud.vr_enabled", ("hud", "vr_enabled"), "bool",
            "VR HUD overlay", "Companion HUD",
            "Show the same HUD as a true in-headset SteamVR overlay floating in the cockpit. Off "
            "by default; needs SteamVR running and Elite Dangerous rendering through it — nothing "
            "to install. Fails soft with no VR runtime (the panel simply doesn't appear).",
            default=False, phrasings=("vr hud", "the vr hud", "vr overlay", "headset hud"),
            example="turn the VR HUD on",
            doc_url="https://dseelinger.github.io/CovasPlusPlus/using/hud/#in-vr-the-in-headset-overlay"),
    Setting("hud.web_enabled", ("hud", "web_enabled"), "bool",
            "Web HUD (OpenKneeboard)", "Companion HUD",
            "Serve the same HUD as a transparent web page at /hud for OpenKneeboard's Web "
            "Dashboard tab, so it composites in-headset on ANY OpenXR runtime "
            "(OpenComposite / VDXR / Virtual Desktop), not just SteamVR. Off by default; needs the "
            "control panel running (run_covas_ui.py) and a one-time OpenKneeboard tab setup. "
            "Independent of the desktop and SteamVR HUDs.",
            default=False,
            phrasings=("web hud", "the web hud", "kneeboard hud", "openkneeboard hud"),
            example="turn the web HUD on",
            doc_url="https://dseelinger.github.io/CovasPlusPlus/using/hud/"
                    "#in-headset-without-steamvr-the-web-hud-openkneeboard"),
    Setting("hud.vr_placement", ("hud", "vr_placement"), "enum",
            "VR HUD placement", "Companion HUD",
            "Where the VR panel sits: 'world' (cockpit-fixed, parked in front — the comfortable "
            "default) or 'head' (locked to your view, follows where you look).",
            default="world", options=["world", "head"],
            phrasings=("vr hud placement", "vr overlay placement")),
    Setting("hud.vr_width_m", ("hud", "vr_width_m"), "float",
            "VR HUD width", "Companion HUD",
            "Physical width of the VR overlay panel in metres. ~0.55 m reads well at arm's length.",
            default=0.55, min=0.15, max=3.0, unit="m",
            phrasings=("vr hud width", "vr overlay size")),
    Setting("hud.vr_distance_m", ("hud", "vr_distance_m"), "float",
            "VR HUD distance", "Companion HUD",
            "How far in front the VR panel sits, in metres. Applies live — say a new value to "
            "move it closer or farther.",
            default=1.30, min=0.30, max=5.0, unit="m",
            phrasings=("vr hud distance", "vr overlay distance")),
    Setting("hud.vr_offset_x_m", ("hud", "vr_offset_x_m"), "float",
            "VR HUD lateral offset", "Companion HUD",
            "Left/right offset of the VR panel in metres (positive = right, negative = left). "
            "Applies live.",
            default=0.0, min=-2.0, max=2.0, unit="m",
            phrasings=("vr hud left right", "vr overlay sideways")),
    Setting("hud.vr_offset_y_m", ("hud", "vr_offset_y_m"), "float",
            "VR HUD height", "Companion HUD",
            "Up/down offset of the VR panel in metres (positive = up, negative = below eye-line). "
            "Applies live.",
            default=-0.12, min=-2.0, max=2.0, unit="m",
            phrasings=("vr hud height", "vr overlay up down")),
    Setting("hud.vr_pitch_deg", ("hud", "vr_pitch_deg"), "float",
            "VR HUD tilt", "Companion HUD",
            "Tilt of the VR panel in degrees; positive leans the top toward you, so a panel below "
            "your eye-line angles up to face you. Applies live.",
            default=0.0, min=-60.0, max=60.0, unit="°",
            phrasings=("vr hud tilt", "vr overlay pitch")),
    Setting("hud.vr_curvature", ("hud", "vr_curvature"), "float",
            "VR HUD curvature", "Companion HUD",
            "Curve of the VR panel: 0 is flat, 1 is a full cylinder. A gentle ED-style wrap is "
            "~0.1. Applies live.",
            default=0.1, min=0.0, max=1.0,
            phrasings=("vr hud curve", "vr overlay curvature")),

    # --- Appearance --------------------------------------------------------
    Setting("ui.theme", ("ui", "theme"), "enum",
            "Theme", "Appearance",
            "Colour theme for the control panel: 'dark' (the default), 'light' (light surfaces for "
            "daytime/streaming use), or 'elite' (the game's orange-on-black cockpit HUD look). "
            "Applies live — no restart.",
            default="dark", options=["dark", "light", "elite"],
            phrasings=("theme", "ui theme", "color theme", "colour theme"),
            example="switch to the light theme"),

    # --- Control panel -----------------------------------------------------
    Setting("ui.host", ("ui", "host"), "string",
            "Control panel host", "Control panel",
            "Interface the local control panel binds to. Restart to apply.",
            default="127.0.0.1", phrasings=("control panel host",)),
    Setting("ui.port", ("ui", "port"), "int",
            "Control panel port", "Control panel",
            "Port the local control panel serves on. Restart to apply.",
            default=8765, min=1, max=65535, phrasings=("control panel port",)),

    # NOTE: dev mock mode (`[dev].mock`) is intentionally NOT a Setting (issue #130) — it swaps the
    # LLM/TTS/STT for fakes, useful only for tests/dev, never for an end user. The mechanism stays
    # (config.toml `[dev] mock = true`, `COVAS_MOCK=1`, `config.mock_enabled`, `app.self.mock`); it's
    # just not on the Settings page or voice-toggleable.
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


def is_combobox(setting: Setting) -> bool:
    """Whether an enum is an EDITABLE combobox (issue #92): its dropdown is a discovery aid, but a
    value OUTSIDE the fetched list stays valid (the custom / at-your-own-risk escape hatch). Only the
    fetched-catalog sources are open; static enums and the strict ElevenLabs/anthropic lists are not.
    A per-setting `allow_custom` (issue #120: the Player-DM voice picks from ElevenLabs but must also
    accept a typed Piper .onnx path / unlisted id) opens an otherwise-strict source the same way."""
    return setting.options_source in _COMBOBOX_SOURCES or setting.allow_custom


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
        if is_combobox(setting):
            # Editable combobox (issue #92): the dropdown is a discovery aid, but a value outside it
            # (a custom base_url, an unlisted-but-valid model/voice id) MUST stay valid — the UI
            # flags it as unsupported instead of blocking. Just type-check it as a non-empty string.
            return sval, None
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


# Choices that only belong on the PUBLIC settings surface when their [experimental.<flag>] toggle
# is on (issue #123). Keyed by (setting.key, choice) -> flag name. Off, the choice is dropped from
# the rendered dropdown so the public is never offered a gated provider/mode (whose registration
# seam would refuse it anyway); Doug's overrides.json opt-in flips the flag AND the choice back in.
# Validation is deliberately NOT filtered here — the seam gate + the fail-soft TTS reload contain
# an errant value, so this stays a display-only nicety and keeps the pure validator options-driven.
_EXPERIMENTAL_CHOICES = {
    ("tts.provider", "azure"): "azure_tts",
    ("tts.provider", "cartesia"): "cartesia_tts",
    ("audio.voices.cast_provider", "azure"): "azure_tts",
    ("listen.mode", "continuous"): "voice_activation",
}


def public_options(cfg: dict, s: Setting, opts: Optional[list]) -> Optional[list]:
    """Drop experimental-gated choices from an enum's option list for the public UI (issue #123).
    A no-op for any setting/choice not in `_EXPERIMENTAL_CHOICES`, and for one whose flag is on."""
    if not opts:
        return opts
    from .config import experimental
    gated = {choice for (key, choice), flag in _EXPERIMENTAL_CHOICES.items()
             if key == s.key and not experimental(cfg, flag)}
    return [o for o in opts if o not in gated] if gated else opts


def field_payload(cfg: dict, overrides: dict, s: Setting,
                  dynamic: Optional[dict] = None, readonly: bool = False) -> dict:
    """Serialize ONE setting into the dict the web surfaces render from: type + display metadata,
    resolved options, current value, and the overridden flag. `readonly` marks a control the quick
    panel shows but edits on the Settings page (issue #86). Shared by `public_schema` (the full
    settings page) and `panel_fields` (the quick panel) so the two can't describe a field
    differently."""
    return {
        "key": s.key,
        "type": s.type,
        "label": s.label,
        "help": s.help,
        "doc_url": s.doc_url,
        "options": public_options(cfg, s, resolve_options(s, dynamic)),
        "options_source": s.options_source,
        "combobox": is_combobox(s),
        "min": s.min,
        "max": s.max,
        "unit": s.unit,
        "value": get_value(cfg, s),
        "default": s.default,
        "overridden": is_overridden(overrides, s),
        "example": s.example,
        "readonly": readonly,
    }


def panel_fields(cfg: dict, overrides: dict, keys, readonly=(),
                 dynamic: Optional[dict] = None) -> list[dict]:
    """The quick-panel payload for a provider (issue #86): serialize each schema key in `keys`
    (skipping any unknown one) into a field dict the control panel renders GENERICALLY. `readonly`
    is the set of keys shown but not editable there. Order follows `keys`."""
    ro = set(readonly)
    out: list[dict] = []
    for k in keys:
        s = by_key.get(k)
        if s is None:  # a panel descriptor naming a key that isn't in the schema — skip, fail-soft
            continue
        out.append(field_payload(cfg, overrides, s, dynamic, readonly=k in ro))
    return out


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
        grp["settings"].append(field_payload(cfg, overrides, s, dynamic))
    return groups
