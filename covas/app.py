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

from .config import (load_config, load_overrides, save_overrides, deep_merge,
                     mock_enabled, experimental)
from . import settings_schema as schema
from .audio import CuePlayer, Recorder
from .listen import VadListener
from .wake import WakeWordGate
from .reflex_spotter import PhraseSpotter
from .events import EventBus
from .checklist import Checklist
from .capabilities import CapabilityRegistry
from .providers.base import LLMProvider, STTProvider, TTSProvider
from .providers.factory import make_llm, make_stt, make_tts
from .providers import _retry
from .router import Router
from . import tiering
from .ed import ContextDetector
from .keybinds.abort import AbortController
from .memory import MemoryDetector
from . import crew as crew_mod
from . import bootstrap
from .persona_speech import PersonaSpeechArbiter, Priority

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
_LLM_SECTIONS: tuple[str, ...] = ("llm", "anthropic", "openai", "gemini")
_TTS_SECTIONS: tuple[str, ...] = (
    "tts", "elevenlabs", "edge", "azure", "openai_tts", "cartesia", "piper")

# The settings the running VOICE LOOP does not live-reconcile, as schema keys. Most need a RESTART
# (decision #5); ui.theme is the exception — it's a WEB-ONLY setting the app never has to act on
# (the control panel applies it live in the browser and server-renders it on the next paint), so
# from the app's side it's a no-op either way and sits with the other non-reconciled ui.* keys:
#   audio.enabled / audio.mix_sample_rate — the bus-mixer graph is cross-wired and the shared
#     output device opened at init/start (see the load-bearing fallback around BusMixer.start);
#   ui.host / ui.port — bound by Flask when the control panel launches;
#   ui.theme — control-panel colours only (issue #104); no app-side effect, applied live by the UI.
# (dev.mock — the fakes swap at the composition root — is dev/test-only and no longer a UI Setting
#  (issue #130), so it's not in this set; it's set via config.toml [dev] / COVAS_MOCK before launch.)
# Explicitly NOT here (all live): provider/base_url/model/key/voice (rebuild), whisper.* (reload),
# keys.* + reflex.ptt (hotkey reconcile), audio.input_device (mic reconcile), volumes/toggles
# (audio.apply_settings), listen.* (listener reconcile).
RESTART_REQUIRED: frozenset[str] = frozenset({
    "audio.enabled", "audio.mix_sample_rate", "ui.host", "ui.port", "ui.theme",
})
# Top-level config sections that apply LIVE. Single source of truth paired with RESTART_REQUIRED:
# the drift-guard unit test asserts every settings_schema key falls under LIVE_SECTIONS ∪
# RESTART_REQUIRED, so a NEW setting in an unclassified section fails the test until it's placed.
LIVE_SECTIONS: tuple[str, ...] = (
    "llm", "openai", "gemini", "tts", "edge", "azure", "openai_tts",
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
        # Capability construction lives in covas/bootstrap.py (issue #113): bootstrap.wire(self)
        # near the end of __init__ builds every capability from an ordered manifest. These shared /
        # multi-bind defaults have no single manifest entry, so they're declared here to exist even
        # when their (gated) builder never runs (ED context + carriers/CG; auto-reflex/build_reflex).
        self.ed_ctx = None
        self._ed_watchers: list = []
        self.carriers = None      # registered by build_ed_monitoring (Location & carrier, N3)
        self.cg = None            # registered by build_ed_monitoring (Community Goals, N6)
        self.auto_reflex = None   # built inside build_reflex (ambient auto-reflex, #37)

        # Auto persona->voice pairing state (issue #96), driven by the bootstrap pairing worker +
        # reconcile: `_voice_pairings` = {persona(lower) -> voice_id}, `_voice_names` = voice_id ->
        # display name, `_applying_persona_voice` guards reconcile re-entry during our own apply.
        self._voice_pairings: dict[str, str] = {}
        self._voice_names: dict[str, str] = {}
        self._applying_persona_voice = False
        # Crew best-fit voice pairing (issue #124): the same background pairing machine run over
        # the crew roster instead of the shipped personas, into a SEPARATE cache so a roster edit
        # never busts the persona cache. `_crew_voice_pairings` = {member name (lower) -> voice_id};
        # display names land in the SHARED `_voice_names` above. Pushed to the audio layer (which
        # applies the precedence: explicit voice_ref > crew pairing > deterministic assign) via
        # `AudioLayer.set_crew_pairings` once the background worker completes.
        self._crew_voice_pairings: dict[str, str] = {}

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

        # Shared scancode executor + parsed .binds + window focuser: built ONCE and reused across
        # keybinds, reflex, honk, comms and macros so a single hard abort releases keys held by any
        # of them (and the .binds file is parsed once). The bootstrap shared factories
        # (ed_binds/key_executor/window_focuser) populate these lazily; the handles live here.
        self._shared_executor = None
        # Shared window focuser (#105) — foregrounds ED before injection; None off-Windows.
        self._shared_focuser = None
        self._binds_cache: dict | None = None
        # Shared hard-abort coordinator so ONE "abort" stops a running sequence started by EITHER
        # the keybind capability or a custom macro (they share the executor too). Created once
        # here. Per-run abort tokens (#154) mean a concurrently-starting run can't wipe an abort
        # meant for a still-running one — the old single Event overloaded set/clear did.
        self._keybind_abort = AbortController()
        # True once the control panel (Flask) is up — set by web.create_app via
        # note_web_ui_started(). The web HUD (#103) needs it: /hud is only served under
        # run_covas_ui.py, never headless run_covas.py.
        self._web_ui_running = False
        self._proactive_lock = threading.Lock()
        # The ONE speech arbiter for the persona (Ship's-AI) voice (issue #146): every persona
        # line — replies, proactive/route callouts, AND the audio layer's ambient PERSONA cues
        # (which used to speak uncoordinated on the same COVAS bus and MIX) — enqueues here and a
        # single speaker thread plays them one at a time (priority + freshness + preempt). Built
        # once here so `_speak` and the AudioLayer (wired in bootstrap) share it. Its default_speak
        # is the direct persona TTS; most app lines pass their own crew-splitting thunk. The
        # speaker thread is daemon + lazily started, so a test that never speaks pays nothing.
        self.persona_arbiter = PersonaSpeechArbiter(
            self._speak_persona,
            log=lambda m: self._log("audio", m),
            max_depth=int((self.cfg.get("audio", {}) or {}).get("persona_queue_depth", 8)))
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
        # Construct + register every capability from the ordered manifest (issue #113), replacing
        # the gated block + inline constructions that used to live here.
        bootstrap.wire(self)

    def _scan_audio_content(self):
        """Scan the C11 drop-in content folders and return the ContentBundle. The root is the
        writable data dir (project root in a source run, %APPDATA%\\COVAS++ when frozen);
        [audio].content_root overrides it (the seam so tests don't touch the repo). Ensures the
        folder skeleton first (idempotent). Shared by startup and the live reload (issue #110).
        Fail-soft — a skeleton write error never blocks the scan."""
        from .config import data_dir
        from .mixer import ensure_skeleton, load_content
        content_root = self.cfg.get("audio", {}).get("content_root") or data_dir()
        try:
            ensure_skeleton(content_root)
        except Exception:  # noqa: BLE001 — skeleton creation must never block a scan
            pass
        return load_content(content_root)

    def reload_audio_content(self) -> dict:
        """Re-scan the C11 drop-in ambient content (SFX/music/chatter/threat) and hot-swap it into
        the live AudioLayer — no restart (issue #110). Pairs with `CuePlayer.reload` (issue #109) so
        the control panel's ONE Reload cues action refreshes BOTH the turn-stage cues and the
        ambient drop-ins. Returns per-category counts, or {} when the audio layer never came up.
        Fail-soft — a user-initiated reload never crashes the loop."""
        if self.audio is None:
            return {}
        try:
            return self.audio.reload_content(self._scan_audio_content())
        except Exception as e:  # noqa: BLE001 — a reload must never take down the app
            self._log("audio", f"ambient content reload failed: {e}")
            return {}

    def _build_cast_synth(self):
        """The C10 cast synth router — thin seam onto :func:`bootstrap.build_cast_synth` (issue
        #113). Stays an App method because the live TTS-reload path (:meth:`_reload_tts`) rebuilds
        the cast through ``self._build_cast_synth()`` and its test double patches this attribute."""
        return bootstrap.build_cast_synth(self)

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

    def _stop_ed_monitoring(self) -> None:
        for w in self._ed_watchers:
            try:
                w.stop()
            except Exception:  # noqa: BLE001
                pass

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

    def _speak_proactive(self, event_name: str, event: dict,
                         *, prompt_override: str | None = None) -> bool:
        """Originate a spoken callout for an ED event, WITHOUT a PTT press. Returns True
        only if we actually started: we speak only when Idle, so a callout never interrupts
        an in-progress user turn — the Commander always has the floor. The line is generated
        on the cheap tier and spoken through the existing cancel path, so a PTT press
        mid-callout cancels it like any other utterance (on_ptt_down sets active_cancel).

        `prompt_override` (issue #149) supplies a ready-made user prompt (e.g. the long-jump
        flavor line) instead of the default event-derived one — the worker then skips both the
        event summary and the #138 place enrichment, since the override is self-contained flavor."""
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
                target=self._proactive_worker, args=(event_name, event, cancel), daemon=True,
                kwargs={"prompt_override": prompt_override})
            self.worker.start()
        return True

    def _proactive_worker(self, event_name: str, event: dict,
                          cancel: threading.Event, *, prompt_override: str | None = None) -> None:
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
            if prompt_override is not None:
                # Pure flavor (#149): the caller supplied the whole prompt; no summary, no #138
                # place enrichment — it asserts no game facts by design.
                prompt = prompt_override
            else:
                summary = self.ed_ctx.summary() if self.ed_ctx is not None else None
                # Place-aware & visit-history enrichment (#138): consult the visit ledger + special-
                # place classifier and, only for a special place or a NOTABLE visit pattern (gated by
                # a dedicated place cooldown), feed grounded structured facts into the prompt.
                # Ordinary arrivals get exactly today's generic callout (facts stays None). Fail-soft.
                facts = self._place_facts(event_name, event)
                prompt = build_prompt(event, summary, facts=facts)
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
            # CALLOUT priority (issue #146): a proactive musing yields to a user reply but outranks
            # (and preempts) an ambient PERSONA cue. No subject key — an unrelated arrival callout
            # queues behind another rather than superseding it.
            self._speak(reply, cancel, tts=tts, text_only=text_only, priority=Priority.CALLOUT)
            self.set_state("Idle")
        except Exception as e:  # noqa: BLE001 — a proactive failure must never crash the app
            self.set_state("Idle", f"proactive error: {e}")

    def _place_facts(self, event_name: str, event: dict) -> dict | None:
        """Grounded place/visit facts for an arrival callout (#138), or None to leave it generic.

        Consults the special-place classifier + the visit ledger (both PURE), then gates the whole
        enrichment on a DEDICATED place cooldown so a busy engineering session doesn't narrate every
        dock. Returns a small structured facts dict ONLY when the place is special or the visit
        pattern is notable AND the cooldown has elapsed. Fail-soft: any error yields None, so a
        ledger/classifier glitch degrades to today's plain callout, never a crash."""
        try:
            if self.ed_ctx is None or self.proactive is None:
                return None
            if event_name not in ("Docked", "FSDJump", "CarrierJump"):
                return None
            from .ed.place_classifier import classify_station, classify_system, place_facts
            snap = self.ed_ctx.snapshot()
            system = event.get("StarSystem") or snap.get("system")
            if event_name == "Docked":
                station = event.get("StationName") or snap.get("station")
                stats = self.ed_ctx.visit_stats_station(system, station)
                place = classify_station(system, station,
                                         at_own_carrier=self.ed_ctx.at_own_carrier())
            else:
                stats = self.ed_ctx.visit_stats_system(system)
                first_visit = bool(stats is not None and stats.first_visit)
                place = classify_system(system, first_visit=first_visit)
            facts = place_facts(place, stats)
            if not facts:
                return None
            now = time.monotonic()
            if not self.proactive.policy.should_place_remark(now):
                return None
            self.proactive.policy.mark_place_remark(now)
            return facts
        except Exception:  # noqa: BLE001 — enrichment must never break a callout
            return None

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
            # CALLOUT priority on the shared "route" subject (issue #146): a fresher route callout
            # SUPERSEDES an older one still being read (e.g. the next-star update mid old-star line)
            # — same-subject preempts — instead of stacking two stale route lines back to back.
            self._speak(text, cancel, priority=Priority.CALLOUT, subject="route")
            self.set_state("Idle")
        except Exception as e:  # noqa: BLE001 — a route callout must never crash the app
            self.set_state("Idle", f"route error: {e}")

    # The HUD is EXPERIMENTAL (issue #123): all three surface toggles are ANDed with
    # [experimental.hud] here — the single place every HUD decision reads — so a flag-off build
    # never shows an overlay, never pumps for it, and never tells the LLM the HUD is "active"
    # (the prompt-context hint below reads these), matching the registration gate in bootstrap.
    def _hud_enabled(self) -> bool:
        return (bool(self.cfg.get("hud", {}).get("enabled", False))
                and experimental(self.cfg, "hud"))

    def _vr_hud_enabled(self) -> bool:
        return (bool(self.cfg.get("hud", {}).get("vr_enabled", False))
                and experimental(self.cfg, "hud"))

    def _web_hud_enabled(self) -> bool:
        return (bool(self.cfg.get("hud", {}).get("web_enabled", False))
                and experimental(self.cfg, "hud"))

    def note_web_ui_started(self) -> None:
        """Called once by web.create_app when the control panel (Flask) comes up, so a web HUD
        (#103) enabled BEFORE the server existed can attach now that /hud is served. Idempotent
        and fail-soft — it only ever brings the web surface up, never blocks startup."""
        self._web_ui_running = True
        if self.hud is not None:
            try:
                self.hud.on_web_ui_ready()
            except Exception as e:  # noqa: BLE001 — a reconcile glitch must not crash startup
                self._log("hud", f"web UI ready reconcile failed: {e}")

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
            if self._hud_enabled() or self._vr_hud_enabled() or self._web_hud_enabled():
                self._start_event_pump()  # idempotent
            self.hud.reconcile()
            # Push the live placement so a Settings/voice change to distance / offset / pitch /
            # curvature / width repositions a SHOWN VR overlay immediately (no re-toggle).
            placement = self._vr_hud_placement()
            self.hud.set_vr_placement(placement)
            # Diagnostic (issue #144): a `hud` line every apply so a "the lateral offset does
            # nothing" report can be pinned to config vs render — confirms the new offset/yaw
            # actually flowed to set_vr_placement.
            self._log("hud", f"placement -> yaw={placement.yaw_deg:.0f} "
                             f"x={placement.offset_x_m:.2f} y={placement.up_m:.2f} "
                             f"dist={placement.forward_m:.2f} pitch={placement.pitch_deg:.0f}")
        except Exception as e:  # noqa: BLE001 — a toggle glitch must not crash the loop
            self._log("hud", f"reconcile failed: {e}")

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

    def _current_ship_pad_size(self) -> str | None:
        """The landing-pad size ("S"/"M"/"L") the Commander's CURRENTLY-FLOWN ship needs, from
        live ED context — or None when no ship has been seen yet / the symbol isn't recognized.
        Backs the nav "match" pad option (#117); the capability applies the Large fallback."""
        if self.ed_ctx is None:
            return None
        from .ed import ship_pad_size
        return ship_pad_size(self.ed_ctx.snapshot().get("ship_symbol"))

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
        # Flush the persona speech arbiter (issue #146): cancel whatever persona line is speaking
        # AND drop every queued line, so no stale ambient/callout plays after the Commander has
        # spoken. This covers the ambient PERSONA cues whose cancel Event is the arbiter's own (not
        # active_cancel); setting active_cancel above already covers an in-flight reply/callout.
        self.persona_arbiter.flush()
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

    def _fail_cue_to_idle(self, extra: str = "") -> None:
        """Return to Idle after a failed / empty / errored turn AND actually play the `failure`
        cue. ORDER IS LOAD-BEARING: set_state('Idle') stops the thinking bed via stop_loop(),
        which clear_bus()'es the shared 'alert' bus — and clear_bus drops EVERY pending source on
        it. So a `failed` cue submitted BEFORE the transition (the old order) is wiped before it
        sounds; the bed and one-shot cues share that bus. Playing it AFTER, onto the now-clean bus,
        is what makes it audible — this is why the shipped failure cue was silent out of the box."""
        self.set_state("Idle", extra)
        self.cues.play("failed")

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
                self._fail_cue_to_idle("(no speech detected)")
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
            self._fail_cue_to_idle(f"reflex error: {e}")

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
        # EXPERIMENTAL (issue #123): hands-free voice activation (continuous VAD listening + the
        # wake word) is gated behind [experimental.voice_activation] (off by default) at THIS
        # seam — the single choke every listener/wake decision reads. Off, the app is push-to-talk
        # only regardless of [listen].mode, so no VAD listener starts and the wake gate is never
        # armed (the continuous path is the only caller that sets wake_gated).
        if not experimental(self.cfg, "voice_activation"):
            return "ptt"
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
        bootstrap.reconcile_persona_voice(self, before)

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
               text_only=None, priority: int = Priority.REPLY, subject: str = "") -> None:
        """Play `text` in the persona voice, SERIALIZED through the one speech arbiter (issue #146)
        so it can never mix with an ambient PERSONA cue on the shared COVAS bus. `tts` is the
        turn-local provider captured at the start of a turn (issue #90) so a mid-turn hot-swap
        can't change the voice underneath a reply; callers outside a turn omit it and get the live
        `self.tts`. `text_only` is the turn-local text-only flag, captured with `tts` (issue #90
        review): a mid-turn swap to a keyless provider flips the LIVE `self.text_only`, so a turn
        that started with a working voice must gate on the flag it CAPTURED, not the live one —
        otherwise its reply is silently dropped. None => use the live `self.text_only`.

        `priority`/`subject` place this line in the arbiter (issue #146): a REPLY (the default)
        outranks a CALLOUT and is never preempted by an ambient cue; a `subject` topic key lets a
        fresher same-subject callout supersede a stale one still being read. This call BLOCKS until
        the arbiter's speaker thread has spoken (or barge-in/preemption cut) the line — exactly as
        the old direct call blocked — and re-raises a real TTS failure so callers keep degrading to
        text/Idle. In text-only mode (no ElevenLabs key) there's nothing to speak, so skip quietly
        WITHOUT touching the arbiter; that loud failure path is for a CONFIGURED-but-broken TTS."""
        if self.text_only if text_only is None else text_only:
            return
        line = self.persona_arbiter.enqueue(
            text, priority=priority, subject=subject, cancel=cancel,
            speak=lambda c: self._speak_now(text, c, tts=tts))
        line.wait()
        line.raise_if_error()  # a real TTS failure propagates, per _speak's diagnosable contract

    def _speak_now(self, text: str, cancel: threading.Event, *, tts=None) -> None:  # noqa: ANN001
        """The actual persona speak, run ON THE ARBITER'S speaker thread (issue #146) — never
        called directly by a producer, always via the arbiter so exactly one persona line sounds
        at a time. On failure it re-raises (after `_speak_persona` logs LOUDLY) so the arbiter can
        surface it to a blocking `_speak` caller.

        When CREW voicing is on ([crew].enabled, issue #69), the reply is first split into
        `[Name]`-prefixed segments: persona lines keep the direct TTS path, crew lines are voiced
        in their own deterministic cast voice on the radio-treated comms bus (a DIFFERENT bus —
        out of scope for the persona arbiter, #146). When it's off (the default) the reply is
        spoken verbatim, exactly as before — the parser isn't invoked."""
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
                self._fail_cue_to_idle("(no speech detected)")
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
            self._fail_cue_to_idle(f"error: {e}")
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
                self._fail_cue_to_idle("(nothing to answer)")
                return
            # Build THIS call's messages WITHOUT mutating stored history. A cancelled, errored,
            # or empty-reply turn must leave NO trace: an orphaned user turn (appended before the
            # call with no assistant reply) poisons the next call — the model answers the stale
            # question and the API 400s on the malformed history. So we commit the user+assistant
            # PAIR together only after a successful reply (below). The per-turn message carries the
            # context-augmented `llm_text`; what we PERSIST is the clean `user_text`, so telemetry/
            # recall never linger across turns.
            messages = self.history + [{"role": "user", "content": llm_text}]

            # Active-ship crew roster (issue #127 §3): stamp the flown ship's id onto cfg so the
            # per-turn build_system() inside the provider resolves THIS ship's crew roster. A single,
            # clearly-commented point — the provider's build_system(cfg) has no ed_ctx, and cfg (not
            # overrides) is what it reads, so this runtime-only key never persists to overrides.json.
            # Fail-soft; no active ship / crew off -> the default roster.
            from . import crew as _crew
            _crew.stamp_active_ship(self.cfg, self.ed_ctx)

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
                self._fail_cue_to_idle("(empty reply)")
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
            # Issue #97: a transient/overloaded provider (exhausted retries, connection drop, 529)
            # earns an in-character, provider-named spoken heads-up instead of a bare error.
            # Issue #108: a NON-transient, user-fixable misconfiguration (bad key/model/endpoint)
            # earns a different spoken heads-up naming the likely fix — that's the one failure mode
            # that's silent-by-default AND entirely the Commander's to fix, so staying quiet is worse
            # than repeating it every failed turn. Any other (unclassified) failure just degrades
            # soft as before — never call the LLM to narrate its own outage. History is untouched
            # here (the commit is after a successful reply), so a failed turn leaves NO orphan. The
            # failure cue plays AFTER the Idle transition (via _fail_cue_to_idle) so stop_loop's
            # alert-bus clear can't drop it.
            if _retry.is_degraded_error(e):
                self._speak_degraded(e, cancel, tts=tts, text_only=text_only)
            elif _retry.is_config_error(e):
                self._speak_misconfig(e, cancel, tts=tts, text_only=text_only)
            self._fail_cue_to_idle(f"error: {e}")

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
                "openai": "The AI service"}.get(name, "The AI service")

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

    def _speak_misconfig(self, err: Exception, cancel: threading.Event, *, tts=None,  # noqa: ANN001
                         text_only=None) -> None:
        """Tell the Commander, plainly, that the LLM is unreachable because of a SETTINGS problem —
        a bad model id, a wrong/missing API key, a bad endpoint (issue #108) — and log the precise
        reason. Sibling of :meth:`_speak_degraded`: canned/verbatim (the LLM is what's down, so it
        never narrates its own failure), fires on EVERY failed turn (no rate-limiting — a config
        error is persistent and each failed turn was a deliberate PTT that got no answer), and
        degrades to a logged line when TTS is unavailable or `[llm].speak_config_errors` is off.
        `tts`/`text_only` are the turn-locals (issue #90 review) so the line stays on the turn's own
        provider + text-only state through a mid-turn hot-swap."""
        name = self._provider_display_name()
        hint = _retry.config_hint(err)
        line = f"I can't reach {name}, Commander — {hint}. Check the AI settings."
        self._log("system", f"provider misconfigured: {_retry.degraded_reason(err)}")
        if not bool((self.cfg.get("llm", {}) or {}).get("speak_config_errors", True)):
            return  # operator opted out of the spoken line; the log line above still recorded it
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
        try:
            self.persona_arbiter.stop()  # join the persona speech-arbiter thread (issue #146)
        except Exception:  # noqa: BLE001 — never let cleanup raise on exit
            pass
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
