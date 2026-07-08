"""COVAS++ core loop (headless).

Hold PTT -> listening cue + capture. Release -> processing cue, then in a worker:
transcribe (Whisper) -> Claude (streaming) -> done cue -> speak (ElevenLabs).
Cancel key aborts the in-flight Claude call and any TTS playback. Pressing PTT again
also interrupts current speech.
"""
from __future__ import annotations
import datetime as _dt
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
        if self.cfg.get("checklist", {}).get("file"):
            self.registry.register(ChecklistCapability(self.checklist))

        self.history: list[dict] = []
        self.active_cancel: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.ptt_held = False
        self._ptt_t0 = 0.0  # key-down time, for tap-vs-hold detection
        self.state = "Idle"
        self._quit = threading.Event()
        self._logf = self._open_log()
        self._log("system", _cost_summary(self.cfg, self.mock))

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
        self._interrupt()            # interrupt any current thinking/speaking
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

            # Local voice commands (checklist, etc.) — handled without calling Claude
            if self._handle_command(text, cancel):
                self.set_state("Idle")
                return

            self.history.append({"role": "user", "content": text})
            self._trim_history()

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
                self.history, cancel, on_event,
                tool_handler=self.registry.run_tool, tools=self.registry.tools())
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
        keyboard.add_hotkey("ctrl+alt+q", lambda: self._quit.set())
        self.set_state("Idle")

    def run(self) -> None:
        try:
            self.start()
        except ValueError as e:
            print(f"Bad key name in config [keys]: {e}")
            return
        print(_banner(self.cfg))
        try:
            self._quit.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self._logf.close()
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
    return (
        "cost settings — "
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
    return (
        "\n================ COVAS++ (Phase 2) ================\n"
        f"  Model      : {cfg['anthropic']['model']}\n"
        f"  Voice      : {cfg['elevenlabs']['voice_name']}\n"
        f"  Whisper    : {cfg['whisper']['model']}\n"
        f"  Personality: {p}\n"
        f"  Cache TTL  : {cfg['anthropic'].get('cache_ttl', '1h')}\n"
        f"  Dev mock   : {'ON' if mock else 'OFF'}\n"
        f"  TALK        : hold  [{k['push_to_talk']}]\n"
        f"  CANCEL      : tap   [{k['push_to_talk']}] briefly\n"
        f"  QUIT        : Ctrl+Alt+Q (or close this window)\n"
        "==================================================\n"
        "Hold the PTT key and speak, Commander.\n"
    )
