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
        self.cues = CuePlayer(self.cfg)
        self.recorder = Recorder(self.cfg)
        if self.mock:
            print("Dev mock ON — LLM/TTS/STT are fakes; zero API calls, zero cost.", flush=True)
        elif stt is None:
            print("Loading Whisper model (first run may download it)...", flush=True)
        self.stt = stt or make_stt(self.cfg)
        self.tts = tts or make_tts(self.cfg)
        self.llm = llm or make_llm(self.cfg)

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

        # Elite Dangerous monitoring (DESIGN §5). Opt-in ([elite].enabled, off by
        # default). When on, two daemon watchers tail ED's journal + Status.json,
        # publishing events on the bus and updating a shared context the ED-context
        # capability references. Watchers publish only; they never drive the loop.
        self.ed_ctx = None
        self._ed_watchers: list = []
        if self.cfg.get("elite", {}).get("enabled"):
            self._start_ed_monitoring()

        self.history: list[dict] = []
        self.active_cancel: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.ptt_held = False
        self._ptt_t0 = 0.0  # key-down time, for tap-vs-hold detection
        self.state = "Idle"
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
        # Find-closest-module: resolve a module by voice (offline taxonomy), confirm, then
        # find the nearest station selling it via Spansh + copy the system to the clipboard.
        # Opt-in ([nav].enabled, off by default).
        self.nav = None
        self._proactive_lock = threading.Lock()
        self._pump: threading.Thread | None = None
        self._pump_q: queue.Queue | None = None
        self._pump_stop = threading.Event()

        self._logf = self._open_log()
        self._log("system", _cost_summary(self.cfg, self.mock))
        if self.cfg.get("proactive", {}).get("enabled"):
            self._start_proactive()
        if self.cfg.get("keybinds", {}).get("enabled"):
            self._start_keybinds()
        if self.cfg.get("nav", {}).get("enabled"):
            self._start_nav()
        if self.cfg.get("star_systems", {}).get("enabled"):
            self._start_system_search()
        if self.cfg.get("search", {}).get("enabled"):
            self._start_searches()

    # ---- Elite Dangerous monitoring (DESIGN §5) ---------------------------
    def _start_ed_monitoring(self) -> None:
        """Build the shared context + ED-context capability and start the journal/status
        watchers. Fail soft: a missing directory or import problem must not stop the app
        from starting — ED monitoring just stays dark until the next run."""
        try:
            from .ed import (EDContext, JournalWatcher, StatusWatcher,
                             resolve_journal_dir, status_path)
            from .capabilities.ed_context_capability import EDContextCapability

            el = self.cfg.get("elite", {})
            jdir = resolve_journal_dir(self.cfg)
            self.ed_ctx = EDContext(recent_maxlen=int(el.get("recent_events_kept", 25)))
            self.registry.register(EDContextCapability(self.ed_ctx))

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
        and replay=False so stale startup events aren't delivered to a handler."""
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

    # ---- Keybind automation (DESIGN §6) -----------------------------------
    def _start_keybinds(self) -> None:
        """Build the keybind capability: resolve + parse the active ED bindings, build the
        scancode executor, and register the capability behind its safety layer. Fail soft —
        a missing bindings file or a non-Windows host just leaves ship controls off; it must
        never block startup. The combat guard reads the live ED context snapshot (so keybinds
        needs [elite].enabled to positively confirm it's safe to act)."""
        try:
            from .keybinds import BindsError, load_binds
            from .keybinds.executor import KeyExecutor
            from .capabilities.keybind_capability import KeybindConfig, KeybindCapability

            kcfg = KeybindConfig.from_cfg(self.cfg)
            try:
                binds = load_binds(self.cfg)
            except BindsError as e:
                # Couldn't locate/read the .binds file — register anyway with no bindings so
                # the capability gives a clear "bind it in-game / set binds_file" message
                # rather than vanishing silently.
                binds = {}
                self.bus.publish({"type": "log", "who": "system",
                                  "text": f"Keybinds: {e}"})

            executor = KeyExecutor()   # raises ExecutorError off-Windows -> caught below
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

    # ---- Find-closest-module ----------------------------------------------
    def _start_nav(self) -> None:
        """Build + register the find-closest-module capability. Fail soft: a startup problem
        just leaves the feature off. The Spansh HTTP client is built here (composition root)
        so tests never need it; current-system is read live from ED context with a journal
        fallback."""
        try:
            from .nav import RequestsHttp
            from .capabilities.find_closest_capability import NavConfig, FindClosestCapability

            ncfg = NavConfig.from_cfg(self.cfg)
            self.nav = FindClosestCapability(
                ncfg, http=RequestsHttp(),
                get_current_system=self._current_system,
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
        except Exception as e:  # noqa: BLE001 — optional; never block startup
            self.nav = None
            self.bus.publish({"type": "log", "who": "system",
                              "text": f"Find-closest-module failed to start: {e}"})

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
        self._logf.write(f"{ts}  {who}: {text}\n")
        self._logf.flush()
        self.bus.publish({"type": "log", "who": who, "text": text})

    def set_state(self, state: str, extra: str = "") -> None:
        self.state = state
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
        self.bus.publish({"type": "settings", "settings": self.public_settings()})

        # reload Whisper in the background if its model/device changed
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
            "whisper": c["whisper"]["model"],
        }

    # ---- local voice commands --------------------------------------------
    def _speak(self, text: str, cancel: threading.Event) -> None:
        """Play `text` through the injected TTS provider (real, mock, or fake)."""
        self.tts.speak(text, cancel)

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
        except Exception as e:  # noqa: BLE001
            self.bus.publish({"type": "log", "who": "system", "text": f"TTS error: {e}"})

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
            self._log("router", f"{route.model} max_tokens={route.max_tokens} — {route.reason}")
            self.bus.publish({"type": "router", "model": route.model,
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
