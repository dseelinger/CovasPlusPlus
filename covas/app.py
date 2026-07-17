"""COVAS++ core loop (headless).

Hold PTT -> listening cue + capture. Release -> processing cue, then in a worker:
transcribe (Whisper) -> Claude (streaming) -> done cue -> speak (ElevenLabs).
Cancel key aborts the in-flight Claude call and any TTS playback. Pressing PTT again
also interrupts current speech.
"""
from __future__ import annotations
import datetime as _dt
import queue
import sys
import threading
import time
from pathlib import Path

import keyboard

from .config import load_config, load_overrides, save_overrides, deep_merge, mock_enabled
from . import settings_schema as schema
from .audio import CuePlayer, Recorder
from .listen import VadListener
from .wake import WakeWordGate
from .reflex_spotter import PhraseSpotter
from .events import EventBus
from .checklist import Checklist
from .capabilities import CapabilityRegistry
from .capabilities.checklist_capability import ChecklistCapability
from .providers.base import LLMProvider, STTProvider, TTSProvider
from .providers.factory import make_llm, make_stt, make_tts
from .providers import _retry
from .router import Router
from . import tiering
from .ed import ContextDetector
from .memory import MemoryDetector
from . import crew as crew_mod

def _harden_streams(streams) -> None:
    """Make console output lossy-safe. Claude replies can contain Unicode (arrows, em-dashes,
    emoji) the default Windows console (cp1252) can't encode — a stray glyph would raise
    UnicodeEncodeError and crash the worker mid-reply. Reconfigure each stream to utf-8 with
    errors="replace" so an unencodable glyph degrades to a placeholder instead of crashing.
    Best-effort per stream (older/odd streams may lack reconfigure)."""
    for _stream in streams:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — older/odd streams may lack reconfigure
            pass


_harden_streams((sys.stdout, sys.stderr))

STATES = ("Idle", "Listening", "Transcribing", "Thinking", "Searching", "Speaking")
# States where COVAS is heads-down WORKING on a spoken turn — the window the soft "thinking" bed
# (issue #5) fills. Entering one (arms, enabled) starts the bed; entering any OTHER state stops it,
# which is what wires the stop into every exit path (reply -> Speaking, cancel/error -> Idle,
# barge-in -> Listening) through the single set_state chokepoint.
_WORKING_STATES = frozenset({"Transcribing", "Thinking", "Searching"})

# Barge-in playback-halt bounds (issue #71). On barge-in the mic must not open until COVAS's own
# reply has actually gone silent, or the capture records its tail (there is no acoustic echo
# cancellation) and it pollutes the next utterance. _interrupt() hard-stops the mixer and AWAITS
# confirmed silence, bounded by this timeout so barge-in still feels instant. The mute window is a
# belt-and-braces backstop applied to the barge-in capture only (residual device-buffer tail, and
# the direct-device TTS path the app can't synchronously stop).
_HALT_PLAYBACK_TIMEOUT_MS = 150.0
_BARGE_IN_MUTE_MS = 150.0
# States in which playback (a spoken reply or the thinking bed / cues) may be audible, so a PTT/VAD
# onset from one of them is a barge-in that needs the mute window.
_PLAYBACK_STATES = _WORKING_STATES | frozenset({"Speaking"})


def _pop_path(d: dict, path: tuple) -> None:
    """Remove the nested key at `path` from `d`, if present (no-op otherwise)."""
    if not path:
        return
    for p in path[:-1]:
        d = d.get(p)  # type: ignore[assignment]
        if not isinstance(d, dict):
            return
    d.pop(path[-1], None)


def _prune_empty(d: dict) -> None:
    """Drop now-empty sub-dicts so overrides.json doesn't accumulate {} husks."""
    for k in [k for k, v in d.items() if isinstance(v, dict)]:
        _prune_empty(d[k])
        if not d[k]:
            del d[k]


# ---- Live-settings classification (issue #90) -------------------------------
# Providers are built once at the composition root and cached for the process; the LLM/TTS impls
# read their config at CONSTRUCTION, so a config change to their sections means a REBUILD (a fresh
# instance rebound in place), never an in-place mutation. Everything else (router tiers, ED/memory
# detectors, whisper, the VAD listener, hotkeys, the mic, audio volumes/toggles) is read fresh each
# turn or reconciled, so it applies live WITHOUT a rebuild.
#
# Section-granularity diff (decision #3): any change ANYWHERE under one of these top-level sections
# rebuilds that provider; an unrelated key never does.
_LLM_SECTIONS: tuple[str, ...] = ("llm", "anthropic", "openai", "gemini", "ollama")
_TTS_SECTIONS: tuple[str, ...] = (
    "tts", "elevenlabs", "edge", "azure", "openai_tts", "cartesia", "piper")

# The TRUE minimum of settings that ONLY take effect on a RESTART (decision #5), as schema keys:
#   audio.enabled / audio.mix_sample_rate — the bus-mixer graph is cross-wired and the shared
#     output device opened at init/start (see the load-bearing fallback around BusMixer.start);
#   dev.mock — swaps the whole LLM/TTS/STT set for fakes at the composition root;
#   ui.host / ui.port — bound by Flask when the control panel launches.
# Explicitly NOT here (all live): provider/base_url/model/key/voice (rebuild), whisper.* (reload),
# keys.* + reflex.ptt (hotkey reconcile), audio.input_device (mic reconcile), volumes/toggles
# (audio.apply_settings), listen.* (listener reconcile).
RESTART_REQUIRED: frozenset[str] = frozenset({
    "audio.enabled", "audio.mix_sample_rate", "dev.mock", "ui.host", "ui.port",
})
# Top-level config sections that apply LIVE. Single source of truth paired with RESTART_REQUIRED:
# the drift-guard unit test asserts every settings_schema key falls under LIVE_SECTIONS ∪
# RESTART_REQUIRED, so a NEW setting in an unclassified section fails the test until it's placed.
LIVE_SECTIONS: tuple[str, ...] = (
    "llm", "openai", "gemini", "ollama", "tts", "edge", "azure", "openai_tts",
    "cartesia", "anthropic", "elevenlabs", "piper", "router", "web_search",
    "conversation", "keys", "listen", "whisper", "personality", "crew", "elite",
    "proactive", "route", "nav", "star_systems", "search", "route_plan",
    "neutron_plan", "riches_plan", "keybinds", "macros", "honk", "reflex",
    "comms_send", "audio", "music", "hud",
)


def _section_changed(before: dict, cfg: dict, sections: tuple[str, ...]) -> bool:
    """True if any of the named top-level config sub-trees differs between the pre-merge
    snapshot and the live config — the section-granularity rebuild trigger (decision #3)."""
    return any(before.get(s) != cfg.get(s) for s in sections)


class _LatencyWatchdog:
    """Background timer for the >Ns latency heads-up (issue #97). If a turn goes `seconds` without
    a reply in hand, it fires ONCE and calls `speak` (the normal voice path, or a logged line in
    text-only) so the Commander isn't left in dead silence during a slow/retrying provider call.
    Disarmed the moment the reply arrives or the turn is cancelled.

    Thread-safe and fail-soft: `_fire` (Timer thread) and `disarm` (worker thread) coordinate under
    a lock so the interim line speaks at most once and never after disarm; a speak failure degrades
    to nothing (the reason is already logged). Never blocks or crashes the turn."""

    def __init__(self, seconds: float, *, speak, cancel: threading.Event, log) -> None:  # noqa: ANN001
        self._seconds = seconds
        self._speak = speak
        self._cancel = cancel
        self._log = log
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._spent = False  # True once the line has fired OR the watchdog was disarmed

    def arm(self) -> "_LatencyWatchdog":
        if self._seconds and self._seconds > 0:
            self._timer = threading.Timer(self._seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()
        return self

    def _fire(self) -> None:
        with self._lock:
            if self._spent or self._cancel.is_set():
                return
            self._spent = True  # claim the single interim slot before releasing the lock
        try:
            self._log(f"provider slow: no reply after {self._seconds:.0f}s — speaking interim status")
            self._speak()
        except Exception:  # noqa: BLE001 — interim reassurance is best-effort, never crash the turn
            pass

    def disarm(self) -> None:
        with self._lock:
            self._spent = True
        if self._timer is not None:
            self._timer.cancel()


class App:
    def __init__(
        self,
        cfg: dict | None = None,
        *,
        bus: EventBus | None = None,
        llm: LLMProvider | None = None,
        tts: TTSProvider | None = None,
        stt: STTProvider | None = None,
    ) -> None:
        # Composition root: build the real providers from config via the factory,
        # unless the caller injects them (unit tests pass fakes — DESIGN §9). The
        # factory returns fakes on its own when dev-mode mock is enabled, so a
        # single code path covers real, mock, and injected-fake runs.
        self.cfg = cfg if cfg is not None else load_config()
        self.overrides = load_overrides()
        self.mock = mock_enabled(self.cfg)
        self.bus = bus or EventBus()
        # C9 audio layer: when [audio].enabled, ONE BusMixer owns the device and ALL playback
        # (COVAS speech, cues, comms, chatter, SFX, music) routes through it. Off by default ->
        # the legacy direct playback path, byte-for-byte unchanged. Built device-free here; the
        # device is opened in start(). Never built under mock (fakes make no sound).
        self.mixer = None
        self.audio = None
        if self.cfg.get("audio", {}).get("enabled") and not self.mock:
            try:
                from .mixer import BusMixer
                self.mixer = BusMixer(self.cfg)
            except Exception as e:  # noqa: BLE001 — a mixer failure falls back to legacy playback
                self.mixer = None
                print(f"Audio mixer init failed ({e}); using direct playback.", flush=True)
        self.cues = CuePlayer(self.cfg, mixer=self.mixer)
        self.recorder = Recorder(self.cfg)
        # Hands-free continuous listening (issue #63). Off by default ([listen].mode = "ptt");
        # when "continuous", a VAD mic listener is started in start() and reconciled live on a
        # settings change. Built lazily so PTT-only runs (and the offline tests) never touch it.
        self.listener: VadListener | None = None
        if self.mock:
            print("Dev mock ON — LLM/TTS/STT are fakes; zero API calls, zero cost.", flush=True)
        elif stt is None:
            print("Loading Whisper model (first run may download it)...", flush=True)
        self.stt = stt or make_stt(self.cfg)
        self.tts = tts or make_tts(self.cfg, mixer=self.mixer)
        self.llm = llm or make_llm(self.cfg)
        # Text-only mode (I3): ElevenLabs is the only packaged TTS and it needs a key. With none,
        # the app runs text-only — a supported path (decision #2), not a failure. Detect it once
        # so the voice loop skips TTS quietly instead of raising FileNotFoundError every turn.
        from .firstrun import text_only_mode
        self.text_only = text_only_mode(self.cfg, mock=self.mock, tts_injected=tts is not None)

        # Capabilities register the tools they expose to the LLM. The checklist is
        # one; ED-context and keybinds will be others (DESIGN §3.3). Present only
        # when a checklist file is configured, matching the prior tool-gating.
        self.checklist = Checklist(self.cfg["checklist"]["file"])
        # Capability/token tiering (issue #84): resolve the optimization level ONCE at startup —
        # auto-selected from the provider (a low-TPM free tier like Groq-free -> Minimal so the
        # ~10K-token tool set can't blow the TPM budget) or a manual override. Stable for the whole
        # session so the tool set — and the prompt cache — stay warm (v2 per-turn gating is out of
        # scope). Filters registry.tools() before stream_reply and gates the background LLM paths.
        self.tier_level = tiering.resolve_level(self.cfg)
        self.registry = CapabilityRegistry()
        # Help is first-class and always on: it registers ITSELF so "what can you do" always
        # has one honest answer, and it projects the other capabilities' help metadata (it
        # holds the registry live, so capabilities registered later still show up). Templated
        # only — no LLM in the help path (Search Prompt 1).
        from .capabilities.help_capability import HelpCapability
        self.help = HelpCapability(self.registry,
                                   log=lambda m: self._log("help", m))
        self.registry.register(self.help)
        if self.cfg.get("checklist", {}).get("file"):
            # on_change publishes a `checklist` event on every voice/tool CRUD so a live
            # Checklist page reflects it in place instead of going stale until reload (#82).
            self.registry.register(
                ChecklistCapability(self.checklist, on_change=self.bus.publish))

        # Settings-by-voice (Prompt N2): change any setting spoken aloud, projected from the
        # SAME schema the web page uses so the two can't drift. Always on, like help — it
        # reads/writes the live config through update_settings, validating against the schema.
        from .capabilities.settings_capability import SettingsCapability
        self.settings_cap = SettingsCapability(
            get_value=lambda s: schema.get_value(self.cfg, s),
            apply_patch=self.update_settings,
            options_for=self._settings_option_pairs,
            log=lambda m: self._log("settings", m))
        self.registry.register(self.settings_cap)

        # "Copy that to my clipboard" (N11): one LLM-native tool — the model resolves what
        # "that" refers to from the conversation and passes the exact text. Always on, like
        # help/settings: local, harmless, no config.
        from .capabilities.clipboard_capability import ClipboardCapability
        self.clipboard_cap = ClipboardCapability(log=lambda m: self._log("clipboard", m))
        self.registry.register(self.clipboard_cap)

        # "What version are you?" (I7): report the running app version by voice, read from the
        # single-source-of-truth covas/__version__.py. Always on, like help/settings/clipboard
        # — local and harmless. Checking FOR updates stays a control-panel action, never a
        # voice command (INSTALLER_DESIGN.md decision #5).
        from .capabilities.version_capability import VersionCapability
        self.version_cap = VersionCapability(log=lambda m: self._log("version", m))
        self.registry.register(self.version_cap)

        # Grounded ship specifications (issue #83): answer "what can a Type-8 carry / what pad
        # does a Mandalay need" from a bundled, refreshable dataset keyed to the SAME canonical
        # names ships.py resolves — so newer hulls (Python Mk II, Corsair, Cobra Mk V, …) get
        # real numbers instead of training-cutoff guesses. Always on, like help/version: pure,
        # offline, no network (the bundled roster already covers current hulls; a resolved hull
        # with no bundled spec is spoken as "no data, web-search" rather than confabulated).
        from .capabilities.ship_spec_capability import ShipSpecCapability
        self.ship_spec = ShipSpecCapability(log=lambda m: self._log("ship_spec", m))
        self.registry.register(self.ship_spec)

        # Game-data freshness (issue #101): "how current is your ship/game data?" answered from
        # the bundled dataset manifest (sources + generation dates) — the honest companion to the
        # ship_spec "no data yet, web-search" path. Always on: pure, offline, no network.
        from .capabilities.game_data_status_capability import GameDataStatusCapability
        self.game_data_status = GameDataStatusCapability(
            log=lambda m: self._log("game_data_status", m))
        self.registry.register(self.game_data_status)

        # Elite Dangerous monitoring (DESIGN §5). Opt-in ([elite].enabled, off by
        # default). When on, two daemon watchers tail ED's journal + Status.json,
        # publishing events on the bus and updating a shared context the ED-context
        # capability references. Watchers publish only; they never drive the loop.
        self.ed_ctx = None
        self._ed_watchers: list = []
        # Location & carrier commands (N3) are registered by ED monitoring (they read the
        # journal), so this must be set BEFORE _start_ed_monitoring, not in the later
        # capability block — otherwise it would clobber the assignment back to None.
        self.carriers = None
        # Community Goals (N6) — also registered by ED monitoring (journal-primary).
        self.cg = None
        if self.cfg.get("elite", {}).get("enabled"):
            self._start_ed_monitoring()

        # Auto persona->voice pairing (issue #96): computed in the BACKGROUND at startup and applied
        # on persona selection. `_voice_pairings` is {persona_name(lower) -> voice_id}; `_voice_names`
        # maps a voice_id back to its display name for the applied `voice_name`. `_applying_persona_
        # voice` guards the reconcile re-entry when WE apply a paired voice via update_settings.
        self._voice_pairings: dict[str, str] = {}
        self._voice_names: dict[str, str] = {}
        self._applying_persona_voice = False

        self.history: list[dict] = []
        self.active_cancel: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.ptt_held = False
        self._ptt_t0 = 0.0  # key-down time, for tap-vs-hold detection
        # Second PTT for the Tier-2 reflex FAST PATH (issue #38): a capture on this key is spotted
        # locally against the fixed phrase vocabulary and, on a hit, fires the reflex WITHOUT the
        # LLM (routing through the same #36 guard/executor). Default unbound -> the hook never
        # installs, so the main PTT + conversation path are untouched.
        self.reflex_held = False
        # Resolved PTT/cancel/reflex scan-code SETS (issue #90). Populated by _resolve_hotkeys()
        # in start(); on_key reads these in place so a [keys]/[reflex].ptt change applies LIVE via
        # _reconcile_hotkeys() with no re-hook. Empty until start() (a pre-start settings change
        # just re-resolves into empty sets and start() resolves them for real).
        self._ptt_codes: set[int] = set()
        self._cancel_codes: set[int] = set()
        self._reflex_codes: set[int] = set()
        # An [audio].input_device change that arrives WHILE a PTT/reflex capture is in flight is
        # deferred (issue #90 review): rebuilding the Recorder mid-capture would strand the open
        # input stream and drop the utterance. This flag defers the rebuild to the capture boundary.
        self._recorder_dirty = False
        self.state = "Idle"
        # Armed for the duration of a USER (PTT) turn, so the "thinking" bed (issue #5) fills the
        # wait for those turns only — never a proactive callout (which has no "did it hear me" gap).
        self._bed_armed = False
        self._quit = threading.Event()

        # Proactive callouts (DESIGN §5): the companion initiates speech on notable ED
        # events (arrival, mission complete, low fuel, near-death) without a PTT press.
        # Opt-in ([proactive].enabled, off by default). The _proactive_lock serializes the
        # idle-claim so a callout can never start on top of an in-progress user turn.
        self.proactive = None
        # Keybind automation (DESIGN §6): the companion presses ONE ship control (toggle
        # landing gear) on request, behind a safety layer (confirmation, allowlist, combat
        # guard, hard abort). Opt-in ([keybinds].enabled, off by default).
        self.keybinds = None
        # Auto-honk (N5): fire the Discovery Scanner on arrival in a new system. Opt-in
        # ([honk].enabled, off by default), combat-gated. Shares the keybind executor + binds.
        self.honk = None
        # Tier-2 combat reflexes (DESIGN §6, issue #36): a SEPARATE, combat-PERMISSIVE policy —
        # fire chaff (and later heat sink / shields / boost) ONLY while under fire, the inverse
        # of the Tier-1 combat guard. Opt-in ([reflex].enabled, off by default); shares the
        # keybind executor + binds so the one hard abort releases every held key.
        self.reflex = None
        # Ambient auto-reflex layer (#37): fires the same reflexes automatically off Status/journal
        # thresholds (no voice), behind the same combat-permissive guard + a per-reflex cooldown.
        # Opt-in per reflex ([reflex.auto.<name>].enabled, off by default).
        self.auto_reflex = None
        # Send in-game comms text by voice (issue #49): compose a message, read it back, and
        # send on confirm (local/wing/squadron/direct). Outward-facing (other Commanders see
        # it), so it's behind a MANDATORY read-back-before-send gate — no un-confirmed sends.
        # Opt-in ([comms_send].enabled, off by default); shares the keybind executor + binds.
        self.comms = None
        # Custom macros (issue #50) — the Commander AUTHORS named, triggerable macros by voice/UI,
        # validated against the action/trigger registry (anti-hallucination), persisted, and run
        # through the SAME executor + guard + hard abort as [keybinds]. Opt-in ([macros].enabled,
        # off by default).
        self.macros = None
        # Shared scancode executor + parsed .binds, built once and reused by keybinds and
        # auto-honk so a hard abort releases keys held by either (and the .binds file is parsed
        # a single time). Lazily populated by the helpers below.
        self._shared_executor = None
        self._binds_cache: dict | None = None
        # Shared hard-abort flag so ONE "abort" stops a running sequence started by EITHER the
        # keybind capability or a custom macro (they share the executor too). Created once here.
        self._keybind_abort = threading.Event()
        # Find-closest-module: resolve a module by voice (offline taxonomy), confirm, then
        # find the nearest station selling it via Spansh + copy the system to the clipboard.
        # Opt-in ([nav].enabled, off by default).
        self.nav = None
        # Find-closest-ship: resolve a ship by voice (offline roster), then find the nearest
        # station selling it via Spansh + copy the system to the clipboard. Shares [nav].
        self.ship_nav = None
        # Route callouts (N4): proactive heads-ups while flying a plotted route (scoopable
        # star, jumps remaining, arrival). Opt-in ([route].enabled, off by default).
        self.route = None
        # Companion HUD (issue #47): a transparent always-on-top 2D overlay of the voice-loop
        # state + checklist step + route progress. Off by default ([hud].enabled); the
        # capability is ALWAYS registered so the toggle (Settings/voice) works live, but the
        # window is created only when enabled and only when a display is available.
        self.hud = None
        # Persistent-memory CAPTURE (issue #60): a self-contained capability that captures
        # curated journal milestones off the bus and exposes a 'remember that' store tool the
        # LLM calls in-turn. Store-only — recall (#61) extends it. Opt-in ([memory].enabled).
        self.memory = None
        self._proactive_lock = threading.Lock()
        self._pump: threading.Thread | None = None
        self._pump_q: queue.Queue | None = None
        self._pump_stop = threading.Event()

        self._logf = self._open_log()
        self._log("system", _cost_summary(self.cfg, self.mock))
        self._log("system", tiering.describe_level(self.cfg))
        if self.text_only:
            self._log("system", "No ElevenLabs key — running in text-only mode; "
                                "COVAS replies appear as text in the log (add a key in Settings "
                                "for voice).")
        if self.cfg.get("proactive", {}).get("enabled"):
            self._start_proactive()
        if self.cfg.get("route", {}).get("enabled"):
            self._start_route()
        if self.cfg.get("keybinds", {}).get("enabled"):
            self._start_keybinds()
        if self.cfg.get("honk", {}).get("enabled"):
            self._start_honk()
        if self.cfg.get("reflex", {}).get("enabled"):
            self._start_reflex()
        if self.cfg.get("comms_send", {}).get("enabled"):
            self._start_comms()
        if self.cfg.get("macros", {}).get("enabled"):
            self._start_macros()
        if self.cfg.get("nav", {}).get("enabled"):
            self._start_nav()
            self._start_ship_nav()
        if self.cfg.get("star_systems", {}).get("enabled"):
            self._start_system_search()
        if self.cfg.get("search", {}).get("enabled"):
            self._start_searches()
        if self.cfg.get("bodies", {}).get("enabled"):
            self._start_bodies()
        if self.cfg.get("route_plan", {}).get("enabled"):
            self._start_route_plan()
        if self.cfg.get("neutron_plan", {}).get("enabled"):
            self._start_neutron_plan()
        if self.cfg.get("riches_plan", {}).get("enabled"):
            self._start_riches_plan()
        if self.cfg.get("mining_helper", {}).get("enabled"):
            self._start_mining_helper()
        if self.cfg.get("memory", {}).get("enabled"):
            self._start_memory()
        # Companion HUD (issue #47) — always wired so a live toggle (Settings/voice) can bring
        # it up; the window itself stays off until [hud].enabled and a display are both present.
        self._start_hud()
        # C9: compose the audio layer once the mixer, providers, and ED context all exist.
        if self.mixer is not None:
            self._start_audio_layer()
        # Auto-pair a default voice per persona (issue #96) — off the hot path, never blocks startup.
        self._start_voice_pairing()

    # ---- Audio layer (C1-C8 composition, C9) ------------------------------
    def _start_audio_layer(self) -> None:
        """Build the AudioLayer over the shared mixer and register the voice-control capability
        (which also forwards bus events to the layer). Fail soft — a startup problem just leaves
        the ambient layer off; COVAS speech still routes through the mixer. Needs the event pump
        so comms/chatter/interdiction/music react to journal events."""
        try:
            from .config import data_dir
            from .mixer import AudioControlsCapability, AudioLayer, ensure_skeleton, load_content
            # Drop-in content (C11): ensure the folder skeleton (idempotent) then scan it, so a
            # dropped-in file joins the cues with no code/config edits. Fail-soft. The root is the
            # writable data dir (project root in a source run, %APPDATA%\COVAS++ when frozen);
            # [audio].content_root overrides it (a seam so tests don't touch the repo).
            content_root = self.cfg.get("audio", {}).get("content_root") or data_dir()
            try:
                ensure_skeleton(content_root)
            except Exception:  # noqa: BLE001 — skeleton creation must never block startup
                pass
            content = load_content(content_root)
            cheap = Router.from_cfg(self.cfg).cheap_route(None).model
            self.audio = AudioLayer(
                self.cfg, self.mixer, self.tts,
                ed_ctx=self.ed_ctx, llm=self.llm, cheap_model=cheap,
                cast_synth=self._build_cast_synth(), content=content,
                # Tiering (#84): the level gates the two LLM-generated background paths — below Full
                # these fall back to canned chatter / verbatim comms (no background LLM call).
                allow_chatter_flavor=self.tier_level.chatter_flavor,
                allow_comms_variants=self.tier_level.comms_variants,
                log=lambda m: self._log("audio", m))
            self.registry.register(
                AudioControlsCapability(self.audio, log=lambda m: self._log("audio", m)))
            self._start_event_pump()
            # Fetch the (famous-filtered) ElevenLabs voice list off the hot path and rebuild the
            # cast's exclusions when it lands — never block startup on a network call.
            threading.Thread(target=self._refresh_cast_exclusions, name="cast-exclusions",
                             daemon=True).start()
            if self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    "Audio layer ON (bus mixer), but ED monitoring is OFF — no game events to "
                    "drive comms/chatter/music."})
            else:
                self.bus.publish({"type": "log", "who": "system",
                                  "text": "Audio layer ON (bus mixer)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.audio = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Audio layer failed to start: {e}"})

    def _build_cast_synth(self):
        """The C10 cast synth router: ElevenLabs for EL/persona voices, local Piper models (cached)
        for the cast pool. EL synth reuses the app's own provider (which keeps a fake/mock run
        offline); only when the main provider is REAL Piper do we build a dedicated ElevenLabs
        provider for EL voices. Either backend may be absent -> that voice fails soft to silence."""
        from .mixer import CastSynth

        el_synth = None
        try:
            from .providers.elevenlabs_tts import ElevenLabsTTS
            from .providers.piper_tts import PiperTTS
            if isinstance(self.tts, PiperTTS):     # Piper is the main voice -> a separate EL cast
                el_prov = ElevenLabsTTS(self.cfg)
                el_synth = lambda text, vid: el_prov.synth_pcm(text, vid)  # noqa: E731
            else:                                   # EL main, or a fake/mock -> reuse it (offline-safe)
                el_synth = self.tts.synth_pcm
        except Exception:  # noqa: BLE001 — no EL available; EL voices fall to silence
            el_synth = None
        cs = CastSynth(el_synth=el_synth, piper_loader=self._load_piper_voice,
                       log=lambda m: self._log("audio", m))
        self._register_edge_cast(cs)
        self._register_azure_cast(cs)
        self._register_openai_cast(cs)
        return cs

    def _register_edge_cast(self, cast_synth) -> None:  # noqa: ANN001 — a CastSynth
        """Register the FREE Edge (edge-tts) provider as a cast-eligible backend (issue #15), so any
        NPC/comms/chatter role can use it without touching CastSynth. The cast Edge provider has NO
        fallback — a broken endpoint fails soft to SILENCE for a background line (CastSynth catches
        the error), never to COVAS's own voice. Fail-soft: if edge-tts isn't importable we simply
        don't register, and an 'edge' voice degrades to silence."""
        try:
            from .providers.edge_tts import EdgeTTS
            edge = EdgeTTS(self.cfg)
            cast_synth.registry.register(
                "edge", lambda text, ref: edge.synth_pcm(text, ref or None))
        except Exception as e:  # noqa: BLE001 — optional provider; never block the cast
            self._log("audio", f"Edge cast provider unavailable: {e}")

    def _register_azure_cast(self, cast_synth) -> None:  # noqa: ANN001 — a CastSynth
        """Register official Azure Neural TTS as a cast-eligible backend (issue #17) — the reliable,
        free-tier sibling of Edge. Any NPC/comms/chatter role can use it. Fail-soft: a synth error
        (no key, service down) is caught by CastSynth and the voice degrades to silence."""
        try:
            from .providers.azure_tts import AzureTTS
            azure = AzureTTS(self.cfg)
            cast_synth.registry.register(
                "azure", lambda text, ref: azure.synth_pcm(text, ref or None))
        except Exception as e:  # noqa: BLE001 — optional provider; never block the cast
            self._log("audio", f"Azure cast provider unavailable: {e}")

    def _register_openai_cast(self, cast_synth) -> None:  # noqa: ANN001 — a CastSynth
        """Register an OpenAI-compatible TTS backend as cast-eligible (issue #16) — a cheap cloud
        supplemental cast voice. Fail-soft: a synth error (no key, service down) is caught by
        CastSynth and the voice degrades to silence."""
        try:
            from .providers.openai_tts import OpenAITTS
            oai = OpenAITTS(self.cfg)
            cast_synth.registry.register(
                "openai", lambda text, ref: oai.synth_pcm(text, ref or None))
        except Exception as e:  # noqa: BLE001 — optional provider; never block the cast
            self._log("audio", f"OpenAI cast provider unavailable: {e}")

    def _load_piper_voice(self, model_path: str):
        """Load a Piper model as a cast voice (lazy, one per path). Returns an object with
        synth_pcm(text) -> (pcm, sr). Raises if Piper/the model isn't available (CastSynth
        catches it and degrades to silence)."""
        from .providers.piper_tts import PiperTTS
        cfg = dict(self.cfg)
        cfg["piper"] = {"model": model_path}
        return PiperTTS(cfg)

    def _refresh_cast_exclusions(self) -> None:
        """Background: fetch the famous-filtered ElevenLabs voice list and rebuild the cast so a
        ™/unusable voice is dropped from the pool. Fail-soft — the cast works without it."""
        if self.text_only:
            return  # no ElevenLabs key: nothing to fetch, and the call would just error
        try:
            from . import elevenlabs as el
            voices = el.list_voices(self.cfg)
            if self.audio is not None:
                self.audio.rebuild_cast(el_voices=voices)
        except Exception as e:  # noqa: BLE001 — best-effort; no filtering if the API is unreachable
            self._log("audio", f"cast voice-exclusion refresh skipped: {e}")

    # ---- Auto persona->voice pairing (issue #96) --------------------------
    def _voice_pairing_allowed(self) -> bool:
        """Gate the ONE background pairing call: only when it's opted in, the active TTS is
        ElevenLabs with a key, and the tiering level (#84) permits a background call. A lean/
        constrained level (proactive off) SKIPS it — the current default voice is kept. Consulting
        `tier_level.proactive` reuses #84's 'may I make a background call' axis (on at Full/Standard,
        off at Lean/Minimal/Bare) rather than inventing a new flag."""
        if not (self.cfg.get("personality", {}) or {}).get("auto_voice_pairing", True):
            return False
        if self.text_only:            # no ElevenLabs key -> no catalog, nothing to pair
            return False
        if (self.cfg.get("tts", {}) or {}).get("provider") != "elevenlabs":
            return False
        if self.llm is None or not self.tier_level.proactive:
            return False
        return True

    def _start_voice_pairing(self) -> None:
        """Kick off the background pairing thread (never blocks startup). Gated by
        `_voice_pairing_allowed`; skipped quietly otherwise."""
        if not self._voice_pairing_allowed():
            return
        threading.Thread(target=self._pair_persona_voices, name="voice-pairing",
                         daemon=True).start()

    def _pair_persona_voices(self) -> None:
        """Background worker: pair a default voice with each PRE-BUILT persona via one cheap-tier,
        one-time (cached) LLM call, then apply it to the current persona if it has no explicit
        voice. Fail-soft throughout — any failure just leaves the current default voice in place."""
        try:
            from . import elevenlabs as el
            from . import personality as persona_mod
            from . import voice_pairing as vp
            presets = [p for p in persona_mod.list_personas(self.cfg)
                       if p.get("source") == "preset"]
            if not presets:
                return
            voices = el.list_voices_detailed(self.cfg)
            if not voices:
                return
            cheap = Router.from_cfg(self.cfg).cheap_route(None).model
            gen = vp.make_pairing_generator(self.llm, model=cheap)
            result = vp.pair_voices(presets, voices, gen,
                                    cache_path=vp.default_cache_path(self.cfg),
                                    log=lambda m: self._log("voice", m))
            if result is None or not result.mapping:
                return
            self._voice_pairings = {k.strip().lower(): v for k, v in result.mapping.items()}
            self._voice_names = {v["voice_id"]: v.get("name", "") for v in voices}
            self._log("voice", f"persona voices paired ({'cache' if result.from_cache else 'fresh'}): "
                               f"{len(self._voice_pairings)}")
            # If a persona is already selected and has no explicit voice, dress it now.
            cur = str((self.cfg.get("personality", {}) or {}).get("persona") or "").strip()
            if cur:
                self._apply_persona_voice(cur)
        except Exception as e:  # noqa: BLE001 — pairing is best-effort; never crash/block the app
            self._log("voice", f"voice pairing skipped: {e}")

    def _persona_explicit_voices(self) -> dict:
        """The per-persona EXPLICIT voice choices ([personality].persona_voices) the user has made —
        these ALWAYS win over an auto pairing and are never overwritten."""
        return (self.cfg.get("personality", {}) or {}).get("persona_voices", {}) or {}

    def _remember_persona_voice(self, persona: str, voice_id: str, voice_name) -> None:  # noqa: ANN001
        """Record that the user EXPLICITLY chose `voice_id` for `persona` (a manual voice change while
        that persona is active), persisted to overrides so it survives a restart and always wins."""
        persona = str(persona or "").strip()
        if not persona or not voice_id:
            return
        patch = {"personality": {"persona_voices": {persona: str(voice_id)}}}
        deep_merge(self.cfg, patch)
        deep_merge(self.overrides, patch)
        save_overrides(self.overrides)
        self._log("voice", f"remembered explicit voice for persona {persona!r}")

    def _apply_persona_voice(self, persona: str) -> None:
        """Apply the paired default voice for `persona` — UNLESS the user has set an explicit voice
        for it (which always wins). No-op when TTS isn't ElevenLabs, nothing is paired, or the voice
        already matches. Routed through update_settings (persist + live TTS reload); a re-entry guard
        stops the resulting voice change from being mis-recorded as an explicit user choice."""
        if self._applying_persona_voice:
            return
        if (self.cfg.get("tts", {}) or {}).get("provider") != "elevenlabs":
            return
        from . import voice_pairing as vp
        target = vp.voice_for_persona(self._persona_explicit_voices(), self._voice_pairings, persona)
        if not target:
            return
        el = self.cfg.get("elevenlabs", {}) or {}
        if str(el.get("voice_id") or "") == target:
            return  # already on the right voice
        patch = {"elevenlabs": {"voice_id": target}}
        name = self._voice_names.get(target)
        if name:
            patch["elevenlabs"]["voice_name"] = name
        self._applying_persona_voice = True
        try:
            self.update_settings(patch)
            self._log("voice", f"persona {persona!r} -> paired voice {name or target}")
        finally:
            self._applying_persona_voice = False

    def _reconcile_persona_voice(self, before: dict) -> None:
        """On a settings change (issue #96): if the user changed the VOICE while staying on a persona,
        remember it as that persona's explicit choice; if the PERSONA changed, dress it in its paired
        (or explicit) voice. Skipped during our own apply (the re-entry guard). Fail-soft."""
        if self._applying_persona_voice:
            return
        try:
            pers = self.cfg.get("personality", {}) or {}
            if not pers.get("enabled"):
                return
            now_name = str(pers.get("persona") or "").strip()
            before_name = str((before.get("personality") or {}).get("persona") or "").strip()
            now_voice = str((self.cfg.get("elevenlabs", {}) or {}).get("voice_id") or "")
            before_voice = str((before.get("elevenlabs") or {}).get("voice_id") or "")
            persona_changed = now_name.lower() != before_name.lower()
            voice_changed = now_voice != before_voice
            if voice_changed and not persona_changed and now_name \
                    and (self.cfg.get("tts", {}) or {}).get("provider") == "elevenlabs":
                self._remember_persona_voice(
                    now_name, now_voice, (self.cfg.get("elevenlabs", {}) or {}).get("voice_name"))
            elif persona_changed and now_name:
                self._apply_persona_voice(now_name)
        except Exception as e:  # noqa: BLE001 — a reconcile glitch must never crash the loop
            self._log("voice", f"persona-voice reconcile failed: {e}")

    # ---- Elite Dangerous monitoring (DESIGN §5) ---------------------------
    def _start_ed_monitoring(self) -> None:
        """Build the shared context + ED-context capability and start the journal/status
        watchers. Fail soft: a missing directory or import problem must not stop the app
        from starting — ED monitoring just stays dark until the next run."""
        try:
            from .ed import (EDContext, JournalWatcher, StatusWatcher,
                             resolve_journal_dir, status_path)
            from .capabilities.ed_context_capability import EDContextCapability
            from .capabilities.on_foot_srv_capability import OnFootSrvCapability
            from .capabilities.engineers_capability import EngineersCapability
            from .capabilities.on_foot_engineering_capability import OnFootEngineeringCapability
            from .capabilities.loadout_capability import LoadoutCapability
            from .capabilities.blueprint_capability import BlueprintCapability
            from .capabilities.stored_capability import StoredCapability
            from .nav import copy as _nav_copy

            el = self.cfg.get("elite", {})
            jdir = resolve_journal_dir(self.cfg)
            self.ed_ctx = EDContext(recent_maxlen=int(el.get("recent_events_kept", 25)))
            self.registry.register(EDContextCapability(self.ed_ctx))
            # On-foot / SRV / exobiology read tools (#54): situational awareness in the modes
            # ED context was silent in. Same live EDContext, mode-specific read answers.
            self.registry.register(OnFootSrvCapability(self.ed_ctx))
            # Ship loadout & engineering (N9): reads the snapshot the journal watcher keeps
            # on EDContext. Registered with monitoring since that's its only data source.
            self.registry.register(LoadoutCapability(
                get_loadout=self.ed_ctx.loadout_snapshot,
                log=lambda m: self._log("loadout", m)))
            # Blueprint / material sourcing (#66): crosses the bundled engineering tables with the
            # live material inventory the journal watcher keeps on EDContext (the Materials event).
            # Registered with monitoring since the journal inventory is its only live data source.
            self.registry.register(BlueprintCapability(
                get_materials=self.ed_ctx.materials_snapshot,
                log=lambda m: self._log("blueprint", m)))
            # Stored ships & modules finder (issue #67): reads the StoredShips/StoredModules
            # snapshots the journal watcher keeps on EDContext. Copies a destination system to
            # the clipboard for a resolved remote ship/module (galaxy-map handoff).
            self.registry.register(StoredCapability(
                get_stored_ships=self.ed_ctx.stored_ships_snapshot,
                get_stored_modules=self.ed_ctx.stored_modules_snapshot,
                get_current_system=self._current_system,
                clipboard=_nav_copy,
                log=lambda m: self._log("stored", m)))
            # Engineers finder (#65): bundled reference table joined with live EngineerProgress
            # for journal-grounded unlock status. Copies an engineer's system for plotting.
            self.registry.register(EngineersCapability(
                get_progress=self.ed_ctx.engineer_progress,
                get_current_system=lambda: self.ed_ctx.snapshot().get("system"),
                clipboard=_nav_copy,
                log=lambda m: self._log("engineers", m)))
            # On-foot (Odyssey suit/weapon) engineering (#73): bundled reference for suits,
            # weapons, modifications and the 13 on-foot engineers. Joins the SAME live
            # EngineerProgress event (on-foot engineers share it) for grounded unlock status.
            self.registry.register(OnFootEngineeringCapability(
                get_progress=self.ed_ctx.engineer_progress,
                get_current_system=lambda: self.ed_ctx.snapshot().get("system"),
                clipboard=_nav_copy,
                log=lambda m: self._log("on_foot_engineering", m)))
            self._start_carriers(jdir)
            self._start_cg(jdir)

            def _err(e: Exception) -> None:  # watcher-thread errors -> log, don't crash
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"ED watcher error: {e}"})

            self._ed_watchers = [
                JournalWatcher(jdir, self.bus, self.ed_ctx,
                               poll_interval=float(el.get("journal_poll_interval", 0.5)),
                               on_error=_err),
                StatusWatcher(status_path(jdir), self.bus, self.ed_ctx,
                              poll_interval=float(el.get("status_poll_interval", 1.0)),
                              on_error=_err),
            ]
            for w in self._ed_watchers:
                w.start()
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"ED monitoring ON — watching {jdir}"})
        except Exception as e:  # noqa: BLE001 — monitoring is optional; never block startup
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"ED monitoring failed to start: {e}"})

    def _stop_ed_monitoring(self) -> None:
        for w in self._ed_watchers:
            try:
                w.stop()
            except Exception:  # noqa: BLE001
                pass

    # ---- Location & carrier commands (N3) ---------------------------------
    def _start_carriers(self, jdir) -> None:
        """Register the location/carrier capability (copy current system, where's my fleet /
        squadron carrier). Called from ED monitoring since it reads the journal; fleet-carrier
        state is the live EDContext with a journal-scan fallback, and the squadron lookup goes
        through Spansh by the configured callsign. Fail soft — never blocks startup."""
        try:
            from .capabilities.location_capability import LocationCarrierCapability
            from .nav import (CarrierInfo, carrier_from_journals, copy as _nav_copy,
                              squadron_name_from_journals)

            def _fleet_carrier():
                # Prefer the live watcher state; fall back to a journal scan for a carrier the
                # current session hasn't seen jump yet.
                if self.ed_ctx is not None:
                    snap = self.ed_ctx.carrier_snapshot()
                    if snap["carrier_name"] or snap["carrier_callsign"] or snap["carrier_system"]:
                        return CarrierInfo(snap["carrier_name"], snap["carrier_callsign"],
                                           snap["carrier_system"], snap["carrier_pending_system"])
                return carrier_from_journals(jdir)

            self.carriers = LocationCarrierCapability(
                get_current_system=self._current_system,
                clipboard=_nav_copy,
                get_fleet_carrier=_fleet_carrier,
                get_squadron_name=lambda: squadron_name_from_journals(jdir),
                log=lambda m: self._log("carrier", m))
            self.registry.register(self.carriers)
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Location & carrier commands ON."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.carriers = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Location & carrier commands failed to start: {e}"})

    # ---- Community Goals (N6) ---------------------------------------------
    def _start_cg(self, jdir) -> None:
        """Register the Community-Goals capability. Journal-primary (works offline); an
        external Inara feed is added only when a key is configured. Fail soft — never blocks
        startup. The Inara key is a restart-level setting, so config is snapshotted here."""
        try:
            from . import firstrun
            from .capabilities.cg_capability import CGCapability
            from .cg import CGConfig, cg_from_journals, fetch_inara_goals
            from .nav import copy as _nav_copy
            from .search import RequestsHttp

            ccfg = CGConfig.from_cfg(self.cfg)
            # The Inara key now lives DPAPI-encrypted in InaraAPIKey.txt (issue #24); reading it here
            # also migrates any legacy inline `[cg].inara_api_key` off plaintext on first run.
            api_key = firstrun.inara_key(self.cfg) or ""
            use_feed = ccfg.source == "inara" and bool(api_key)
            fetch_external = None
            if use_feed:
                http = RequestsHttp()

                def fetch_external():   # stamp the Inara envelope timestamp per call
                    return fetch_inara_goals(http, api_key=api_key,
                                             timestamp=_dt.datetime.now().isoformat())

            self.cg = CGCapability(
                get_journal_goals=lambda: cg_from_journals(jdir),
                get_current_system=self._current_system,
                clipboard=_nav_copy,
                fetch_external=fetch_external,
                log=lambda m: self._log("cg", m))
            self.registry.register(self.cg)
            src = "feed: Inara" if use_feed else "journal-only (no Inara key)"
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Community Goals ON ({src})."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.cg = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Community Goals failed to start: {e}"})

    # ---- Proactive callouts (DESIGN §5) -----------------------------------
    def _start_proactive(self) -> None:
        """Build the proactive-callout capability and start the event pump that feeds bus
        events to capability on_event hooks. Fail soft: a startup problem just leaves
        callouts off. Proactive needs ED monitoring for its events — warn (don't fail) if
        that's not on, since the two are independently toggled."""
        try:
            from .capabilities.proactive_capability import (ProactiveCapability,
                                                            ProactivePolicy)
            policy = ProactivePolicy.from_cfg(self.cfg)
            self.proactive = ProactiveCapability(
                policy, self._speak_proactive,
                log=lambda reason: self._log("proactive", reason))
            self.registry.register(self.proactive)
            self._start_event_pump()
            if self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    "Proactive callouts ON, but ED monitoring is OFF — no events to react to."})
            else:
                self.bus.publish({"type": "log", "who": "system",
                                  "text": "Proactive callouts ON."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Proactive callouts failed to start: {e}"})

    def _start_event_pump(self) -> None:
        """Subscribe to the bus (live-only, no backlog replay) and fan each event out to
        capability on_event hooks on a dedicated daemon thread. A thread — not inline in
        the publisher — so slow handler work (a proactive LLM call) never blocks a watcher,
        and replay=False so stale startup events aren't delivered to a handler. Idempotent:
        proactive and route callouts both need the pump but there's only ever one."""
        if self._pump is not None:
            return
        self._pump_q = self.bus.subscribe(replay=False)
        self._pump = threading.Thread(target=self._pump_events, name="event-pump",
                                      daemon=True)
        self._pump.start()

    def _pump_events(self) -> None:
        while not self._pump_stop.is_set():
            try:
                event = self._pump_q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self.registry.dispatch_event(event)
            except Exception as e:  # noqa: BLE001 — one bad handler must not kill the pump
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"event pump error: {e}"})

    def _stop_event_pump(self) -> None:
        self._pump_stop.set()
        if self._pump_q is not None:
            try:
                self.bus.unsubscribe(self._pump_q)
            except Exception:  # noqa: BLE001
                pass

    def _speak_proactive(self, event_name: str, event: dict) -> bool:
        """Originate a spoken callout for an ED event, WITHOUT a PTT press. Returns True
        only if we actually started: we speak only when Idle, so a callout never interrupts
        an in-progress user turn — the Commander always has the floor. The line is generated
        on the cheap tier and spoken through the existing cancel path, so a PTT press
        mid-callout cancels it like any other utterance (on_ptt_down sets active_cancel)."""
        # Tiering second axis (issue #84): proactive callouts are LLM-generated background turns
        # the Commander never PTT'd for. The lean levels (Lean/Minimal/Bare) suppress them entirely
        # — return False WITHOUT claiming the turn or arming the cooldown, so no LLM call is spawned.
        if not self.tier_level.proactive:
            return False
        with self._proactive_lock:
            if self.state != "Idle":
                return False
            if self.worker is not None and self.worker.is_alive():
                return False
            cancel = threading.Event()
            self.active_cancel = cancel
            # Claim the turn synchronously (state off Idle) before releasing the lock, so a
            # near-simultaneous second event sees us as busy and doesn't also start.
            self.set_state("Thinking", "proactive")
            self.worker = threading.Thread(
                target=self._proactive_worker, args=(event_name, event, cancel), daemon=True)
            self.worker.start()
        return True

    def _proactive_worker(self, event_name: str, event: dict,
                          cancel: threading.Event) -> None:
        from .capabilities.proactive_capability import build_prompt
        # Turn-local provider binding (issue #90): one callout runs on one consistent LLM+TTS pair
        # (and text-only state) even if a hot-swap rebinds self.llm/self.tts mid-callout (decision #2).
        llm = self.llm
        tts = self.tts
        text_only = self.text_only
        try:
            if cancel.is_set():
                self.set_state("Idle")
                return
            summary = self.ed_ctx.summary() if self.ed_ctx is not None else None
            prompt = build_prompt(event, summary)
            # Cheap tier by design (DESIGN §5) — a callout is one sentence; small cap.
            cap = self.proactive.policy.cfg.max_tokens if self.proactive else None
            route = Router.from_cfg(self.cfg).cheap_route(cap)
            self._log("router", f"{route.model} max_tokens={route.max_tokens} — {route.reason}")

            def on_event(kind: str, data) -> None:  # noqa: ANN001
                if kind == "usage":
                    self._log_usage(data)

            reply = ""
            stream = llm.stream_reply(
                [{"role": "user", "content": prompt}], cancel, on_event,
                model=route.model, max_tokens=route.max_tokens)
            for kind, chunk in stream:
                if cancel.is_set():
                    break
                if kind == "text":
                    reply += chunk

            if cancel.is_set() or not reply.strip():
                self.set_state("Idle")
                return
            # Proactive lines are logged + spoken but NOT added to self.history — they're
            # ambient, so keeping them out avoids polluting the conversation and paying to
            # re-send them every following turn.
            self._log("COVAS", f"(proactive) {reply}")
            print(f"\n>> [proactive] {reply}")
            self.set_state("Speaking", "proactive")
            self._speak(reply, cancel, tts=tts, text_only=text_only)
            self.set_state("Idle")
        except Exception as e:  # noqa: BLE001 — a proactive failure must never crash the app
            self.set_state("Idle", f"proactive error: {e}")

    def _speak_proactive_line(self, text: str) -> bool:
        """Speak a PREDETERMINED proactive line (e.g. a route callout) through the same
        never-interrupt/cancel machinery as `_speak_proactive`, but WITHOUT the LLM — the
        text is deterministic and factual, so there's no reason to pay for or risk generation.
        Returns True only if we actually started (Idle, no active worker), so the caller can
        tell a spoken line from one skipped because the Commander had the floor."""
        text = (text or "").strip()
        if not text:
            return False
        with self._proactive_lock:
            if self.state != "Idle":
                return False
            if self.worker is not None and self.worker.is_alive():
                return False
            cancel = threading.Event()
            self.active_cancel = cancel
            self.set_state("Speaking", "route")
            self.worker = threading.Thread(
                target=self._proactive_line_worker, args=(text, cancel), daemon=True)
            self.worker.start()
        return True

    def _proactive_line_worker(self, text: str, cancel: threading.Event) -> None:
        try:
            if cancel.is_set():
                self.set_state("Idle")
                return
            # Ambient, like proactive callouts — logged + spoken but NOT added to history.
            self._log("COVAS", f"(route) {text}")
            print(f"\n>> [route] {text}")
            self._speak(text, cancel)
            self.set_state("Idle")
        except Exception as e:  # noqa: BLE001 — a route callout must never crash the app
            self.set_state("Idle", f"route error: {e}")

    # ---- Route callouts (DESIGN §5, N4) -----------------------------------
    def _start_route(self) -> None:
        """Build + register the route-callout capability and ensure the event pump is running.
        Fail soft: a startup problem just leaves route callouts off. Needs ED monitoring for
        its events (warn, don't fail, if that's off — the two are independently toggled)."""
        try:
            from .capabilities.route_capability import RouteCalloutCapability, RouteConfig
            from .ed import read_navroute, resolve_journal_dir

            rcfg = RouteConfig.from_cfg(self.cfg)
            jdir = resolve_journal_dir(self.cfg)
            # Route callouts honour the shared proactive mute ('stop the callouts') when
            # proactive is enabled; otherwise there's nothing muting them.
            is_muted = ((lambda: self.proactive.policy.muted) if self.proactive is not None
                        else (lambda: False))
            self.route = RouteCalloutCapability(
                rcfg,
                speak_line=self._speak_proactive_line,
                load_navroute=lambda: read_navroute(jdir),
                is_muted=is_muted,
                log=lambda m: self._log("route", m))
            self.route.prime()
            self.registry.register(self.route)
            self._start_event_pump()
            every = rcfg.every_n
            if self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    "Route callouts ON, but ED monitoring is OFF — no route events to react to."})
            else:
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Route callouts ON (jumps-remaining every {every})."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.route = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Route callouts failed to start: {e}"})

    # ---- Companion HUD (issue #47) ----------------------------------------
    def _start_hud(self) -> None:
        """Register the always-on HUD capability and ensure the event pump is running so it
        hears status/checklist/route/settings events. The capability keeps a pure HudModel and
        only opens a window when [hud].enabled AND a display are present — off by default, so
        this is inert until the Commander opts in (Settings page or 'turn the HUD on'). Fail
        soft: any wiring problem just leaves the HUD off; it must never block startup."""
        try:
            from .capabilities.hud_capability import HudCapability, HudModel, checklist_line
            from .capabilities.vr_hud import make_vr_view
            from .ed import read_navroute, resolve_journal_dir

            jdir = resolve_journal_dir(self.cfg)
            model = HudModel(
                checklist_provider=lambda: checklist_line(self.checklist),
                load_navroute=lambda: read_navroute(jdir),
                state=self.state,
            )
            # The VR overlay is a SECOND view over the same model — placement is read live from
            # config when the sink is (lazily) created, so it reflects the current settings.
            def _vr_factory(provider):
                return make_vr_view(provider, self._vr_hud_placement(),
                                    log=lambda m: self._log("hud", m))
            self.hud = HudCapability(
                model,
                is_enabled=self._hud_enabled,
                vr_is_enabled=self._vr_hud_enabled,
                vr_view_factory=_vr_factory,
                log=lambda m: self._log("hud", m))
            self.registry.register(self.hud)
            # Voice repositioning for the VR overlay (nudges + look-to-place). Reuses the HUD's
            # config + the app's live-apply settings path; pin reads the HMD gaze from the live
            # overlay. Registered even when the VR HUD is off — the tool just reports it's not up.
            from .capabilities.hud_placement_capability import HudPlacementCapability
            self.registry.register(HudPlacementCapability(
                get_hud=lambda: self.cfg.get("hud", {}),
                apply_patch=self.update_settings,
                pin=lambda: self.hud.pin_vr_here() if self.hud is not None else None,
                log=lambda m: self._log("hud", m)))
            # A SHOWN HUD (either surface) repaints from live bus events (status/checklist/route/
            # callout), so it needs the shared event pump — but only when actually enabled. The
            # toggle itself is driven directly (see _reconcile_hud), so a disabled HUD adds no
            # pump thread and can still be brought up live by voice/Settings.
            if self._hud_enabled() or self._vr_hud_enabled():
                self._start_event_pump()
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.hud = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Companion HUD failed to start: {e}"})

    def _hud_enabled(self) -> bool:
        return bool(self.cfg.get("hud", {}).get("enabled", False))

    def _vr_hud_enabled(self) -> bool:
        return bool(self.cfg.get("hud", {}).get("vr_enabled", False))

    def _vr_hud_placement(self):
        """Build the VR overlay placement from the live [hud] config (clamped to sane ranges).
        Every field is voice-adjustable and applies live via _reconcile_hud -> set_vr_placement."""
        from .capabilities.vr_hud import VrPlacement
        hud = self.cfg.get("hud", {})
        return VrPlacement.normalize(
            hud.get("vr_placement", "world"),
            hud.get("vr_width_m", 0.55),
            forward_m=hud.get("vr_distance_m", 1.30),
            up_m=hud.get("vr_offset_y_m", -0.12),
            offset_x_m=hud.get("vr_offset_x_m", 0.0),
            pitch_deg=hud.get("vr_pitch_deg", 0.0),
            curvature=hud.get("vr_curvature", 0.1),
            yaw_deg=hud.get("vr_yaw_deg", 0.0))

    def _reconcile_hud(self) -> None:
        """Bring the HUD surfaces up/down after a settings change, and start the event pump when
        either is newly enabled so a shown overlay has live data to repaint from. Directly
        invoked (not via the pump) so the toggle works even when no ambient feature was running."""
        if self.hud is None:
            return
        try:
            if self._hud_enabled() or self._vr_hud_enabled():
                self._start_event_pump()  # idempotent
            self.hud.reconcile()
            # Push the live placement so a Settings/voice change to distance / offset / pitch /
            # curvature / width repositions a SHOWN VR overlay immediately (no re-toggle).
            self.hud.set_vr_placement(self._vr_hud_placement())
        except Exception as e:  # noqa: BLE001 — a toggle glitch must not crash the loop
            self._log("hud", f"reconcile failed: {e}")

    # ---- Persistent memory capture (issue #60) ----------------------------
    def _start_memory(self) -> None:
        """Wire persistent memory (CAPTURE #60 + RECALL #61): register a capability that
        (a) captures curated journal milestones off the bus (deterministic describers — no LLM
        per event), (b) exposes a 'remember that' store tool the LLM calls in-turn, (c) exposes a
        'recall_memory' tool for explicit look-ups, and (d) provides `recall_block` so the worker
        loop can prepend relevant facts to a recall-referencing turn's USER message (cache-safe —
        never the system prompt). Capture dedups + caps the git-ignored file; recall is keyword/tag
        only (free, offline). Opt-in ([memory].enabled). Fail soft — any wiring problem just leaves
        memory off; it must never block startup."""
        try:
            from .capabilities.memory_capability import MemoryCapability
            from .memory import MemoryCapture, Retriever, store_from_config

            store = store_from_config(self.cfg)
            cap = int(self.cfg.get("memory", {}).get("cap", 500))
            capture = MemoryCapture(store, cap=cap, log=lambda m: self._log("memory", m))
            # Recall side (#61): keyword/tag retriever over the SAME store — embedder stays None
            # (the default free, offline path), so recall never costs money or touches the network.
            retriever = Retriever(store, embedder=None)
            self.memory = MemoryCapability(capture, retriever,
                                           log=lambda m: self._log("memory", m))
            self.registry.register(self.memory)
            # Journal-highlight capture rides the shared bus/event pump (live-only, so an
            # existing journal isn't re-captured on every launch — the watcher primes context
            # WITHOUT publishing). The 'remember that' tool works regardless of the pump.
            self._start_event_pump()
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Persistent memory ON (capture + recall)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.memory = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Memory failed to start: {e}"})

    # ---- Keybind automation (DESIGN §6) -----------------------------------
    def _start_keybinds(self) -> None:
        """Build the keybind capability: resolve + parse the active ED bindings, build the
        scancode executor, and register the capability behind its safety layer. Fail soft —
        a missing bindings file or a non-Windows host just leaves ship controls off; it must
        never block startup. The combat guard reads the live ED context snapshot (so keybinds
        needs [elite].enabled to positively confirm it's safe to act)."""
        try:
            from .capabilities.keybind_capability import KeybindConfig, KeybindCapability

            kcfg = KeybindConfig.from_cfg(self.cfg)
            binds = self._ed_binds()
            executor = self._key_executor()   # raises ExecutorError off-Windows -> caught below
            snapshot = ((lambda: self.ed_ctx.snapshot()) if self.ed_ctx is not None else None)
            self.keybinds = KeybindCapability(
                binds=binds, executor=executor, config=kcfg,
                status_snapshot=snapshot,
                abort_event=self._keybind_abort,   # shared with custom macros (#50)
                log=lambda msg: self._log("keybind", msg))
            self.registry.register(self.keybinds)

            # Report per-macro readiness so the manual test knows what's wired.
            for macro in self.keybinds._allowed_macros():
                if macro.steps:
                    # Sequence macro (#33): usable iff every key-pressing step is bound to a key.
                    missing = [s.action for s in macro.steps if s.action
                               and (binds.get(s.action) is None or not binds[s.action].usable)]
                    detail = (f"{macro.name} (sequence) READY" if not missing
                              else f"{macro.name} (sequence) UNUSABLE (bind: {', '.join(missing)})")
                else:
                    b = binds.get(macro.action)
                    if b is not None and b.usable:
                        detail = f"{macro.name} -> {b.key}"
                    else:
                        detail = f"{macro.name} UNUSABLE (no keyboard bind for {macro.action})"
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Keybind macro: {detail}"})
            guard = "on" if kcfg.combat_guard else "off"
            if kcfg.combat_guard and self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    "Keybinds ON but ED monitoring is OFF — combat guard can't verify safety, "
                    "so actions will be refused until [elite].enabled."})
            else:
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Keybinds ON (confirmation "
                                          f"{'on' if kcfg.require_confirmation else 'off'}, "
                                          f"combat guard {guard})."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.keybinds = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Keybinds failed to start: {e}"})

    def _ed_binds(self) -> dict:
        """Parse the active ED key bindings once, shared by keybinds + auto-honk. Returns {}
        (with a logged reason) if the .binds file can't be located/read, so a capability
        degrades to a clear 'bind it in-game' message instead of vanishing silently."""
        if self._binds_cache is None:
            from .keybinds import BindsError, load_binds
            try:
                self._binds_cache = load_binds(self.cfg)
            except BindsError as e:
                self._binds_cache = {}
                self.bus.publish({"type": "log", "who": "system", "text": f"Keybinds: {e}"})
        return self._binds_cache

    def _key_executor(self):
        """Build (once) the shared scancode executor used by both keybind actions and auto-honk,
        so a hard abort releases keys held by either. Raises ExecutorError off-Windows (the
        callers catch it and leave the feature off)."""
        if self._shared_executor is None:
            from .keybinds.executor import KeyExecutor
            self._shared_executor = KeyExecutor()
        return self._shared_executor

    # ---- Tier-2 combat reflexes (#36) -------------------------------------
    def _start_reflex(self) -> None:
        """Build the Tier-2 combat-reflex capability: parse the active ED bindings (shared),
        build the shared scancode executor, and register the capability behind the SEPARATE
        combat-permissive guard. Fail soft — a missing bindings file or a non-Windows host just
        leaves reflexes off; it must never block startup. The guard reads the live ED context
        snapshot (so it needs [elite].enabled to positively confirm you're IN danger before
        firing a reflex)."""
        try:
            from .capabilities.reflex_capability import (
                REFLEX_ACTIONS, ReflexCapability, ReflexConfig)

            rcfg = ReflexConfig.from_cfg(self.cfg)
            binds = self._ed_binds()
            executor = self._key_executor()   # raises ExecutorError off-Windows -> caught below
            snapshot = ((lambda: self.ed_ctx.snapshot()) if self.ed_ctx is not None else None)
            self.reflex = ReflexCapability(
                binds=binds, executor=executor, config=rcfg,
                status_snapshot=snapshot,
                log=lambda msg: self._log("reflex", msg))
            self.registry.register(self.reflex)

            # Report per-reflex readiness so the manual test knows what's wired.
            for r in self.reflex._allowed_reflexes():
                b = binds.get(r.action)
                if b is not None and b.usable:
                    detail = f"{r.name} -> {b.key}"
                else:
                    detail = f"{r.name} UNUSABLE (no keyboard bind for {r.action})"
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Reflex: {detail}"})
            if rcfg.combat_guard and self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    "Tier-2 reflexes ON but ED monitoring is OFF — the combat-permissive guard "
                    "can't confirm you're in danger, so reflexes will be refused until "
                    "[elite].enabled."})
            else:
                self.bus.publish({"type": "log", "who": "system", "text":
                    f"Tier-2 combat reflexes ON (combat-permissive guard "
                    f"{'on' if rcfg.combat_guard else 'off'}; allowlist: "
                    f"{', '.join(rcfg.allowlist) or 'empty'})."})

            # AMBIENT auto-reflex layer (#37): fire the same reflexes automatically off Status/
            # journal thresholds, no voice. Opt-in per reflex ([reflex.auto.<name>].enabled) and
            # off by default. Shares the binds/executor/snapshot + the combat-permissive guard.
            self._start_auto_reflex(binds, executor)
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.reflex = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Tier-2 reflexes failed to start: {e}"})

    def _start_auto_reflex(self, binds: dict, executor: object) -> None:
        """Build + register the ambient auto-reflex capability when opted in ([reflex.auto].
        enabled). Fail soft: a startup problem just leaves the automatic layer off — the verbal
        reflexes still work. Needs the event pump (it reacts to bus ed_events) and ED monitoring
        (for the trigger snapshot + the guard)."""
        from .capabilities.auto_reflex_capability import AutoReflexCapability, AutoReflexConfig
        from .capabilities.reflex_capability import REFLEX_ACTIONS

        acfg = AutoReflexConfig.from_cfg(self.cfg)
        if not acfg.enabled:
            return
        snapshot = ((lambda: self.ed_ctx.snapshot()) if self.ed_ctx is not None else None)
        self.auto_reflex = AutoReflexCapability(
            binds=binds, executor=executor, config=acfg,
            status_snapshot=snapshot,
            log=lambda msg: self._log("reflex", msg))
        self.registry.register(self.auto_reflex)
        self._start_event_pump()

        enabled = self.auto_reflex.enabled_reflexes()
        if not enabled:
            self.bus.publish({"type": "log", "who": "system", "text":
                "Auto-reflexes ON but no reflex is enabled — set [reflex.auto.<name>].enabled "
                "(heat_sink, chaff) to opt one in."})
            return
        for trig in enabled:
            b = binds.get(REFLEX_ACTIONS[trig.name].action)
            usable = b is not None and b.usable
            detail = (f"{trig.name} -> {b.key}" if usable
                      else f"{trig.name} UNUSABLE (no keyboard bind for "
                           f"{REFLEX_ACTIONS[trig.name].action})")
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Auto-reflex: {detail} ({trig.summary})"})
        if self.ed_ctx is None:
            self.bus.publish({"type": "log", "who": "system", "text":
                "Auto-reflexes ON but ED monitoring is OFF — no trigger events, and the guard "
                "can't confirm danger, so nothing will fire until [elite].enabled."})

    # ---- Send in-game comms (issue #49) -----------------------------------
    def _start_comms(self) -> None:
        """Build the comms-send capability: reuse the shared ED binds + scancode executor, wire
        the clipboard-paste text injector, and register it behind the read-back-before-send gate.
        Fail soft — a missing bindings file or a non-Windows host (no executor) just leaves comms
        off; it must never block startup. No combat/ED-monitoring dependency: the safety here is
        the mandatory read-back confirmation, not a game-state guard."""
        try:
            from .capabilities.comms_capability import CommsSendCapability, CommsSendConfig
            from .nav import clipboard

            ccfg = CommsSendConfig.from_cfg(self.cfg)
            binds = self._ed_binds()
            executor = self._key_executor()   # raises ExecutorError off-Windows -> caught below
            self.comms = CommsSendCapability(
                binds=binds, executor=executor, config=ccfg,
                copy=clipboard.copy,
                log=lambda msg: self._log("comms", msg))
            self.registry.register(self.comms)

            # Report readiness so the manual test knows what's wired: the open-comms bind and
            # each configured channel-select bind.
            ob = binds.get(ccfg.open_bind)
            if ob is not None and ob.usable:
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Comms: open box {ccfg.open_bind} -> {ob.key}"})
            else:
                self.bus.publish({"type": "log", "who": "system", "text":
                    f"Comms UNUSABLE (bind {ccfg.open_bind} to a key to open the chat box)."})
            for ch, token in ccfg.channel_binds.items():
                if not token:
                    continue
                b = binds.get(token)
                detail = (f"{ch} -> {b.key}" if b is not None and b.usable
                          else f"{ch} UNUSABLE (no keyboard bind for {token})")
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Comms channel: {detail}"})
            self.bus.publish({"type": "log", "who": "system", "text":
                "Comms send ON (read-back-before-send confirmation required)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.comms = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Comms send failed to start: {e}"})

    # ---- Custom macros (#50) ----------------------------------------------
    def _start_macros(self) -> None:
        """Build + register the custom-macro capability: the persisted spec store, the shared
        binds/executor/abort, and the allowlist provider the compiler validates against. Fail
        soft — a missing bindings file or non-Windows host just leaves authoring able to SAVE and
        VALIDATE macros (offline), degrading only the actual key-press at run time to a spoken
        'bind it in-game'. Ensures the event pump so triggered macros can auto-run. The combat
        guard reads the live ED snapshot (needs [elite].enabled to positively confirm safety)."""
        try:
            from .capabilities.keybind_capability import KeybindConfig
            from .capabilities.macro_capability import MacroCapability, MacroConfig
            from .macros.store import store_from_config

            mcfg = MacroConfig.from_cfg(self.cfg)
            store = store_from_config(self.cfg)
            binds = self._ed_binds()
            executor = self._key_executor()   # raises ExecutorError off-Windows -> caught below
            snapshot = ((lambda: self.ed_ctx.snapshot()) if self.ed_ctx is not None else None)
            # Live allowlist: a custom macro may only use actions the Commander has opted into via
            # [keybinds].allowlist, read fresh so a live settings change is honoured at run time.
            allowlist = (lambda: frozenset(KeybindConfig.from_cfg(self.cfg).allowlist))
            self.macros = MacroCapability(
                store=store, config=mcfg, binds=binds, executor=executor,
                allowlist=allowlist, status_snapshot=snapshot,
                abort_event=self._keybind_abort,          # one hard abort covers keybinds + macros
                speak=self._speak_proactive_line,         # triggered arm prompt / outcome
                log=lambda msg: self._log("macro", msg))
            self.registry.register(self.macros)
            self._start_event_pump()                      # triggered macros need the bus pump

            saved = store.all()
            triggered = sum(1 for s in saved if s.trigger)
            if self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    "Custom macros ON, but ED monitoring is OFF — the combat guard can't verify "
                    "safety and triggers won't fire, so macros will be refused until "
                    "[elite].enabled."})
            else:
                self.bus.publish({"type": "log", "who": "system", "text":
                    f"Custom macros ON ({len(saved)} saved, {triggered} triggered; confirmation "
                    f"{'on' if mcfg.require_confirmation else 'off'}, combat guard "
                    f"{'on' if mcfg.combat_guard else 'off'})."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.macros = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Custom macros failed to start: {e}"})

    # ---- Auto-honk (N5) ---------------------------------------------------
    def _start_honk(self) -> None:
        """Build + register the auto-honk capability and ensure the event pump is running. Fail
        soft — a missing bindings file or a non-Windows host just leaves it off; it must never
        block startup. Needs ED monitoring for the arrival event, the current fire group, and
        the combat guard, so warn (don't fail) if that's off."""
        try:
            from .capabilities.honk_capability import HonkCapability, HonkConfig

            hcfg = HonkConfig.from_cfg(self.cfg)
            binds = self._ed_binds()
            executor = self._key_executor()   # raises ExecutorError off-Windows -> caught below
            snapshot = ((lambda: self.ed_ctx.snapshot()) if self.ed_ctx is not None else None)
            self.honk = HonkCapability(
                hcfg, binds=binds, executor=executor,
                status_snapshot=snapshot,
                speak=self._speak_proactive_line,   # spoken Surface-Scanner-misfire warning (K2)
                log=lambda msg: self._log("honk", msg))
            self.registry.register(self.honk)
            self._start_event_pump()

            fire = binds.get(hcfg.fire_action)
            fire_ok = fire is not None and fire.usable
            if self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    "Auto-honk ON, but ED monitoring is OFF — no arrival events, and the "
                    "combat guard can't verify safety, so it won't fire until [elite].enabled."})
            elif not fire_ok:
                self.bus.publish({"type": "log", "who": "system", "text":
                    f"Auto-honk ON, but {hcfg.fire_action} has no keyboard binding — bind the "
                    "Discovery Scanner's fire button to a key in-game so COVAS can honk."})
            else:
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Auto-honk ON (probe + hold {hcfg.fire_action} on the "
                                          f"current fire group; backs out of a Surface-Scanner "
                                          f"misfire; combat guard "
                                          f"{'on' if hcfg.combat_guard else 'off'})."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.honk = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Auto-honk failed to start: {e}"})

    # ---- Find-closest-module ----------------------------------------------
    def _start_nav(self) -> None:
        """Build + register the find-closest-module capability. Fail soft: a startup problem
        just leaves the feature off. The Spansh HTTP client is built here (composition root)
        so tests never need it; current-system is read live from ED context with a journal
        fallback."""
        try:
            from .nav import RequestsHttp, ModuleIndex
            from .capabilities.find_closest_capability import NavConfig, FindClosestCapability

            ncfg = NavConfig.from_cfg(self.cfg)
            # Live taxonomy so newly-released Frontier modules are findable without a CSV
            # refresh: reconciled against the bundled taxonomy on a background startup thread
            # (below), and resolution falls back to the bundle until/if that fetch lands.
            module_index = ModuleIndex()
            self.nav = FindClosestCapability(
                ncfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                module_index=module_index,
                log=lambda msg: self._log("nav", msg))
            self.registry.register(self.nav)
            if self.ed_ctx is None:
                self.bus.publish({"type": "log", "who": "system", "text":
                    f"Find-closest-module ON (pad {ncfg.default_pad_size or 'any'}); ED "
                    "monitoring is OFF, so current system falls back to the newest journal."})
            else:
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Find-closest-module ON "
                                          f"(pad {ncfg.default_pad_size or 'any'})."})
            threading.Thread(target=self._refresh_module_index, args=(module_index,),
                             name="module-index-refresh", daemon=True).start()
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.nav = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Find-closest-module failed to start: {e}"})

    def _refresh_module_index(self, module_index) -> None:
        """Background startup task: fetch Spansh's current module list and log any modules newer
        than the bundled taxonomy (they're now findable). Fail-soft — off the hot path, never
        blocks the voice loop, and a fetch failure just leaves the bundle in charge."""
        try:
            module_index.refresh()
            new = module_index.extra_names()
            if new:
                self._log("nav", f"live taxonomy added {len(new)} module(s) not in the "
                                 f"bundle: {', '.join(new)}.")
        except Exception as e:  # noqa: BLE001 — best-effort; the bundled taxonomy still works
            self._log("nav", f"live taxonomy refresh failed: {e}")

    # ---- Find-closest-ship ------------------------------------------------
    def _start_ship_nav(self) -> None:
        """Build + register the find-closest-ship capability (shares [nav]). Fail soft: a
        startup problem just leaves the feature off. Same seams as find-closest-module —
        Spansh client built here, current-system read live with a journal fallback."""
        try:
            from .nav import RequestsHttp, ShipIndex
            from .nav.edsm_stock import EdsmStockLookup
            from .capabilities.find_closest_capability import NavConfig
            from .capabilities.find_closest_ship_capability import FindClosestShipCapability
            from .ed.journal import resolve_journal_dir
            from .ed.shipyard import read_shipyard_snapshot

            ncfg = NavConfig.from_cfg(self.cfg)
            # Live roster so newly-released Frontier hulls are findable without a code change:
            # the index is reconciled against the bundled roster on a background startup thread
            # (below), and resolution falls back to the bundle until/if that fetch lands.
            ship_index = ShipIndex()
            # Ground-truth stock for the last-visited shipyard (Spansh lists the CATALOG, not
            # stock). Re-read per lookup — the file is tiny and ED rewrites it on each visit.
            shipyard_path = resolve_journal_dir(self.cfg) / "Shipyard.json"
            # EDSM current-stock check for every OTHER station — what makes the answer agree
            # with Inara (Spansh unions ships into a catalog; EDSM keeps the live snapshot).
            stock_lookup = (EdsmStockLookup(RequestsHttp(), user_agent=ncfg.user_agent)
                            if ncfg.verify_stock else None)
            self.ship_nav = FindClosestShipCapability(
                ncfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                get_local_shipyard=lambda: read_shipyard_snapshot(shipyard_path),
                stock_lookup=stock_lookup,
                ship_index=ship_index,
                log=lambda msg: self._log("ship_nav", msg))
            self.registry.register(self.ship_nav)
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Find-closest-ship ON (pad {ncfg.default_pad_size or 'any'}, "
                                      f"stock check {'EDSM' if stock_lookup else 'off'})."})
            threading.Thread(target=self._refresh_ship_index, args=(ship_index,),
                             name="ship-index-refresh", daemon=True).start()
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.ship_nav = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Find-closest-ship failed to start: {e}"})

    def _refresh_ship_index(self, ship_index) -> None:
        """Background startup task: fetch Spansh's current ship list and log any hulls newer
        than the bundled roster (they're now findable). Fail-soft — off the hot path, never
        blocks the voice loop, and a fetch failure just leaves the bundle in charge."""
        try:
            ship_index.refresh()
            new = ship_index.extra_names()
            if new:
                self._log("ship_nav", f"live roster added {len(new)} ship(s) not in the "
                                      f"bundle: {', '.join(new)}.")
        except Exception as e:  # noqa: BLE001 — best-effort; the bundled roster still works
            self._log("ship_nav", f"live roster refresh failed: {e}")

    # ---- Star-system search -----------------------------------------------
    def _start_system_search(self) -> None:
        """Build + register the star-system search capability. Fail soft: a startup problem
        just leaves the feature off. The Spansh HTTP client is built here (composition root)
        so tests never need it; current-system is read live from ED context with a journal
        fallback (same seam as find-closest)."""
        try:
            from .search import RequestsHttp
            from .capabilities.system_search_capability import (SystemSearchCapability,
                                                                SystemSearchConfig)
            scfg = SystemSearchConfig.from_cfg(self.cfg)
            self.system_search = SystemSearchCapability(
                scfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                log=lambda msg: self._log("systems", msg))
            self.registry.register(self.system_search)
            self.bus.publish({"type": "log", "who": "system", "text": "Star-system search ON."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.system_search = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Star-system search failed to start: {e}"})

    # ---- Remaining Spansh search categories (stations/factions/signals/misc) --
    def _start_searches(self) -> None:
        """Build + register the four remaining LLM-native Spansh search capabilities. Fail
        soft: a startup problem just leaves them off. One [search] toggle enables the group;
        each shares the injected HTTP client + current-system seam."""
        try:
            from .search import RequestsHttp
            from .search.faction_index import FactionIndex
            from .capabilities._search_support import SearchConfig
            from .capabilities.station_search_capability import StationSearchCapability
            from .capabilities.minor_faction_search_capability import MinorFactionSearchCapability
            from .capabilities.signal_search_capability import SignalSearchCapability
            from .capabilities.misc_search_capability import MiscSearchCapability

            scfg = SearchConfig.from_cfg(self.cfg, "search")
            http = RequestsHttp()
            # One faction-name index shared by the faction-using capabilities (lazily fetched
            # from Spansh on first use, then cached) so a mistranscribed faction name resolves
            # to its exact string instead of returning zero systems.
            factions = FactionIndex()
            common = dict(http=http, get_current_system=self._current_system,
                          log=lambda msg: self._log("search", msg))
            self.searches = [
                StationSearchCapability(scfg, factions=factions, **common),
                MinorFactionSearchCapability(scfg, factions=factions, **common),
                SignalSearchCapability(scfg, **common),
                MiscSearchCapability(scfg, factions=factions, **common),
            ]
            for cap in self.searches:
                self.registry.register(cap)
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Search categories ON (stations, minor factions, "
                                      "signals, faction states)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.searches = []
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Search categories failed to start: {e}"})

    # ---- Body / bio-geo signal finder (#68) -------------------------------
    def _start_bodies(self) -> None:
        """Build + register the body finder (#68) — nearest body by type / biological signal over
        the `bodies/search` endpoint. Fail soft: a startup problem just leaves it off. Its own
        `[bodies]` toggle (defaults OFF); shares the injected HTTP client + current-system seam;
        the nearest match's system is copied to the clipboard for the galaxy map."""
        try:
            from .search import RequestsHttp
            from .capabilities._search_support import SearchConfig
            from .capabilities.body_search_capability import BodySearchCapability

            bcfg = SearchConfig.from_cfg(self.cfg, "bodies")
            self.body_search = BodySearchCapability(
                bcfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                log=lambda msg: self._log("bodies", msg))
            self.registry.register(self.body_search)
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Body finder ON (nearest world / biological signal)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.body_search = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Body finder failed to start: {e}"})

    # ---- Route planning (#41 foundation proof) ----------------------------
    def _start_route_plan(self) -> None:
        """Build + register the trade-route planner (#41), the foundation proof for the Spansh
        route client + galaxy-map plot handoff. Fail soft — a startup problem just leaves it off.
        Shares the current-system/station seams; the plot handoff copies the next stop to the
        clipboard until the galaxy-map keybind automation (#32) lands."""
        try:
            from .search import RequestsHttp
            from .capabilities.route_plan_capability import RoutePlanCapability, RoutePlanConfig

            rcfg = RoutePlanConfig.from_cfg(self.cfg)
            self.route_plan = RoutePlanCapability(
                rcfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                get_current_station=self._current_station,
                log=lambda msg: self._log("route", msg))
            self.registry.register(self.route_plan)
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Trade-route planner ON (plot handoff via clipboard)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.route_plan = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Trade-route planner failed to start: {e}"})

    def _start_neutron_plan(self) -> None:
        """Build + register the neutron / long-range galaxy planner (#43), the second capability on
        the #41 route foundation. Fail soft — a startup problem just leaves it off. Shares the
        current-system seam for the default start; the plot handoff copies the first waypoint to the
        clipboard until the galaxy-map keybind automation (#32) lands."""
        try:
            from .search import RequestsHttp
            from .capabilities.neutron_plan_capability import (NeutronPlanCapability,
                                                               NeutronPlanConfig)

            ncfg = NeutronPlanConfig.from_cfg(self.cfg)
            self.neutron_plan = NeutronPlanCapability(
                ncfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                log=lambda msg: self._log("neutron", msg))
            self.registry.register(self.neutron_plan)
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Neutron-route planner ON (plot handoff via clipboard)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.neutron_plan = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Neutron-route planner failed to start: {e}"})

    # ---- Road to Riches (#42, on the #41 foundation) ----------------------
    def _start_riches_plan(self) -> None:
        """Build + register the Road-to-Riches planner (#42) — nearby high-value UNSCANNED bodies
        to First-Discovery-scan for exploration credits — on the shared Spansh route client +
        galaxy-map plot handoff. Fail soft: a startup problem just leaves it off. Only needs the
        current SYSTEM (not a docked station); the plot handoff copies the first system to the
        clipboard until the galaxy-map keybind automation (#32) lands."""
        try:
            from .search import RequestsHttp
            from .capabilities.riches_plan_capability import RichesPlanCapability, RichesPlanConfig

            rcfg = RichesPlanConfig.from_cfg(self.cfg)
            self.riches_plan = RichesPlanCapability(
                rcfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                log=lambda msg: self._log("route", msg))
            self.registry.register(self.riches_plan)
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Road-to-Riches planner ON (plot handoff via clipboard)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.riches_plan = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Road-to-Riches planner failed to start: {e}"})

    # ---- Mining helper (#45, on the Spansh search layer) ------------------
    def _start_mining_helper(self) -> None:
        """Build + register the mining helper (#45) — nearest ring hotspot for a material + the best
        FRESHNESS-VERIFIED place to sell it + the mining loop dropped onto the checklist. Uses the
        synchronous Spansh /search layer (not the async route client), shares the current-system
        seam and the same checklist model the checklist capability serves, and hands the hotspot
        system to the galaxy map via the clipboard until the #32 keybind course-set lands. Fail
        soft — a startup problem just leaves it off."""
        try:
            from .search import RequestsHttp
            from .capabilities.mining_helper_capability import (MiningHelperCapability,
                                                                MiningHelperConfig)

            mcfg = MiningHelperConfig.from_cfg(self.cfg)
            self.mining_helper = MiningHelperCapability(
                mcfg, http=RequestsHttp(),
                get_current_system=self._current_system,
                checklist=self.checklist,
                log=lambda msg: self._log("mining", msg))
            self.registry.register(self.mining_helper)
            self.bus.publish({"type": "log", "who": "system",
                              "text": "Mining helper ON (hotspots + fresh sell price + checklist)."})
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.mining_helper = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Mining helper failed to start: {e}"})

    def _current_station(self) -> str | None:
        """The station the Commander is currently DOCKED at, from live ED context — or None when
        not docked / no telemetry (the trade planner then asks for a start station)."""
        if self.ed_ctx is not None:
            snap = self.ed_ctx.snapshot()
            if snap.get("docked") and snap.get("station"):
                return snap["station"]
        return None

    def _current_system(self) -> str | None:
        """The Commander's current star system: live ED context first, else the newest
        journal's last jump/location. None when nothing is available."""
        if self.ed_ctx is not None:
            s = self.ed_ctx.snapshot().get("system")
            if s:
                return s
        try:
            from .ed import resolve_journal_dir
            from .nav import current_system_from_journal
            return current_system_from_journal(resolve_journal_dir(self.cfg))
        except Exception:  # noqa: BLE001 — a fallback failure just means "unknown"
            return None

    # ---- logging & status -------------------------------------------------
    def _open_log(self):
        d = Path(self.cfg["logging"]["dir"])
        d.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        return open(d / f"session_{ts}.log", "a", encoding="utf-8")

    def _log(self, who: str, text: str) -> None:
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        try:
            self._logf.write(f"{ts}  {who}: {text}\n")
            self._logf.flush()
        except (ValueError, OSError):
            # The log file may be closed while a daemon (index/cast refresh) is still finishing
            # on the way out — never let a late log line crash a background thread.
            pass
        self.bus.publish({"type": "log", "who": who, "text": text})

    def _thinking_bed_enabled(self) -> bool:
        """The soft "thinking" bed toggle ([audio].thinking_bed, default on). Read live so a
        Settings change applies to the next turn without a restart."""
        return bool((self.cfg.get("audio", {}) or {}).get("thinking_bed", True))

    def set_state(self, state: str, extra: str = "") -> None:
        self.state = state
        # Drive the soft "thinking" bed (issue #5) off the single state chokepoint: start it when a
        # user turn enters a WORKING state, stop it on entering any other state. Because every exit
        # path (reply->Speaking, cancel/error->Idle, barge-in->Listening) funnels through here, the
        # bed can never be orphaned. Fail-soft — a cue glitch must not break the state transition.
        try:
            if state in _WORKING_STATES:
                if self._bed_armed and self._thinking_bed_enabled():
                    self.cues.start_loop("thinking")
            else:
                self.cues.stop_loop()
                self._bed_armed = False
        except Exception:  # noqa: BLE001
            pass
        label = f"[{state}] {extra}".rstrip()
        print("\n>> " + label, flush=True)
        self.bus.publish({"type": "status", "state": state, "extra": extra})

    # ---- cancellation -----------------------------------------------------
    def _interrupt(self) -> None:
        """Abort whatever is in flight AND actively silence playback. Setting the turn's cancel
        event alone (the old behaviour) only tells the async feeder to stop a chunk-read later —
        the mixer keeps playing already-buffered speech for tens of ms, which on barge-in leaks
        the reply's tail into the just-opened mic (issue #71). So we also hard-stop the mixer and
        BRIEFLY await confirmed silence before returning, so callers can open the mic clean."""
        if self.active_cancel is not None and not self.active_cancel.is_set():
            self.active_cancel.set()
        self.cues.stop()  # stop any cue still playing
        self._halt_playback()

    def _halt_playback(self) -> None:
        """Drop all in-flight TTS on the shared mixer and wait (bounded) for silence. No-op when
        the audio layer is off (direct-device playback path, or tests) — the barge-in mute window
        is the backstop there. Fail-soft: never let teardown crash the loop."""
        if self.mixer is None:
            return
        try:
            self.mixer.cancel_speech()
            deadline = time.monotonic() + _HALT_PLAYBACK_TIMEOUT_MS / 1000.0
            while self.mixer.speech_active() and time.monotonic() < deadline:
                time.sleep(0.005)
        except Exception:  # noqa: BLE001 — barge-in teardown must never break the voice loop
            pass

    # ---- PTT / cancel handlers -------------------------------------------
    def on_ptt_down(self) -> None:
        self._ptt_t0 = time.monotonic()
        # Hold the proactive lock across the interrupt + state flip so a callout can't slip
        # its idle-claim in between and end up speaking over this capture. Either the claim
        # loses (sees "Listening" -> skips) or it already won (this interrupt cancels it).
        with self._proactive_lock:
            barged = self.state in _PLAYBACK_STATES
            self._interrupt()        # interrupt any current thinking/speaking (incl. a callout)
            self.set_state("Listening")
        # On a barge-in _interrupt() has already silenced the mixer + awaited it; the mute window
        # is the backstop for any residual device-buffer tail (issue #71).
        self.recorder.start(mute_ms=_BARGE_IN_MUTE_MS if barged else 0.0)
        self.cues.play("listening")

    def on_ptt_up(self) -> None:
        audio = self.recorder.stop()
        self._apply_pending_recorder()  # apply a mic change that arrived during the hold (issue #90)
        held_ms = (time.monotonic() - self._ptt_t0) * 1000.0
        tap_ms = float(self.cfg["keys"].get("tap_cancel_ms", 400))
        if held_ms < tap_ms:
            # Brief tap = cancel. The in-flight op was already aborted on key-down;
            # just drop this (empty) capture and return to Idle.
            self.cues.stop()
            self.set_state("Idle", "cancelled")
            return
        self._dispatch_utterance(audio)

    def _dispatch_utterance(self, audio, *, wake_gated: bool = False) -> None:
        """Run ONE turn on the worker thread for a captured utterance. Shared by PTT release
        and the hands-free VAD listener (issue #63) so both drive the exact same
        transcribe→LLM→TTS + cancellation path. ``wake_gated`` (issue #64) is set ONLY by the
        continuous path: when a wake word is configured, the worker must confirm the transcript
        carries it before running the turn. PTT never sets it, so a deliberate press is ungated."""
        self.cues.play("processing")
        # Arm the soft "thinking" bed for THIS user turn (issue #5): the one-shot processing tick
        # above acknowledges receipt; the bed then fills the transcribe/think/search wait. The
        # worker's first set_state("Transcribing") starts it; any exit stops it (see set_state).
        self._bed_armed = True
        cancel = threading.Event()
        self.active_cancel = cancel
        self.worker = threading.Thread(
            target=self._process, args=(audio, cancel), kwargs={"wake_gated": wake_gated},
            daemon=True,
        )
        self.worker.start()

    # ---- typed prompt (control panel, issue #76) --------------------------
    def dispatch_text(self, text: str) -> None:
        """Public entry for a TYPED prompt from the control panel (issue #76). Runs a FULL normal
        turn — router tiering, ED + memory injection, capability tools, conversation history, and a
        spoken TTS reply — identical to a spoken turn, just skipping STT. Empty/whitespace is
        rejected (mirrors the transcription guard). Like a PTT press it barges in on anything in
        flight, then runs on the worker thread."""
        if not text or not text.strip():
            return  # nothing to answer — never send an empty user turn (the API 400s on it)
        # Barge-in exactly like on_ptt_down: interrupt any current thinking/speaking (incl. a
        # proactive callout) and claim the turn under the proactive lock so a callout can't slip in.
        with self._proactive_lock:
            self._interrupt()
        self._dispatch_text(text.strip())

    def _dispatch_text(self, text: str) -> None:
        """Run ONE typed turn on the worker thread — mirrors :meth:`_dispatch_utterance` minus STT
        (no capture cue, no transcription): same soft-bed arming, cancel/barge-in wiring, and
        transactional history path, straight into the shared :meth:`_run_turn`."""
        self.cues.play("processing")
        self._bed_armed = True
        cancel = threading.Event()
        self.active_cancel = cancel
        self.worker = threading.Thread(
            target=self._run_turn, args=(text, cancel), daemon=True)
        self.worker.start()

    # ---- Tier-2 reflex FAST PATH (second PTT, issue #38) ------------------
    def on_reflex_ptt_down(self) -> None:
        """The reflex second-PTT was pressed. Same barge-in as the main PTT (interrupt anything in
        flight, flip to Listening under the proactive lock) — but this capture is destined for the
        local phrase-spotter fast path, not a conversation turn."""
        with self._proactive_lock:
            barged = self.state in _PLAYBACK_STATES
            self._interrupt()
            self.set_state("Listening", "reflex")
        self.recorder.start(mute_ms=_BARGE_IN_MUTE_MS if barged else 0.0)
        self.cues.play("listening")

    def on_reflex_ptt_up(self) -> None:
        """Reflex second-PTT released — dispatch the capture down the fast path."""
        audio = self.recorder.stop()
        self._apply_pending_recorder()  # apply a mic change that arrived during the hold (issue #90)
        self._dispatch_reflex(audio)

    def _dispatch_reflex(self, audio) -> None:
        """Run the reflex fast path on the worker thread: local transcribe -> phrase-spot -> on a
        MATCH fire the reflex immediately through the #36 guard+executor (no LLM); on NO MATCH hand
        the same capture to the normal conversation turn so the second PTT never eats an ordinary
        request. Mirrors :meth:`_dispatch_utterance`'s worker/cancel setup so cancellation and
        barge-in behave identically."""
        self.cues.play("processing")
        cancel = threading.Event()
        self.active_cancel = cancel
        self.worker = threading.Thread(
            target=self._process_reflex, args=(audio, cancel), daemon=True)
        self.worker.start()

    def _process_reflex(self, audio, cancel: threading.Event) -> None:
        """Worker for the reflex second-PTT (issue #38). Transcribes locally, runs the pure
        :class:`PhraseSpotter` over the transcript, then:

          * **match** -> fires the named reflex via :meth:`ReflexCapability.fire_reflex` — the SAME
            allowlist + combat-permissive guard + shared executor + hard abort as the LLM tool path
            (#36). NO LLM round-trip, so latency is ~STT time only; the spoken result is just
            after-the-fact feedback (the keypress already went out).
          * **no match** (or reflexes disabled) -> falls through to a normal conversation turn on
            the SAME capture via :meth:`_dispatch_utterance`, so the second PTT still works as an
            ordinary talk key.

        Fail soft: any error returns to Idle and never crashes the loop."""
        try:
            if cancel.is_set():
                return
            self.set_state("Transcribing", "reflex")
            text = self.stt.transcribe(audio)
            if cancel.is_set():
                return
            if not text.strip():
                # Empty OR whitespace-only transcription (silence, or a barge-in that caught
                # near-silence). Drop it — sending an empty/whitespace user turn 400s the API
                # ("messages … must have non-empty content") and poisons later turns.
                self.cues.play("failed")
                self.set_state("Idle", "(no speech detected)")
                return

            action = PhraseSpotter.from_cfg(self.cfg).match(text)
            self._log("reflex-spot", f"{text!r} -> {action or 'no match'}")

            if action is None or self.reflex is None:
                # Not a snap call (no keyword) — or reflexes aren't enabled. Fall through to the
                # normal turn so this key still behaves like an ordinary push-to-talk.
                print(f"\nCommander (reflex miss): {text}")
                self._dispatch_utterance(audio)
                return

            # A spotted reflex: fire it IMMEDIATELY via the #36 path (guard + executor + abort).
            result = self.reflex.fire_reflex(action)
            self._log("reflex", f"phrase-spot fired {action}: {result}")
            self.bus.publish({"type": "log", "who": "system", "text": f"Reflex: {result}"})
            self.set_state("Speaking", "reflex")
            try:
                self._speak(result, cancel)   # spoken feedback; the keypress already fired
            except Exception:  # noqa: BLE001 — feedback is best-effort, never fail the reflex on it
                pass
            self.set_state("Idle")
        except Exception as e:  # noqa: BLE001 — keep the loop alive on any failure
            self.cues.play("failed")
            self.set_state("Idle", f"reflex error: {e}")

    # ---- hands-free / VAD listen (issue #63) ------------------------------
    def _on_vad_speech_start(self) -> None:
        """Speech onset in continuous mode = barge-in, exactly like on_ptt_down: interrupt any
        in-flight thinking/speaking (including a proactive callout) and flip to Listening, under
        the proactive lock so a callout can't slip in. A physical PTT hold wins — if the user is
        holding the key, ignore the VAD onset so the two inputs don't fight."""
        if self.ptt_held:
            return
        with self._proactive_lock:
            self._interrupt()
            self.set_state("Listening")
        self.cues.play("listening")

    def _on_vad_utterance(self, audio) -> None:
        """A completed hands-free utterance: dispatch it down the shared turn path. Skipped
        while PTT is held so a physical hold wins and we never double-fire a turn. Marked
        ``wake_gated`` (issue #64) so the worker enforces the wake word (when one is set)
        before this ambient capture becomes a turn — PTT stays ungated."""
        if self.ptt_held:
            return
        self._dispatch_utterance(audio, wake_gated=True)

    def _listen_mode(self) -> str:
        return str(self.cfg.get("listen", {}).get("mode", "ptt")).strip().lower()

    def _start_listener(self) -> None:
        """Start the VAD mic listener (idempotent). Fail-soft: if the mic won't open, log and
        stay on PTT — continuous mode simply doesn't engage rather than crashing the loop."""
        if self.mock:
            return  # dev-mock has no real audio; nothing to listen to
        if self.listener is not None and self.listener.running:
            return
        try:
            self.listener = VadListener(
                self.cfg,
                on_speech_start=self._on_vad_speech_start,
                on_utterance=self._on_vad_utterance,
                log=lambda m: self._log("listen", m))
            if self.listener.start():
                self._log("listen", "Hands-free continuous listening ON.")
            else:
                self.listener = None  # start() already logged the fall-back to PTT
        except Exception as e:  # noqa: BLE001 — never let listen setup crash the loop
            self.listener = None
            self._log("listen", f"continuous listen failed to start: {e}; staying on PTT.")

    def _stop_listener(self) -> None:
        if self.listener is None:
            return
        try:
            self.listener.stop()
        except Exception as e:  # noqa: BLE001 — teardown must never raise
            self._log("listen", f"stopping the listener failed: {e}")
        self.listener = None
        self._log("listen", "Hands-free continuous listening OFF (push-to-talk).")

    def _reconcile_listener(self) -> None:
        """Start/stop the VAD listener to match [listen].mode — the live mode switch. Mirrors
        _reconcile_hud: called directly (not via the pump) so the toggle works from the Settings
        page or by voice even when no other ambient feature is running."""
        try:
            if self._listen_mode() == "continuous":
                self._start_listener()
            else:
                self._stop_listener()
        except Exception as e:  # noqa: BLE001 — a toggle glitch must not crash the loop
            self._log("listen", f"reconcile failed: {e}")

    def on_cancel(self) -> None:
        self._interrupt()
        self.set_state("Idle", "cancelled")

    # public alias for the UI cancel button / voice command
    def trigger_cancel(self) -> None:
        self.on_cancel()

    # ---- live settings (from the web UI) ----------------------------------
    def update_settings(self, patch: dict) -> None:
        """Merge a settings patch into the running config, persist it to overrides.json, and
        live-apply anything that changed (providers, Whisper, listener, hotkeys, mic, audio)."""
        before = self._settings_snapshot()
        deep_merge(self.cfg, patch)
        deep_merge(self.overrides, patch)
        save_overrides(self.overrides)
        self._after_settings_change(before)

    def reset_setting(self, path) -> None:
        """Reset ONE setting to its config.toml default by dropping it from
        overrides.json (the file's own reset mechanism) and reloading config.
        Live-applies the change (providers/Whisper/listener/hotkeys/mic) like update_settings."""
        before = self._settings_snapshot()
        _pop_path(self.overrides, tuple(path))
        _prune_empty(self.overrides)
        save_overrides(self.overrides)
        # Re-derive the effective config from config.toml + the remaining
        # overrides (paths re-resolved), keeping the same dict identity so any
        # holder of self.cfg sees the update.
        fresh = load_config()
        self.cfg.clear()
        self.cfg.update(fresh)
        self._after_settings_change(before)

    def _settings_snapshot(self) -> dict:
        """Deep-copy the config sub-trees whose changes drive a live rebuild/reconcile, taken
        BEFORE a settings merge so _after_settings_change can diff old vs new at section
        granularity (issue #90, decision #3). Cheap — these sections are small dicts."""
        import copy
        sections = (set(_LLM_SECTIONS) | set(_TTS_SECTIONS)
                    | {"whisper", "keys", "reflex", "listen", "audio", "personality"})
        return {s: copy.deepcopy(self.cfg.get(s)) for s in sections}

    def _after_settings_change(self, before: dict) -> None:
        """Shared tail for update/reset: broadcast the new settings and LIVE-APPLY every change
        that can be (issue #90). Providers rebuild on a background thread when their config section
        changed (fail-soft, next-turn), Whisper reloads on model/device/compute change, and the
        listener/hotkeys/mic/audio reconcile in place. Only RESTART_REQUIRED settings need a
        relaunch — everything here takes effect without one."""
        self.bus.publish({"type": "settings", "settings": self.public_settings()})
        # Companion HUD (issue #47): apply an [hud].enabled toggle live (Settings page or voice).
        self._reconcile_hud()
        # Activation mode (issue #63): apply a [listen].mode switch live — start/stop the VAD
        # mic listener to match, so "switch to continuous listening" takes effect immediately.
        self._reconcile_listener()
        # Mic device (issue #89): rebuild the Recorder + restart the VAD listener when the input
        # device changed — rides the same reconcile path as the listen-mode switch.
        if (before.get("audio") or {}).get("input_device") != \
                self.cfg.get("audio", {}).get("input_device"):
            self._reconcile_recorder()
        # Hotkeys (issue #90): re-resolve the PTT/cancel/reflex scan-code sets in place on a
        # [keys].* or [reflex].ptt change — the single keyboard hook stays installed, no re-hook.
        if (before.get("keys") != self.cfg.get("keys")
                or (before.get("reflex") or {}).get("ptt")
                != (self.cfg.get("reflex", {}) or {}).get("ptt")):
            self._reconcile_hotkeys()
        # Audio settings (bus volumes, enable toggles, comms treatment) apply live.
        if self.audio is not None:
            try:
                self.audio.apply_settings()
            except Exception as e:  # noqa: BLE001 — a settings glitch must not crash the loop
                self._log("system", f"audio settings apply failed: {e}")
        # Providers (issue #90): a change under an LLM/TTS config section swaps that provider on the
        # NEXT turn. Built on a daemon thread (a provider build may touch the network/GPU) then
        # rebound in place; an in-flight turn finishes on its turn-local instance (decision #2).
        if _section_changed(before, self.cfg, _LLM_SECTIONS):
            threading.Thread(target=self._reload_llm, name="reload-llm", daemon=True).start()
        if _section_changed(before, self.cfg, _TTS_SECTIONS):
            threading.Thread(target=self._reload_tts, name="reload-tts", daemon=True).start()
        w = self.cfg["whisper"]
        ow = before.get("whisper") or {}
        if (w["model"], w["device"], w["compute_type"]) != (
            ow.get("model"), ow.get("device"), ow.get("compute_type")
        ):
            self.set_state(self.state, f"reloading Whisper: {w['model']}")
            threading.Thread(target=self._reload_whisper, daemon=True).start()
        # Persona->voice pairing (issue #96): a persona switch dresses it in its paired/explicit
        # voice; a manual voice change on the current persona is remembered as explicit. Routed
        # through its own update_settings (guarded), so it rides the normal persist + TTS-reload path.
        self._reconcile_persona_voice(before)

    def _reload_whisper(self) -> None:
        try:
            self.stt = make_stt(self.cfg)
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Whisper model reloaded: {self.cfg['whisper']['model']}"})
        except Exception as e:  # noqa: BLE001
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Whisper reload failed: {e}"})

    def _reload_llm(self) -> None:
        """Rebuild + rebind the LLM provider after an [llm]/[<provider>] change (issue #90). Runs
        on a daemon thread (a build may hit the network). Fail-soft (non-negotiable): on failure do
        NOT rebind — keep the working provider and say so; on success rebind (GIL-atomic — the next
        turn picks it up via its turn-local) and publish the new provider/model as a UI toast."""
        try:
            new = make_llm(self.cfg)
        except Exception as e:  # noqa: BLE001 — keep the previous provider on any build error
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Couldn't switch the LLM: {e}; keeping the previous one."})
            return
        self.llm = new
        # Re-point the ambient audio layer's LLM too (issue #90 review): it captured the LLM at
        # construction for opt-in chatter-flavor / comms-variants, so without this a swap would
        # half-apply (main turns switch, ambient generation stays on the old provider). Fail-soft.
        if self.audio is not None:
            try:
                cheap = Router.from_cfg(self.cfg).cheap_route(None).model
                self.audio.set_providers(llm=new, cheap_model=cheap)
            except Exception as e:  # noqa: BLE001 — a generator refresh glitch must not fail the swap
                self._log("system", f"audio layer LLM refresh failed: {e}")
        prov = str(self.cfg.get("llm", {}).get("provider", "anthropic"))
        model = str(self.cfg.get(prov, {}).get("model", "") or "default")
        self.bus.publish({"type": "log", "who": "system", "text": f"LLM now: {prov} / {model}."})

    def _reload_tts(self) -> None:
        """Rebuild + rebind the TTS provider after a [tts]/[<voice>] change (issue #90). Passes the
        EXISTING mixer (never rebuilt — that's what keeps a TTS swap safe against the shared output
        device). Fail-soft like _reload_llm: keep the previous voice on failure. Recomputes
        text-only mode so switching to/from a keyless ElevenLabs flips voice output correctly."""
        try:
            new = make_tts(self.cfg, mixer=self.mixer)
        except Exception as e:  # noqa: BLE001 — keep the previous voice on any build error
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Couldn't switch the voice: {e}; keeping the previous one."})
            return
        self.tts = new
        from .firstrun import text_only_mode
        self.text_only = text_only_mode(self.cfg, mock=self.mock, tts_injected=False)
        # Re-point the ambient audio layer's voice too (issue #90 review): it holds its own TTS
        # (persona/interdiction/comms lines) and a cast synth closed over the OLD provider, so
        # without this the swap half-applies (main replies switch, ambient/comms/crew stay old).
        # _build_cast_synth() reads self.tts, which is already the new provider here. Fail-soft.
        if self.audio is not None:
            try:
                self.audio.set_providers(tts=new, cast_synth=self._build_cast_synth())
            except Exception as e:  # noqa: BLE001 — a cast refresh glitch must not fail the swap
                self._log("system", f"audio layer voice refresh failed: {e}")
        prov = str(self.cfg.get("tts", {}).get("provider", "elevenlabs"))
        self.bus.publish({"type": "log", "who": "system", "text": f"Voice now: {prov}."})

    def _reconcile_recorder(self) -> None:
        """Rebuild the Recorder for a new [audio].input_device and restart the VAD listener if it's
        running so the change (device resolved at construction) applies live (issue #89). Fail-soft
        — a bad device just logs; the loop keeps the previous recorder. Mirrors _reconcile_listener,
        so a mic change and a listen-mode change ride the same reconcile path (decision #7).

        Deferral (issue #90 review): if a PTT/reflex capture is in flight, rebuilding now would
        strand the OLD recorder's open input stream and make the pending stop() read an unstarted
        NEW recorder (dropping the utterance). Defer to the capture boundary instead — on_ptt_up /
        on_reflex_ptt_up call _apply_pending_recorder() right after stop() closes the old stream."""
        if self.ptt_held or self.reflex_held:
            self._recorder_dirty = True
            self._log("system", "Microphone change deferred until the current capture ends.")
            return
        try:
            self.recorder = Recorder(self.cfg)
            # If continuous listening is on, the VAD listener holds its own device stream — bounce
            # it so it reopens on the new mic. _reconcile_listener is idempotent (start when
            # continuous, stop otherwise), so a stop-then-reconcile cleanly rebinds.
            if self.listener is not None:
                self._stop_listener()
                self._reconcile_listener()
            self._log("system", "Microphone changed; recorder rebuilt.")
        except Exception as e:  # noqa: BLE001 — a bad mic must not crash the loop
            self._log("system", f"microphone reconcile failed: {e}")

    def _apply_pending_recorder(self) -> None:
        """Rebuild a mic that was changed mid-capture (issue #90 review), now that stop() has closed
        the old input stream and on_key has already cleared ptt_held/reflex_held — so _reconcile_
        recorder rebuilds instead of re-deferring. No-op when nothing was deferred."""
        if self._recorder_dirty:
            self._recorder_dirty = False
            self._reconcile_recorder()

    def _resolve_hotkeys(self) -> None:
        """(Re)resolve the PTT/cancel/reflex scan-code SETS from the live [keys]/[reflex] config
        into instance attributes (issue #90). on_key reads these in place, so a hotkey change
        applies with NO re-hook — the single keyboard hook stays installed, only the sets change.
        The reflex set subtracts the PTT/cancel codes so a mis-configuration can't double-dispatch."""
        keys = self.cfg["keys"]
        self._ptt_codes = _resolve_codes(keys["push_to_talk"])
        cancel_key = str(keys.get("cancel", "")).strip()
        self._cancel_codes = _resolve_codes(cancel_key) if cancel_key else set()
        reflex_key = str((self.cfg.get("reflex", {}) or {}).get("ptt", "")).strip()
        self._reflex_codes = ((_resolve_codes(reflex_key) - self._ptt_codes - self._cancel_codes)
                              if reflex_key else set())

    def _reconcile_hotkeys(self) -> None:
        """Apply a [keys].*/[reflex].ptt change live by re-resolving the scan-code sets (issue #90).
        No re-hook: on_key already reads self._{ptt,cancel,reflex}_codes. Fail-soft — a bad key name
        logs and leaves the previous sets in place rather than crashing the loop."""
        try:
            self._resolve_hotkeys()
            self._log("keys",
                      f"Hotkeys updated (PTT {sorted(self._ptt_codes)}, "
                      f"cancel {sorted(self._cancel_codes) or 'tap-PTT'}, "
                      f"reflex {sorted(self._reflex_codes) or 'unbound'}).")
        except Exception as e:  # noqa: BLE001 — a bad key name must not crash the loop
            self._log("keys", f"hotkey reconcile failed: {e}")

    def _settings_option_pairs(self, setting) -> list | None:
        """Resolve a DYNAMIC enum's (value, label) options for the voice settings layer:
        Claude models from config; ElevenLabs voices/models from the live API (best-effort —
        None on failure so the capability can say so instead of guessing). Static enums are
        read straight from the schema and never reach here."""
        src = getattr(setting, "options_source", None)
        if src == schema.OPT_MODELS:
            return [(m, m) for m in self.cfg["anthropic"].get("available_models", [])]
        if src in (schema.OPT_EL_MODELS, schema.OPT_EL_VOICES):
            try:
                from . import elevenlabs as el
                if src == schema.OPT_EL_MODELS:
                    return [(m["model_id"], m.get("name") or m["model_id"])
                            for m in el.list_models(self.cfg)]
                pairs = []
                for v in el.list_voices(self.cfg):
                    cat = v.get("category", "")
                    label = (v.get("name", "") + (f" [{cat}]" if cat else "")) or v["voice_id"]
                    pairs.append((v["voice_id"], label))
                return pairs
            except Exception:  # noqa: BLE001 — offline/API failure: caller handles None
                return None
        # Fetched-catalog combobox sources (issue #92 / #88) — model ids, base URLs, Edge/Azure/
        # Cartesia voices. `catalog.option_pairs` is fail-soft (None on offline/no-key), so a voice
        # "set the edge voice to …" degrades gracefully like the ElevenLabs pickers.
        if src is not None:
            from . import catalog
            return catalog.option_pairs(src, self.cfg)
        return None

    def public_settings(self) -> dict:
        """Provider-shaped quick-panel state (issue #86): the LLM/Speech blocks MIRROR the active
        [llm]/[tts].provider — each carries only that provider's quick fields, serialized from the
        ONE schema so index.html renders them generically (no hardcoded Anthropic/ElevenLabs ids).
        Dynamic option lists that need the network (ElevenLabs/OpenAI/Edge/... catalogs) are left
        unresolved here and fetched client-side, so this stays cheap and offline; only the cheap
        Anthropic model list (from config) is folded in. whisper/web_search/personality stay flat."""
        c = self.cfg
        llm_provider = str(c.get("llm", {}).get("provider", "anthropic"))
        tts_provider = str(c.get("tts", {}).get("provider", "edge"))
        dyn = {schema.OPT_MODELS: c["anthropic"]["available_models"]}

        llm_panel = schema.LLM_PANELS.get(llm_provider)
        llm_keys = list(llm_panel.fields) if llm_panel else []
        supports_thinking = bool(llm_panel and llm_panel.supports_thinking)
        if supports_thinking:
            llm_keys.append(schema.THINKING_FIELD)
        llm_ro = llm_panel.readonly if llm_panel else ()

        tts_panel = schema.TTS_PANELS.get(tts_provider)
        tts_keys = list(tts_panel.fields) if tts_panel else []
        tts_ro = tts_panel.readonly if tts_panel else ()

        return {
            "llm": {
                "provider": llm_provider,
                "supports_thinking": supports_thinking,
                "fields": schema.panel_fields(c, self.overrides, llm_keys,
                                              readonly=llm_ro, dynamic=dyn),
            },
            "tts": {
                "provider": tts_provider,
                "fields": schema.panel_fields(c, self.overrides, tts_keys,
                                              readonly=tts_ro, dynamic=dyn),
            },
            "web_search": bool(c["web_search"]["enabled"]),
            "personality": bool(c["personality"]["enabled"]),
            "whisper": c["whisper"]["model"],
        }

    # ---- local voice commands --------------------------------------------
    def _speak(self, text: str, cancel: threading.Event, *, tts=None,  # noqa: ANN001
               text_only=None) -> None:
        """Play `text` through the TTS provider (real, mock, or fake). `tts` is the turn-local
        provider captured at the start of a turn (issue #90) so a mid-turn hot-swap can't change the
        voice underneath a reply; callers outside a turn omit it and get the live `self.tts`.
        `text_only` is the turn-local text-only flag, captured with `tts` (issue #90 review): a
        mid-turn swap to a keyless provider flips the LIVE `self.text_only`, so a turn that started
        with a working voice must gate on the flag it CAPTURED, not the live one — otherwise its
        reply is silently dropped. None => use the live `self.text_only` (callers outside a turn).
        On failure, log LOUDLY (session log + stderr) before re-raising — a dead TTS must be
        diagnosable, not a silent no-op (e.g. a 401 famous_voice_not_permitted). Callers keep
        their broad guards and still degrade to text/Idle. In text-only mode (no ElevenLabs key)
        there is no TTS to attempt — the reply is already shown as text — so skip quietly; that
        loud path is for a CONFIGURED-but-broken TTS, not the intended keyless mode.

        When CREW voicing is on ([crew].enabled, issue #69), the reply is first split into
        `[Name]`-prefixed segments: persona lines keep the direct TTS path below, crew lines are
        voiced in their own deterministic cast voice on the radio-treated comms bus. When it's off
        (the default) the reply is spoken verbatim, exactly as before — the parser isn't invoked."""
        if self.text_only if text_only is None else text_only:
            return
        if not crew_mod.is_enabled(self.cfg):
            self._speak_persona(text, cancel, tts=tts)
            return
        segments = crew_mod.parse_segments(text, enabled=True)
        crew_mod.speak_segments(
            segments,
            persona_speak=lambda t: self._speak_persona(t, cancel, tts=tts),
            crew_speak=lambda name, t: self._speak_crew(name, t, cancel),
            cancel=cancel,
        )

    def _speak_persona(self, text: str, cancel: threading.Event, *, tts=None) -> None:  # noqa: ANN001
        """Voice the ship persona (COVAS++) via the TTS provider — the direct path that every reply
        used before crew. `tts` is the turn-local provider (issue #90); None falls back to the live
        `self.tts` for callers outside a turn. Raises on failure after logging LOUDLY so a dead TTS
        stays diagnosable; callers keep their broad guards and degrade to text/Idle."""
        try:
            (tts if tts is not None else self.tts).speak(text, cancel)
        except Exception as e:  # noqa: BLE001 — re-raised after logging; callers fail soft
            msg = f"TTS FAILED ({type(e).__name__}): {e}"
            self._log("system", msg)
            print(f"\n!! {msg}", file=sys.stderr, flush=True)
            raise

    def _speak_crew(self, name: str, text: str, cancel: threading.Event) -> bool:
        """Voice one crew line in its own DETERMINISTIC cast voice (issue #69), delegating to the
        audio layer's cast seam. Returns True if voiced; False degrades that line to the persona
        voice. Never raises — a crew voice failing must not break the reply (fail soft), unlike the
        persona path whose failure is loud + diagnosable."""
        if self.audio is None:
            return False  # no bus mixer / cast available -> speak this line as the persona
        try:
            return self.audio.speak_crew(name, text, cancel)
        except Exception as e:  # noqa: BLE001 — crew is best-effort; degrade to the persona voice
            self._log("system", f"crew voice failed ({name}): {e}")
            return False

    def _log_usage(self, u: dict) -> None:
        """Record a per-call token/cost usage event to the session log + EventBus."""
        line = (f"in={u['input_tokens']} out={u['output_tokens']} "
                f"cache_write={u['cache_creation_input_tokens']} "
                f"cache_read={u['cache_read_input_tokens']} "
                f"~${u['cost_usd']:.4f} [{u['model']}]")
        self._log("usage", line)
        self.bus.publish({"type": "usage", **u})

    def _say(self, text: str, cancel: threading.Event) -> None:
        """Speak a locally-generated response (not from Claude) and log it."""
        self._log("COVAS", text)
        self.set_state("Speaking")
        try:
            self._speak(text, cancel)
        except Exception:  # noqa: BLE001 — _speak already logged loudly; degrade to text
            pass

    def _handle_command(self, text: str, cancel: threading.Event) -> bool:
        """Route deterministic control commands locally (cancel/personality/voice).
        The checklist is intentionally NOT here — Claude handles it via tools so it
        can understand natural phrasing. Returns True if handled. (Phase 5 wip.)"""
        return False

    # ---- worker -----------------------------------------------------------
    def _trim_history(self) -> None:
        cap = int(self.cfg["conversation"]["max_turns"]) * 2
        if len(self.history) > cap:
            self.history = self.history[-cap:]

    def _process(self, audio, cancel: threading.Event, *, wake_gated: bool = False) -> None:
        # Turn-local STT binding (issue #90): capture the provider ONCE so a mid-turn hot-swap
        # (self.stt rebound by _reload_whisper) can't change instances underneath this turn — it
        # finishes on the one it started with; the swap lands on the next turn (decision #2).
        stt = self.stt
        try:
            if cancel.is_set():
                return
            self.set_state("Transcribing")
            text = stt.transcribe(audio)
            if cancel.is_set():
                return
            if not text.strip():
                # Empty OR whitespace-only transcription (silence, or a barge-in that caught
                # near-silence). Drop it — sending an empty/whitespace user turn 400s the API
                # ("messages … must have non-empty content") and poisons later turns.
                self.cues.play("failed")
                self.set_state("Idle", "(no speech detected)")
                return

            # Wake-word gate (issue #64). ONLY the hands-free path is gated (``wake_gated``);
            # a deliberate PTT press is always honoured. When a wake word is configured, an
            # ambient capture must carry it or the turn is dropped BEFORE the LLM — so continuous
            # mode isn't triggered by every stray utterance. The phrase is stripped from what the
            # model sees. Disabled (empty wake_word) -> armed passes straight through unchanged.
            if wake_gated:
                gate = WakeWordGate.from_cfg(self.cfg)
                if gate.enabled:
                    res = gate.check(text)
                    self._log("wake", res.reason)
                    if not res.armed or not res.text:
                        # No wake word (false trigger) or wake word only (nothing to answer):
                        # drop the turn quietly and return to Idle — no tokens spent.
                        self.set_state("Idle", "(no wake word)")
                        return
                    text = res.text  # run the turn on the stripped command
        except Exception as e:  # noqa: BLE001 — a transcription/gate failure must not crash the loop
            self.cues.play("failed")
            self.set_state("Idle", f"error: {e}")
            return
        # Post-transcription: hand off to the shared turn logic (issue #76). _run_turn owns its own
        # fail-soft guard plus the #97 retry/watchdog, so this STT try only has to cover transcription.
        self._run_turn(text, cancel)

    def _run_turn(self, text: str, cancel: threading.Event) -> None:
        """Run ONE full conversation turn from ALREADY-RESOLVED text — the shared spine of a
        spoken turn (:meth:`_process`, post-STT + wake gate) and a TYPED prompt from the control
        panel (:meth:`dispatch_text`, issue #76). Router tiering, ED-context + memory injection,
        capability tools, conversation history, and a spoken TTS reply are identical for both; the
        typed path simply skips STT and logs the same ``Commander: …`` line.

        Reliability (issue #97): transient provider errors are retried with backoff INSIDE the
        provider; a turn that goes silent past the latency threshold speaks an interim "still
        trying" line via the watchdog; exhausted retries speak an in-character, provider-named
        degraded line. Fail soft: any error returns to Idle leaving NO orphaned history turn — the
        user+assistant pair commits atomically only on a successful reply."""
        # Turn-local provider binding (issue #90): capture the LLM + TTS ONCE at the top so a
        # mid-turn hot-swap (self.llm/self.tts rebound by _reload_llm/_reload_tts) can't split a
        # single turn across two provider sets — it runs to completion on the pair it started with;
        # the swap takes effect on the next turn (decision #2). Capture text_only too (issue #90
        # review) so every speak path in this turn — reply, the watchdog "still trying" line, and
        # the degraded apology — gates on the turn's OWN text-only state, not a mid-turn flip.
        llm = self.llm
        tts = self.tts
        text_only = self.text_only
        watchdog = None
        try:
            if cancel.is_set():
                return
            print(f"\nCommander: {text}")
            self._log("Commander", text)

            # New Commander utterance -> advance the confirmation gates, so an armed ship action
            # or a composed comms message can only be confirmed on a genuinely separate command
            # (the model can't arm-and-confirm within one turn). DESIGN §6 safety layer. Comms
            # (#49) uses this as its read-back-before-send gate; the find-closest capability uses
            # the same gate when [nav].require_confirmation is on.
            if self.keybinds is not None:
                self.keybinds.new_turn()
            if self.comms is not None:
                self.comms.new_turn()
            if self.macros is not None:
                self.macros.new_turn()
            if self.nav is not None:
                self.nav.new_turn()

            # Local voice commands (checklist, etc.) — handled without calling Claude
            if self._handle_command(text, cancel):
                self.set_state("Idle")
                return

            # Cost router (DESIGN §4): pick the cheapest capable model + token cap for
            # this turn from the RAW spoken text, then strip any pure tier-control phrase
            # ("use opus", "think hard") so the model never sees it and never comments on
            # a model switch it can't make — it just answers the request. Built fresh from
            # live cfg so UI overrides apply; off by default -> the fixed [anthropic] tier.
            router = Router.from_cfg(self.cfg)
            # Tell the router the HUD is on so bare placement nudges ("bigger", "move it left")
            # escalate to a tier that reliably fires adjust_vr_hud; when it's off they stay cheap
            # and can't over-escalate ordinary chat (issue #48 retest).
            route = router.decide(
                text, {"hud_active": self._hud_enabled() or self._vr_hud_enabled()})
            self._log("router",
                      f"[{route.tier}] {route.model} max_tokens={route.max_tokens} — {route.reason}")
            self.bus.publish({"type": "router", "model": route.model, "tier": route.tier,
                              "max_tokens": route.max_tokens, "reason": route.reason})

            # ED context (DESIGN §5): if monitoring is on and the turn references game
            # state ("where am I", "check my logs"), inject the live telemetry block into
            # THIS call's message only. Stored history keeps the clean text so the (soon
            # stale) telemetry never lingers across turns, and it goes in the user message
            # rather than the cached system prompt so it can't bust the prompt cache.
            user_text = router.strip_control(text)
            llm_text = user_text
            if self.ed_ctx is not None:
                detector = ContextDetector.from_cfg(self.cfg)
                ref = detector.decide(text)
                user_text = detector.strip(user_text)
                llm_text = user_text
                if ref.matched:
                    block = self.ed_ctx.context_block(include_log=ref.wants_log)
                    if block:
                        llm_text = f"{block}\n\n{user_text}"
                    note = ref.reason if block else f"{ref.reason} (no telemetry yet)"
                    self._log("ed-context", note)
                    self.bus.publish({"type": "ed_context", "matched": True,
                                      "wants_log": ref.wants_log, "injected": bool(block),
                                      "reason": ref.reason})

            # Memory recall (#61): if this turn reaches into the past ("do you remember…",
            # "what's my favourite…"), prepend a COMPACT block of relevant stored facts to THIS
            # call only. Composes with the ED block exactly as that one does — it prepends to
            # `llm_text` (which may already carry the ED telemetry) while `history` keeps the
            # clean `user_text`, and it rides the (uncached) user message, NOT the cached system
            # prefix — so recall can't bust the prompt cache. Fail soft: a miss injects nothing.
            if self.memory is not None:
                mref = MemoryDetector.from_cfg(self.cfg).decide(text)
                if mref.matched:
                    mblock = self.memory.recall_block(user_text)
                    if mblock:
                        llm_text = f"{mblock}\n\n{llm_text}"
                    note = mref.reason if mblock else f"{mref.reason} (no matching memory)"
                    self._log("memory-recall", note)
                    self.bus.publish({"type": "memory_recall", "matched": True,
                                      "injected": bool(mblock), "reason": mref.reason})

            if not user_text.strip():
                # The utterance stripped down to nothing to answer — e.g. a bare tier-control
                # phrase ("use opus", "think hard"): the router already applied the tier, and
                # sending an empty user turn 400s the API ("messages must have non-empty
                # content"). Acknowledge and return without touching history.
                self.cues.play("failed")
                self.set_state("Idle", "(nothing to answer)")
                return
            # Build THIS call's messages WITHOUT mutating stored history. A cancelled, errored,
            # or empty-reply turn must leave NO trace: an orphaned user turn (appended before the
            # call with no assistant reply) poisons the next call — the model answers the stale
            # question and the API 400s on the malformed history. So we commit the user+assistant
            # PAIR together only after a successful reply (below). The per-turn message carries the
            # context-augmented `llm_text`; what we PERSIST is the clean `user_text`, so telemetry/
            # recall never linger across turns.
            messages = self.history + [{"role": "user", "content": llm_text}]

            self.set_state("Thinking")

            think = {"buf": "", "shown": False}
            seen_search = {"q": None}

            def on_event(kind: str, data: str) -> None:
                if kind == "search":
                    if data and data != seen_search["q"]:
                        seen_search["q"] = data
                        self.set_state("Searching", data)
                        self.bus.publish({"type": "log", "who": "system",
                                          "text": f"Searching the web for {data}"})
                        print(f"\n>> Searching the web for {data}")
                        self.cues.play("processing")   # audible "working" cue
                elif kind == "thinking":
                    think["buf"] += data
                elif kind == "tool":
                    if data.endswith("_objective") or data.endswith("_objectives"):
                        self.set_state("Thinking", "checklist")
                        self.bus.publish({"type": "log", "who": "system",
                                          "text": "Consulting the ultimate checklist"})
                elif kind == "usage":
                    self._log_usage(data)
                elif kind == "retry":
                    # A transient provider blip being retried (issue #97). Surface it so the log
                    # SHOWS the backoff instead of just a mysterious pause before the reply arrives.
                    # Route through _log (file + bus), like the router line, so it lands wherever the
                    # Commander reads the log — NOT bus.publish alone (that skips the log file).
                    d = data if isinstance(data, dict) else {}
                    prov = d.get("provider") or "LLM"
                    reason = d.get("reason") or "transient error"
                    self.set_state("Thinking", "retrying")
                    self._log("retry", f"{prov} {reason} — retry {d.get('attempt')}/"
                                       f"{d.get('attempts')}, backing off "
                                       f"{float(d.get('delay') or 0.0):.1f}s")

            def flush_thinking() -> None:
                if think["buf"].strip() and not think["shown"]:
                    think["shown"] = True
                    summary = " ".join(think["buf"].split())[:240]
                    self.bus.publish({"type": "log", "who": "thinking",
                                      "text": summary})
                    print(f"\n>> [approach] {summary}")

            reply = ""
            print("COVAS: ", end="", flush=True)
            # Latency watchdog (issue #97): armed for the LLM call only — a slow first token, a
            # retry/backoff, or a hung connection all live here. It speaks a plain-language "still
            # trying" line once past the threshold; disarmed the instant the reply is in hand (the
            # finally), which is just before any reply audio plays.
            watchdog = self._arm_latency_watchdog(cancel, tts=tts, text_only=text_only)
            try:
                stream = llm.stream_reply(
                    messages, cancel, on_event,
                    tool_handler=self.registry.run_tool,
                    tools=self.registry.tools_for_level(self.tier_level),
                    model=route.model, max_tokens=route.max_tokens)
                for kind, chunk in stream:
                    if cancel.is_set():
                        break
                    if kind == "text":
                        flush_thinking()  # thinking precedes the spoken text
                        reply += chunk
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
            finally:
                watchdog.disarm()
            print()

            if cancel.is_set():
                self.set_state("Idle", "cancelled")
                return
            if not reply.strip():
                self.cues.play("failed")
                self.set_state("Idle", "(empty reply)")
                return                      # history untouched — no orphaned user turn

            # Success: commit the user+assistant pair atomically. Persist the CLEAN user_text
            # (not the augmented llm_text) so per-turn context never lingers; trim AFTER so the
            # stored history stays bounded.
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": reply})
            self._trim_history()
            self._log("COVAS", reply)

            if cancel.is_set():
                self.set_state("Idle")
                return
            # Reply is ready: silence the "thinking" bed BEFORE the completed chime so the two
            # don't overlap, then chime and speak (set_state("Speaking") is the belt-and-braces stop).
            self.cues.stop_loop()
            self.cues.play("done", wait=True)
            if cancel.is_set():
                self.set_state("Idle")
                return

            self.set_state("Speaking")
            self._speak(reply, cancel, tts=tts, text_only=text_only)
            self.set_state("Idle")
        except Exception as e:  # noqa: BLE001 — keep the loop alive on any failure
            if watchdog is not None:
                watchdog.disarm()  # idempotent — the stream's finally already ran on most paths
            self.cues.play("failed")
            # Issue #97: a transient/overloaded provider (exhausted retries, connection drop, 529)
            # earns an in-character, provider-named spoken heads-up instead of a bare error; any
            # other failure just degrades soft as before. Never another LLM call — the LLM is what's
            # down. History is untouched here (the commit is after a successful reply), so a failed
            # turn leaves NO orphan.
            if _retry.is_degraded_error(e):
                self._speak_degraded(e, cancel, tts=tts, text_only=text_only)
            self.set_state("Idle", f"error: {e}")

    # ---- provider reliability (issue #97) ---------------------------------
    def _arm_latency_watchdog(self, cancel: threading.Event, *, tts=None,  # noqa: ANN001
                              text_only=None) -> "_LatencyWatchdog":
        """Arm the >Ns latency watchdog for a turn (issue #97). Threshold from
        ``[llm].slow_warning_seconds`` (default 30; <=0 disables). When it fires it speaks a canned,
        plain-language "the AI service is being slow, still trying" line in the CURRENT voice via
        the normal `_speak` path (degrading to a logged line in text-only mode) — never another LLM
        call. Always returns a watchdog object (disabled ones no-op) so callers can disarm blindly.
        `tts`/`text_only` are the turn-locals (issue #90 review) so this interim line stays on the
        turn's own provider + text-only state, never split across a mid-turn hot-swap."""
        seconds = float((self.cfg.get("llm", {}) or {}).get("slow_warning_seconds", 30) or 0)
        line = "Sorry, Commander — the AI service is being slow to respond. I'm still trying."

        def _interim() -> None:
            if self.text_only if text_only is None else text_only:
                self._log("COVAS", line)  # no voice — surface it as text so it's never silent
            else:
                self._speak(line, cancel, tts=tts, text_only=text_only)

        return _LatencyWatchdog(
            seconds, speak=_interim, cancel=cancel,
            log=lambda m: self._log("system", m)).arm()

    def _provider_display_name(self) -> str:
        """Friendly, in-character name for the ACTIVE LLM provider, for the degraded line. Kept
        provider-agnostic — the OpenAI-compatible seam fronts many services (Groq/DeepSeek/…), so
        it gets a generic label rather than a wrong brand."""
        name = str((self.cfg.get("llm", {}) or {}).get("provider", "anthropic")).lower()
        return {"anthropic": "Claude", "gemini": "Gemini",
                "openai": "The AI service", "ollama": "The local model"}.get(name, "The AI service")

    def _speak_degraded(self, err: Exception, cancel: threading.Event, *, tts=None,  # noqa: ANN001
                        text_only=None) -> None:
        """Tell the Commander, in character, that the provider is overloaded (issue #97) and log
        the precise reason. Canned/verbatim — the LLM is the thing that's down, so we NEVER call it
        to narrate its own outage. Fail soft: degrade to a logged line when TTS is unavailable and
        never raise. `tts`/`text_only` are the turn-locals (issue #90 review) so the apology stays
        on the turn's own provider + text-only state through a mid-turn hot-swap."""
        name = self._provider_display_name()
        line = f"{name} is overloaded right now, Commander — give it a moment and try again."
        self._log("system", f"provider degraded: {_retry.degraded_reason(err)}")
        if self.text_only if text_only is None else text_only:
            self._log("COVAS", line)
            return
        try:
            self._speak(line, cancel, tts=tts, text_only=text_only)
        except Exception:  # noqa: BLE001 — _speak already logged loudly; degrade to text
            self._log("COVAS", line)

    # ---- run --------------------------------------------------------------
    def start(self) -> None:
        """Install the global key hooks (non-blocking). Used by both the
        headless entry point and the web-UI entry point."""
        # Resolve the PTT/cancel/reflex scan-code sets into instance attributes (issue #90). The
        # reflex second-PTT (issue #38) is default-unbound = its branch never fires; its set already
        # subtracts the PTT/cancel codes so a mis-configuration (same key) can't double-dispatch —
        # the main PTT keeps priority. on_key reads these live, so a later hotkey change applies via
        # _reconcile_hotkeys() with NO re-hook.
        self._resolve_hotkeys()
        print(f"(PTT scan codes {sorted(self._ptt_codes)}, "
              f"cancel {sorted(self._cancel_codes) or 'tap-PTT'}, "
              f"reflex {sorted(self._reflex_codes) or 'unbound'})")

        def on_key(e):  # noqa: ANN001
            if e.scan_code in self._ptt_codes:
                if e.event_type == "down" and not self.ptt_held:
                    self.ptt_held = True
                    self.on_ptt_down()
                elif e.event_type == "up" and self.ptt_held:
                    self.ptt_held = False
                    self.on_ptt_up()
            elif e.scan_code in self._reflex_codes:
                # Reflex fast-path capture (issue #38) — separate from the main PTT so a snap combat
                # call isn't queued behind the conversation turn.
                if e.event_type == "down" and not self.reflex_held:
                    self.reflex_held = True
                    self.on_reflex_ptt_down()
                elif e.event_type == "up" and self.reflex_held:
                    self.reflex_held = False
                    self.on_reflex_ptt_up()
            elif e.scan_code in self._cancel_codes and e.event_type == "down":
                self.on_cancel()

        keyboard.hook(on_key)
        keyboard.add_hotkey("ctrl+alt+q", self.request_quit)
        # Open the shared audio device (the ONLY thing that opens it, when the layer is on). If
        # the device won't open, fall BACK to the legacy direct path — otherwise COVAS speech
        # would stream into a mixer that never drains and block. Rebuild tts/cues off the mixer.
        if self.mixer is not None:
            try:
                self.mixer.start()
            except Exception as e:  # noqa: BLE001 — degrade to direct playback, never block startup
                self._log("system",
                          f"audio mixer failed to open the device: {e}; using direct playback.")
                try:
                    if self.audio is not None:
                        self.audio.set_muted(True)
                except Exception:  # noqa: BLE001
                    pass
                self.mixer = None
                self.audio = None
                self.cues = CuePlayer(self.cfg)
                if not self.mock:
                    try:
                        self.tts = make_tts(self.cfg)
                    except Exception:  # noqa: BLE001 — keep whatever tts we had
                        pass
        # Hands-free continuous listening (issue #63): start the VAD mic listener now if
        # [listen].mode = "continuous". Fail-soft — a mic that won't open falls back to PTT.
        self._reconcile_listener()
        self.set_state("Idle")

    def wait_for_quit(self) -> None:
        """Block until a quit is requested (Ctrl+Alt+Q sets the event). Used by both entry
        points — the headless loop waits on it directly, and the web UI bridges it on a
        thread since Flask blocks the main thread itself (see run_covas_ui.py)."""
        self._quit.wait()

    def request_quit(self) -> None:
        """Signal the app to shut down (wired to the Ctrl+Alt+Q hotkey)."""
        self._quit.set()

    def shutdown(self) -> None:
        """Stop the watchers/pump and close the log. Safe to call once on the way out."""
        self._stop_event_pump()
        self._stop_ed_monitoring()
        self._stop_listener()  # close the hands-free mic listener if it's running (issue #63)
        if self.hud is not None:
            try:
                self.hud.shutdown()  # tear the overlay window down
            except Exception:  # noqa: BLE001 — never let cleanup raise on exit
                pass
        if self.mixer is not None:
            try:
                self.mixer.stop()
            except Exception:  # noqa: BLE001 — never let cleanup raise on exit
                pass
        try:
            self._logf.close()
        except Exception:  # noqa: BLE001 — never let cleanup raise on exit
            pass

    def run(self) -> None:
        try:
            self.start()
        except ValueError as e:
            print(f"Bad key name in config [keys]: {e}")
            return
        print(_banner(self.cfg))
        try:
            self.wait_for_quit()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
            print("\nCOVAS++ shutting down. o7")


def _resolve_codes(name: str) -> set[int]:
    """Scan codes for a key name. For a left/right variant (e.g. 'right ctrl'),
    drop codes shared with the opposite side so only that physical key matches."""
    codes = set(keyboard.key_to_scan_codes(name))
    low = name.lower()
    opposite = None
    if low.startswith("right "):
        opposite = "left " + low[6:]
    elif low.startswith("left "):
        opposite = "right " + low[5:]
    if opposite:
        try:
            side_specific = codes - set(keyboard.key_to_scan_codes(opposite))
            if side_specific:            # keep only if something remains
                codes = side_specific
        except ValueError:
            pass
    return codes


def _cost_summary(cfg: dict, mock: bool) -> str:
    """One-line summary of the settings that drive cost, for the startup log."""
    a = cfg["anthropic"]
    ws = cfg.get("web_search", {})
    rt = cfg.get("router", {})
    router = (f"on(default={rt.get('default_model', '?')})" if rt.get("enabled")
              else f"off(fixed={a['model']})")
    return (
        "cost settings — "
        f"router={router} "
        f"model={a['model']} "
        f"thinking={a.get('thinking', {}).get('default', 'Off')} "
        f"max_tokens={a['max_tokens']} "
        f"web_search={'on' if ws.get('enabled') else 'off'}(max_uses={ws.get('max_uses', '?')}) "
        f"cache_ttl={a.get('cache_ttl', '1h')} "
        f"mock={'on' if mock else 'off'}"
    )


def _banner(cfg: dict) -> str:
    k = cfg["keys"]
    p = "ON" if cfg["personality"]["enabled"] else "OFF"
    mock = mock_enabled(cfg)
    rt = cfg.get("router", {})
    router = (f"ON (default {rt.get('default_model', '?')})" if rt.get("enabled")
              else f"OFF (fixed {cfg['anthropic']['model']})")
    ed = "ON" if cfg.get("elite", {}).get("enabled") else "OFF"
    pro = "ON" if cfg.get("proactive", {}).get("enabled") else "OFF"
    kb = "ON" if cfg.get("keybinds", {}).get("enabled") else "OFF"
    reflex_on = cfg.get("reflex", {}).get("enabled")
    reflex_ptt = str((cfg.get("reflex", {}) or {}).get("ptt", "")).strip()
    auto_reflex_on = (cfg.get("reflex", {}) or {}).get("auto", {}).get("enabled")
    reflex = (("ON" if reflex_on else "OFF")
              + (f" (fast-PTT [{reflex_ptt}])" if reflex_ptt else "")
              + (" (auto ON)" if auto_reflex_on else ""))
    honk = "ON" if cfg.get("honk", {}).get("enabled") else "OFF"
    nav = "ON" if cfg.get("nav", {}).get("enabled") else "OFF"
    return (
        "\n================ COVAS++ (Phase 2) ================\n"
        f"  Router     : {router}\n"
        f"  Model      : {cfg['anthropic']['model']}\n"
        f"  Voice      : {cfg['elevenlabs']['voice_name']}\n"
        f"  Whisper    : {cfg['whisper']['model']}\n"
        f"  ED monitor : {ed}\n"
        f"  Proactive  : {pro}\n"
        f"  Keybinds   : {kb}\n"
        f"  Reflexes   : {reflex}\n"
        f"  Auto-honk  : {honk}\n"
        f"  Find module: {nav}\n"
        f"  Personality: {p}\n"
        f"  Cache TTL  : {cfg['anthropic'].get('cache_ttl', '1h')}\n"
        f"  Dev mock   : {'ON' if mock else 'OFF'}\n"
        f"  TALK        : hold  [{k['push_to_talk']}]\n"
        f"  CANCEL      : tap   [{k['push_to_talk']}] briefly\n"
        f"  QUIT        : Ctrl+Alt+Q (or close this window)\n"
        "==================================================\n"
        "Hold the PTT key and speak, Commander.\n"
    )
