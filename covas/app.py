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
from .events import EventBus
from .checklist import Checklist
from .capabilities import CapabilityRegistry
from .capabilities.checklist_capability import ChecklistCapability
from .providers.base import LLMProvider, STTProvider, TTSProvider
from .providers.factory import make_llm, make_stt, make_tts
from .router import Router
from .ed import ContextDetector

# Claude replies can contain Unicode (arrows, em-dashes, emoji) the default Windows
# console can't encode. Make console output lossy-safe so a stray glyph never crashes
# the worker mid-reply.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — older/odd streams may lack reconfigure
        pass

STATES = ("Idle", "Listening", "Transcribing", "Thinking", "Searching", "Speaking")
# States where COVAS is heads-down WORKING on a spoken turn — the window the soft "thinking" bed
# (issue #5) fills. Entering one (arms, enabled) starts the bed; entering any OTHER state stops it,
# which is what wires the stop into every exit path (reply -> Speaking, cancel/error -> Idle,
# barge-in -> Listening) through the single set_state chokepoint.
_WORKING_STATES = frozenset({"Transcribing", "Thinking", "Searching"})


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
            self.registry.register(ChecklistCapability(self.checklist))

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

        self.history: list[dict] = []
        self.active_cancel: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.ptt_held = False
        self._ptt_t0 = 0.0  # key-down time, for tap-vs-hold detection
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
        # Shared scancode executor + parsed .binds, built once and reused by keybinds and
        # auto-honk so a hard abort releases keys held by either (and the .binds file is parsed
        # a single time). Lazily populated by the helpers below.
        self._shared_executor = None
        self._binds_cache: dict | None = None
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
        self._proactive_lock = threading.Lock()
        self._pump: threading.Thread | None = None
        self._pump_q: queue.Queue | None = None
        self._pump_stop = threading.Event()

        self._logf = self._open_log()
        self._log("system", _cost_summary(self.cfg, self.mock))
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
        if self.cfg.get("nav", {}).get("enabled"):
            self._start_nav()
            self._start_ship_nav()
        if self.cfg.get("star_systems", {}).get("enabled"):
            self._start_system_search()
        if self.cfg.get("search", {}).get("enabled"):
            self._start_searches()
        if self.cfg.get("route_plan", {}).get("enabled"):
            self._start_route_plan()
        if self.cfg.get("neutron_plan", {}).get("enabled"):
            self._start_neutron_plan()
        if self.cfg.get("riches_plan", {}).get("enabled"):
            self._start_riches_plan()
        # C9: compose the audio layer once the mixer, providers, and ED context all exist.
        if self.mixer is not None:
            self._start_audio_layer()

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

    # ---- Elite Dangerous monitoring (DESIGN §5) ---------------------------
    def _start_ed_monitoring(self) -> None:
        """Build the shared context + ED-context capability and start the journal/status
        watchers. Fail soft: a missing directory or import problem must not stop the app
        from starting — ED monitoring just stays dark until the next run."""
        try:
            from .ed import (EDContext, JournalWatcher, StatusWatcher,
                             resolve_journal_dir, status_path)
            from .capabilities.ed_context_capability import EDContextCapability
            from .capabilities.loadout_capability import LoadoutCapability

            el = self.cfg.get("elite", {})
            jdir = resolve_journal_dir(self.cfg)
            self.ed_ctx = EDContext(recent_maxlen=int(el.get("recent_events_kept", 25)))
            self.registry.register(EDContextCapability(self.ed_ctx))
            # Ship loadout & engineering (N9): reads the snapshot the journal watcher keeps
            # on EDContext. Registered with monitoring since that's its only data source.
            self.registry.register(LoadoutCapability(
                get_loadout=self.ed_ctx.loadout_snapshot,
                log=lambda m: self._log("loadout", m)))
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
            stream = self.llm.stream_reply(
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
            self._speak(reply, cancel)
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
                log=lambda msg: self._log("keybind", msg))
            self.registry.register(self.keybinds)

            # Report per-macro readiness so the manual test knows what's wired.
            for macro in self.keybinds._allowed_macros():
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
        if self.active_cancel is not None and not self.active_cancel.is_set():
            self.active_cancel.set()
        self.cues.stop()  # stop any cue still playing

    # ---- PTT / cancel handlers -------------------------------------------
    def on_ptt_down(self) -> None:
        self._ptt_t0 = time.monotonic()
        # Hold the proactive lock across the interrupt + state flip so a callout can't slip
        # its idle-claim in between and end up speaking over this capture. Either the claim
        # loses (sees "Listening" -> skips) or it already won (this interrupt cancels it).
        with self._proactive_lock:
            self._interrupt()        # interrupt any current thinking/speaking (incl. a callout)
            self.set_state("Listening")
        self.recorder.start()
        self.cues.play("listening")

    def on_ptt_up(self) -> None:
        audio = self.recorder.stop()
        held_ms = (time.monotonic() - self._ptt_t0) * 1000.0
        tap_ms = float(self.cfg["keys"].get("tap_cancel_ms", 400))
        if held_ms < tap_ms:
            # Brief tap = cancel. The in-flight op was already aborted on key-down;
            # just drop this (empty) capture and return to Idle.
            self.cues.stop()
            self.set_state("Idle", "cancelled")
            return
        self.cues.play("processing")
        # Arm the soft "thinking" bed for THIS user turn (issue #5): the one-shot processing tick
        # above acknowledges receipt; the bed then fills the transcribe/think/search wait. The
        # worker's first set_state("Transcribing") starts it; any exit stops it (see set_state).
        self._bed_armed = True
        cancel = threading.Event()
        self.active_cancel = cancel
        self.worker = threading.Thread(
            target=self._process, args=(audio, cancel), daemon=True
        )
        self.worker.start()

    def on_cancel(self) -> None:
        self._interrupt()
        self.set_state("Idle", "cancelled")

    # public alias for the UI cancel button / voice command
    def trigger_cancel(self) -> None:
        self.on_cancel()

    # ---- live settings (from the web UI) ----------------------------------
    def update_settings(self, patch: dict) -> None:
        """Merge a settings patch into the running config, persist it to
        overrides.json, and reload anything that needs it (Whisper model)."""
        old_whisper = dict(self.cfg["whisper"])
        deep_merge(self.cfg, patch)
        deep_merge(self.overrides, patch)
        save_overrides(self.overrides)
        self._after_settings_change(old_whisper)

    def reset_setting(self, path) -> None:
        """Reset ONE setting to its config.toml default by dropping it from
        overrides.json (the file's own reset mechanism) and reloading config.
        Reloads Whisper too if that's the setting that changed."""
        old_whisper = dict(self.cfg["whisper"])
        _pop_path(self.overrides, tuple(path))
        _prune_empty(self.overrides)
        save_overrides(self.overrides)
        # Re-derive the effective config from config.toml + the remaining
        # overrides (paths re-resolved), keeping the same dict identity so any
        # holder of self.cfg sees the update.
        fresh = load_config()
        self.cfg.clear()
        self.cfg.update(fresh)
        self._after_settings_change(old_whisper)

    def _after_settings_change(self, old_whisper: dict) -> None:
        """Shared tail for update/reset: broadcast the new settings and reload
        Whisper in the background if its model/device/compute changed."""
        self.bus.publish({"type": "settings", "settings": self.public_settings()})
        # Audio settings (bus volumes, enable toggles, comms treatment) apply live.
        if self.audio is not None:
            try:
                self.audio.apply_settings()
            except Exception as e:  # noqa: BLE001 — a settings glitch must not crash the loop
                self._log("system", f"audio settings apply failed: {e}")
        w = self.cfg["whisper"]
        if (w["model"], w["device"], w["compute_type"]) != (
            old_whisper["model"], old_whisper["device"], old_whisper["compute_type"]
        ):
            self.set_state(self.state, f"reloading Whisper: {w['model']}")
            threading.Thread(target=self._reload_whisper, daemon=True).start()

    def _reload_whisper(self) -> None:
        try:
            self.stt = make_stt(self.cfg)
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Whisper model reloaded: {self.cfg['whisper']['model']}"})
        except Exception as e:  # noqa: BLE001
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Whisper reload failed: {e}"})

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
        return None

    def public_settings(self) -> dict:
        c = self.cfg
        return {
            "model": c["anthropic"]["model"],
            "thinking": c["anthropic"]["thinking"].get("default", "Off"),
            "web_search": bool(c["web_search"]["enabled"]),
            "personality": bool(c["personality"]["enabled"]),
            "el_model": c["elevenlabs"]["model"],
            "el_voice": c["elevenlabs"]["voice_id"],
            "el_voice_name": c["elevenlabs"].get("voice_name", ""),
            "speed": c["elevenlabs"].get("speed", 1.0),
            "whisper": c["whisper"]["model"],
        }

    # ---- local voice commands --------------------------------------------
    def _speak(self, text: str, cancel: threading.Event) -> None:
        """Play `text` through the injected TTS provider (real, mock, or fake).
        On failure, log LOUDLY (session log + stderr) before re-raising — a dead TTS must be
        diagnosable, not a silent no-op (e.g. a 401 famous_voice_not_permitted). Callers keep
        their broad guards and still degrade to text/Idle. In text-only mode (no ElevenLabs key)
        there is no TTS to attempt — the reply is already shown as text — so skip quietly; that
        loud path is for a CONFIGURED-but-broken TTS, not the intended keyless mode."""
        if self.text_only:
            return
        try:
            self.tts.speak(text, cancel)
        except Exception as e:  # noqa: BLE001 — re-raised after logging; callers fail soft
            msg = f"TTS FAILED ({type(e).__name__}): {e}"
            self._log("system", msg)
            print(f"\n!! {msg}", file=sys.stderr, flush=True)
            raise

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

    def _process(self, audio, cancel: threading.Event) -> None:
        try:
            if cancel.is_set():
                return
            self.set_state("Transcribing")
            text = self.stt.transcribe(audio)
            if cancel.is_set():
                return
            if not text:
                self.cues.play("failed")
                self.set_state("Idle", "(no speech detected)")
                return
            print(f"\nCommander: {text}")
            self._log("Commander", text)

            # New Commander utterance -> advance the keybind confirmation gate, so an armed
            # ship action can only be confirmed on a genuinely separate command (the model
            # can't arm-and-confirm within one turn). DESIGN §6 safety layer. The find-closest
            # capability uses the same gate when [nav].require_confirmation is on.
            if self.keybinds is not None:
                self.keybinds.new_turn()
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
            route = router.decide(text)
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

            self.history.append({"role": "user", "content": user_text})
            self._trim_history()
            # For this call only, swap the last user turn for its context-augmented form.
            messages = self.history
            if llm_text != user_text:
                messages = self.history[:-1] + [{"role": "user", "content": llm_text}]

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

            def flush_thinking() -> None:
                if think["buf"].strip() and not think["shown"]:
                    think["shown"] = True
                    summary = " ".join(think["buf"].split())[:240]
                    self.bus.publish({"type": "log", "who": "thinking",
                                      "text": summary})
                    print(f"\n>> [approach] {summary}")

            reply = ""
            print("COVAS: ", end="", flush=True)
            stream = self.llm.stream_reply(
                messages, cancel, on_event,
                tool_handler=self.registry.run_tool, tools=self.registry.tools(),
                model=route.model, max_tokens=route.max_tokens)
            for kind, chunk in stream:
                if cancel.is_set():
                    break
                if kind == "text":
                    flush_thinking()  # thinking precedes the spoken text
                    reply += chunk
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            print()

            if cancel.is_set():
                self.set_state("Idle", "cancelled")
                return
            if not reply.strip():
                self.cues.play("failed")
                self.set_state("Idle", "(empty reply)")
                return

            self.history.append({"role": "assistant", "content": reply})
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
            self._speak(reply, cancel)
            self.set_state("Idle")
        except Exception as e:  # noqa: BLE001 — keep the loop alive on any failure
            self.cues.play("failed")
            self.set_state("Idle", f"error: {e}")

    # ---- run --------------------------------------------------------------
    def start(self) -> None:
        """Install the global key hooks (non-blocking). Used by both the
        headless entry point and the web-UI entry point."""
        keys = self.cfg["keys"]
        ptt_codes = _resolve_codes(keys["push_to_talk"])
        cancel_key = str(keys.get("cancel", "")).strip()
        cancel_codes = _resolve_codes(cancel_key) if cancel_key else set()
        print(f"(PTT scan codes {sorted(ptt_codes)}, cancel {sorted(cancel_codes) or 'tap-PTT'})")

        def on_key(e):  # noqa: ANN001
            if e.scan_code in ptt_codes:
                if e.event_type == "down" and not self.ptt_held:
                    self.ptt_held = True
                    self.on_ptt_down()
                elif e.event_type == "up" and self.ptt_held:
                    self.ptt_held = False
                    self.on_ptt_up()
            elif e.scan_code in cancel_codes and e.event_type == "down":
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
