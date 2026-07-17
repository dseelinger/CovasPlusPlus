"""Auto-honk — fire the Discovery Scanner on arrival in a new system (N5 + K2, DESIGN §6).

The "honk" is a full-system Discovery Scan: hold the Discovery Scanner's fire button for a
few seconds after jumping in. This capability automates it on the journal's `FSDJump` event.

We do NOT track or switch fire groups. We just fire the CURRENT group's Primary/Secondary
button and react to what happens (K2 detect-and-recover):

  * A short PROBE-press, then read Status.json GuiFocus.
  * If it opened the Detailed Surface Scanner (GuiFocus == SAA) — the current group holds the
    DSS, not the Discovery Scanner — back out (ExplorationSAAExitThirdPerson), warn, and
    DISARM until re-armed (the verbal `rearm_auto_honk` tool, or auto on a real
    FSSDiscoveryScan event).
  * Otherwise complete the full honk. (A weapons group can't fire in supercruise, so the worst
    non-scanner case is a harmless no-op that simply doesn't scan.)

Safety (reuses the keybind layer — DESIGN §6):
  * **Supercruise + analysis-mode only**, and refuses during danger/interdiction or when ED
    status is unavailable (can't prove it's safe).
  * **Hard abort** — the shared `KeyExecutor.release_all()` (wired to `abort_keybinds`) lifts
    the held fire key, and the executor clamps hold duration so a key can't stick.
  * Every honk (and every skip/recover, with its reason) is logged.

Everything is injected (binds, executor, status snapshot, spawner, speak) so the whole
sequence is unit-testable offline with a recording fake executor.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..ed.status import GUI_FOCUS_SAA
from ..keybinds.binds import KeyBinding
from ..keybinds.executor import ExecutorError
from .keybind_capability import SAFE, _GUARD_MESSAGES, combat_state

# ED binding action tokens the honk drives. The model never sees these — deterministic macro.
PRIMARY_FIRE = "PrimaryFire"
SECONDARY_FIRE = "SecondaryFire"
# The bind that backs out of the Detailed Surface Scanner (SAA) probe view — pressed to recover
# when a honk attempt opened the DSS (the current fire group held it, not the Discovery Scanner).
SAA_EXIT = "ExplorationSAAExitThirdPerson"

# The verbal re-arm tool the Commander can invoke after a Surface-Scanner misfire disarms honk.
_REARM_TOOL = "rearm_auto_honk"

# Short test-press (seconds) that lets GuiFocus flip to SAA if the current group holds the DSS,
# before we commit to the full honk hold. Long enough to register the mode, short enough not to
# launch a probe. Internal (not a user setting) — tune here if hardware testing needs it.
_PROBE_SECONDS = 0.4

# The mode change takes a beat to show up in the ~1s-polled Status.json snapshot, so after the
# probe we WATCH GuiFocus for a window longer than one poll cycle before deciding. Without this
# the check reads a stale (pre-honk) snapshot, misses the Surface Scanner, and holds fire in it.
_DETECT_WINDOW = 2.5   # seconds to watch for the Surface-Scanner (SAA) mode after the probe
_POLL_STEP = 0.25      # how often to re-check the snapshot within that window


@dataclass(frozen=True)
class HonkConfig:
    """Immutable snapshot of `[honk]`. Off by default; the capability isn't registered unless
    `enabled`. `trigger` picks which fire button the Discovery Scanner sits on; `hold_seconds`
    is the full honk hold. We don't track fire groups — the detect-and-recover handles a wrong
    (Surface-Scanner) group."""
    enabled: bool = False
    trigger: str = "primary"        # "primary" | "secondary"
    hold_seconds: float = 5.0
    combat_guard: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> "HonkConfig":
        h = cfg.get("honk", {}) or {}
        d = cls()
        trigger = str(h.get("trigger", d.trigger) or "").strip().lower()
        trigger = "secondary" if trigger.startswith("sec") else "primary"
        try:
            hold_seconds = max(0.0, float(h.get("hold_seconds", d.hold_seconds)))
        except (TypeError, ValueError):
            hold_seconds = d.hold_seconds
        return cls(
            enabled=bool(h.get("enabled", False)),
            trigger=trigger,
            hold_seconds=hold_seconds,
            combat_guard=bool(h.get("combat_guard", True)),
        )

    @property
    def fire_action(self) -> str:
        """The ED fire-button action token for the configured trigger."""
        return SECONDARY_FIRE if self.trigger == "secondary" else PRIMARY_FIRE


class HonkCapability:
    """Fires the Discovery Scanner on arrival, behind the keybind safety layer.

    Injected seams (so the default test run is offline and deterministic):
      * `binds`  — {action_token: KeyBinding} parsed from the active .binds file (may be {}).
      * `executor` — a KeyExecutor (or a fake recorder in tests) — SHARED with the keybind
        capability when both are on, so a hard abort lifts the held fire key too.
      * `status_snapshot` — Callable[[], dict|None] returning the live EDContext snapshot (for
        the guards + the GuiFocus check), or None when ED monitoring isn't running.
      * `spawn` — runs the (blocking, ~hold_seconds) honk sequence off the event-pump thread;
        defaults to a daemon thread. Tests inject a synchronous runner.
      * `speak` — speaks a Surface-Scanner-misfire warning (falls back to the log if absent).
    """
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "keybinds"

    def __init__(
        self,
        config: HonkConfig,
        *,
        binds: dict[str, KeyBinding],
        executor: object,
        status_snapshot: Optional[Callable[[], Optional[dict]]] = None,
        spawn: Optional[Callable[[Callable[[], None]], None]] = None,
        speak: Optional[Callable[[str], object]] = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config
        self._binds = binds or {}
        self._executor = executor
        self._status = status_snapshot
        self._spawn = spawn or _default_spawn
        self._speak = speak
        self._sleep = sleep
        self._log = log
        self._lock = threading.Lock()   # non-blocking: skip a second honk while one is holding
        self._disarmed = False          # set after a DSS misfire; cleared by verbal/auto re-arm

    # -- capability interface ---------------------------------------------------------
    # One tool: verbal re-arm after a Surface-Scanner misfire disarms honk. Otherwise ambient.
    def tools(self) -> list[dict]:
        return [{
            "name": _REARM_TOOL,
            "description": ("Re-arm auto-honk after it paused itself because a honk opened the "
                            "Detailed Surface Scanner instead of the Discovery Scanner. Call "
                            "when the Commander confirms the Discovery Scanner is now in their "
                            "current fire group, or asks to re-arm / turn auto-honk back on."),
            "input_schema": {"type": "object", "properties": {}},
        }]

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _REARM_TOOL:
            return f"Unknown tool: {name}"
        was = self._disarmed
        self._disarmed = False
        return "Auto-honk re-armed." if was else "Auto-honk was already armed."

    def on_event(self, event: dict) -> None:
        """Bus hook (event-pump thread). Honk on arrival in a new system; auto-rearm on a real
        discovery scan. Must never raise (a bad event mustn't take the pump down) and must not
        block the pump for the hold duration — the honk sequence runs on the injected spawner."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            name = event.get("event")
            if name == "FSSDiscoveryScan":
                # A discovery scan completed (ours or a manual honk) -> the scanner clearly works
                # in this setup, so lift any disarm left by an earlier Surface-Scanner misfire.
                self._rearm("a discovery scan completed")
                return
            if name == "FSDJump":
                self._spawn(self._do_honk)
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass

    # -- the honk sequence ------------------------------------------------------------
    def _do_honk(self) -> None:
        """Run one honk. Guarded so overlapping arrivals can't stack a second sequence on top
        of a key that's still held. Never raises."""
        if not self._lock.acquire(blocking=False):
            self._logline("skipped: a honk is already in progress")
            return
        try:
            self._honk_sequence()
        except ExecutorError as e:
            self._logline(f"skipped: key injection failed — {e}")
        except Exception as e:  # noqa: BLE001 — a honk must never crash the app
            self._logline(f"error: {e}")
        finally:
            self._lock.release()

    def _honk_sequence(self) -> None:
        # 0. Disarmed after a prior Surface-Scanner misfire — stay quiet until re-armed.
        if self._disarmed:
            self._logline("skipped: disarmed after a Surface-Scanner misfire — say 're-arm auto "
                          "honk', or complete a discovery scan, once the Discovery Scanner is in "
                          "your fire group.")
            return

        # 1. Combat/interdiction guard (and unknown status when the guard is on).
        guard = self._guard()
        if guard is not None:
            self._logline(f"blocked: {guard}")
            return

        snap = self._status() if self._status is not None else None
        if not snap:
            self._logline("skipped: ED status unavailable — can't confirm it's safe to honk.")
            return

        # 2. SUPERCRUISE only.
        if not snap.get("supercruise"):
            self._logline("skipped: not in supercruise.")
            return

        # 3. ANALYSIS mode only — the scanners don't work in combat mode.
        if not snap.get("analysis_mode"):
            self._logline("skipped: in combat mode — switch to analysis mode to honk.")
            return

        # 4. The fire button must be bound to a key we can press.
        fire_binding = self._binds.get(self._cfg.fire_action)
        if fire_binding is None or not fire_binding.usable:
            self._logline(f"skipped: {self._cfg.fire_action} has no keyboard binding — "
                          f"bind the Discovery Scanner's fire button to a key in-game.")
            return

        # 5. Probe-and-recover: a short test-press, then WATCH GuiFocus for a window (the
        #    snapshot polls ~1s, so a single immediate read would be stale). If it opened the
        #    Surface Scanner, back out + warn + disarm; else complete the honk on the current group.
        self._executor.hold(fire_binding, _PROBE_SECONDS)
        if self._opened_surface_scanner():
            self._recover_from_dss()
            return
        self._executor.hold(fire_binding, self._cfg.hold_seconds)
        self._logline("honked — current fire group.")

    def _opened_surface_scanner(self) -> bool:
        """Watch GuiFocus for up to _DETECT_WINDOW for the Surface-Scanner (SAA) mode, re-reading
        the snapshot every _POLL_STEP so the ~1s poll lag doesn't cause a miss. True as soon as
        SAA is seen (the mode persists until we exit), else False after the window."""
        waited = 0.0
        while True:
            snap = self._status() if self._status is not None else None
            if snap and snap.get("gui_focus") == GUI_FOCUS_SAA:
                return True
            if waited >= _DETECT_WINDOW:
                return False
            self._sleep(_POLL_STEP)
            waited += _POLL_STEP

    def _recover_from_dss(self) -> None:
        """The probe opened the Detailed Surface Scanner — the current group holds the DSS, not
        the Discovery Scanner. Back out, warn, and disarm until re-armed."""
        exit_binding = self._binds.get(SAA_EXIT)
        if exit_binding is not None and exit_binding.usable:
            self._executor.press(exit_binding)
        else:
            self._logline(f"couldn't auto-exit the Surface Scanner ({SAA_EXIT} not bound) — "
                          f"back out manually.")
        self._disarmed = True
        self._logline("disarmed: a honk opened the Surface Scanner (wrong fire group).")
        self._say("Heads up — that fired your Surface Scanner, not the Discovery Scanner. "
                  "Auto-honk is paused until you confirm the Discovery Scanner is in your "
                  "current fire group.")

    def _say(self, text: str) -> None:
        """Speak a warning through the injected seam if wired; else fall back to the log."""
        if self._speak is not None:
            try:
                self._speak(text)
                return
            except Exception:  # noqa: BLE001 — a TTS hiccup must not crash the honk thread
                pass
        self._logline(f"(warning): {text}")

    def _rearm(self, reason: str) -> None:
        if self._disarmed:
            self._disarmed = False
            self._logline(f"re-armed ({reason}).")

    # -- guards / state ---------------------------------------------------------------
    def _guard(self) -> Optional[str]:
        """Combat/interdiction guard, mirroring the keybind capability. Returns a refusal
        message when it's not safe to act, or None when clear (or when `combat_guard` is off)."""
        if not self._cfg.combat_guard:
            return None
        snap = self._status() if self._status is not None else None
        state = combat_state(snap)
        return None if state == SAFE else _GUARD_MESSAGES[state]

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _default_spawn(fn: Callable[[], None]) -> None:
    """Run the honk sequence on a daemon thread so the ~hold_seconds hold never blocks the
    event pump (which would delay every other bus consumer)."""
    threading.Thread(target=fn, name="auto-honk", daemon=True).start()
