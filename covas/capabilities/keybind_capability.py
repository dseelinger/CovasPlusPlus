"""Keybind capability — the companion presses ONE ship control, behind a hard safety layer.

The one-action prototype from DESIGN §6: prove a single reliable keystroke (toggle landing
gear) end-to-end before generalizing to macros. Deliberately narrow and paranoid.

Split of concerns (DESIGN §6, "LLM as intent layer, not button-masher"):
  * The LLM only ever SELECTS a named macro by calling a tool. It never synthesizes a key
    sequence — the tool schemas expose named actions, not keys.
  * A deterministic executor (`covas/keybinds/executor.py`) runs the actual scancodes.

The safety layer (non-negotiable — everything opt-in, off by default):
  1. **Allowlist** — only macros named in `[keybinds].allowlist` are advertised or run.
  2. **Explicit confirmation** — arming an action does NOT fire it; the Commander must
     confirm on a SEPARATE spoken command (`confirm_keybind`). We turn-gate this so the
     model can't arm-and-confirm inside one turn: confirmation is rejected unless a new
     Commander utterance arrived after the arm.
  3. **Combat/interdiction guard** — refuses to touch controls when ED Status reports
     danger/interdiction; also refuses when ED status is unavailable (can't prove it's safe).
  4. **Hard global abort** — `abort_keybinds` clears any pending action and releases every
     held key immediately.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..ed.modes import MODE_FIGHTER, MODE_MAINSHIP, MODE_ON_FOOT, MODE_SRV
from ..keybinds import actions as _actions  # noqa: F401 — import populates the macro registry
from ..keybinds.abort import AbortController
from ..keybinds.binds import KeyBinding
from ..keybinds.executor import ExecutorError
from ..keybinds.registry import Macro, registered_macros
from ..keybinds.sequence import run_sequence
from .base import HelpMeta

# ---- macros -------------------------------------------------------------------------------

# `Macro` and the action definitions now live in the keybinds registry (issue #29): action
# batches register themselves from `keybinds/actions/*.py`, so adding actions is a new module,
# not an edit here. DEFAULT_MACROS is the aggregated registry, snapshotted at import (after
# `keybinds.actions` has registered every shipped macro).
DEFAULT_MACROS: dict[str, Macro] = registered_macros()

# Human-readable label per game mode, for mode-gating refusal messages.
_MODE_LABEL: dict[str, str] = {
    MODE_MAINSHIP: "in your ship",
    MODE_FIGHTER: "in a fighter",
    MODE_SRV: "in the SRV",
    MODE_ON_FOOT: "on foot",
}


# ---- config -------------------------------------------------------------------------------


@dataclass(frozen=True)
class KeybindConfig:
    """Immutable snapshot of `[keybinds]`. Off by default; the capability isn't even
    registered unless `enabled`."""
    enabled: bool = False
    require_confirmation: bool = True
    combat_guard: bool = True
    mode_guard: bool = True                  # gate actions to the current game mode (#29)
    confirm_window: float = 60.0            # seconds an armed action stays confirmable
    allowlist: tuple[str, ...] = ("landing_gear",)
    # Foreground ED right before a deliberate macro fires so the keypress can't misfire into a
    # window that stole focus (#105). Default ON: if the Commander asks for a ship control, they
    # want the key to reach ED. The `focus_game` tool ships regardless of this toggle.
    focus_before_inject: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> KeybindConfig:
        k = cfg.get("keybinds", {}) or {}
        d = cls()
        allow = k.get("allowlist")
        if isinstance(allow, (list, tuple)):
            allow = tuple(str(a) for a in allow)
        else:
            allow = d.allowlist
        return cls(
            enabled=bool(k.get("enabled", False)),
            require_confirmation=bool(k.get("require_confirmation", True)),
            combat_guard=bool(k.get("combat_guard", True)),
            mode_guard=bool(k.get("mode_guard", True)),
            confirm_window=float(k.get("confirm_window", d.confirm_window)),
            allowlist=allow,
            focus_before_inject=bool(k.get("focus_before_inject", d.focus_before_inject)),
        )


# ---- combat guard (pure) ------------------------------------------------------------------

# Return values of the combat guard. Only SAFE permits an action.
SAFE, COMBAT, INTERDICTION, UNKNOWN = "safe", "combat", "interdiction", "unknown"


def combat_state(snap: dict | None) -> str:
    """Classify danger from an EDContext snapshot. None (no telemetry) -> UNKNOWN, which the
    guard treats as unsafe: we won't press controls unless we can positively confirm it's
    clear. Interdiction outranks generic danger for a clearer message."""
    if snap is None:
        return UNKNOWN
    if snap.get("being_interdicted"):
        return INTERDICTION
    if snap.get("in_danger"):
        return COMBAT
    return SAFE


_GUARD_MESSAGES = {
    INTERDICTION: ("Refusing — you're being interdicted. I won't touch ship controls "
                   "mid-interdiction."),
    COMBAT: ("Refusing — you appear to be in danger/combat. Ship controls stay locked "
             "until it's clear."),
    UNKNOWN: ("Can't confirm you're clear of combat — Elite Dangerous status isn't "
              "available, so I'm holding off for safety. Turn on ED monitoring "
              "([elite].enabled) to use ship controls."),
}


# ---- tools --------------------------------------------------------------------------------

_CONFIRM_TOOL = {
    "name": "confirm_keybind",
    "description": (
        "Confirm and EXECUTE the ship action you previously armed (e.g. landing gear). Only "
        "call this after the Commander has explicitly confirmed on a NEW, separate command "
        "(they said 'confirm', 'do it', 'yes', 'go ahead'). NEVER call it in the same turn "
        "you armed the action — that isn't a real confirmation and will be refused."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_ABORT_TOOL = {
    "name": "abort_keybinds",
    "description": (
        "Hard abort for ship controls: cancel any armed action and immediately release every "
        "held key. Call the moment the Commander says stop/cancel/abort/belay, or if anything "
        "about a ship action seems wrong."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

# Bringing a window to the front is always safe — no allowlist/mode/combat gate — so this tool is
# advertised whenever keybinds are enabled (#105). It fires immediately (no arm/confirm): the
# Commander explicitly asked to focus the game.
_FOCUS_TOOL = {
    "name": "focus_game",
    "description": (
        "Bring the Elite Dangerous window to the foreground (make it the active window). Call this "
        "when the Commander asks to focus / switch to / bring up the game (e.g. 'focus Elite', "
        "'set focus on the game', 'bring Elite to the front'). Safe to run any time — it only "
        "changes which window is in front, it doesn't touch ship controls. If the game isn't "
        "running, say so."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def _arm_tool(macro: Macro) -> dict:
    return {
        "name": macro.tool,
        "description": (
            f"Arm the ship action: {macro.arm_phrase}. This does NOT fire immediately — for "
            "safety it must be confirmed by the Commander on a separate command. Call this "
            "when the Commander asks for it, tell them what you're about to do, then wait for "
            "them to confirm before calling confirm_keybind. Deterministic named action; you "
            "never specify keys."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }


class KeybindCapability:
    """Advertises the allowlisted macros + confirm/abort, and runs them behind the guards.

    Wiring inputs (all injected so the whole thing is unit-testable offline):
      * `binds`  — {action_token: KeyBinding} parsed from the active .binds file (may be {}).
      * `executor` — a KeyExecutor (or a fake recorder in tests).
      * `status_snapshot` — Callable[[], dict|None] returning the live EDContext snapshot for
        the combat guard, or None when ED monitoring isn't running.
    """
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "keybinds"

    def __init__(
        self,
        *,
        binds: dict[str, KeyBinding],
        executor: object,
        config: KeybindConfig,
        macros: dict[str, Macro] | None = None,
        status_snapshot: Callable[[], dict | None] | None = None,
        focuser: object | None = None,
        abort_controller: AbortController | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._binds = binds or {}
        self._executor = executor
        # Window focuser (#105): powers the explicit `focus_game` tool and the auto-focus pre-step
        # before a macro fires. None off-Windows (or when focus couldn't be built) — every use is
        # guarded, so the capability degrades to the old ambient-focus behaviour, never crashes.
        self._focuser = focuser
        self._cfg = config
        self._macros = macros or dict(DEFAULT_MACROS)
        self._status = status_snapshot
        self._clock = clock
        self._sleep = sleep                    # injected so sequence waits are hermetic in tests
        self._log = log
        self._lock = threading.Lock()
        self._pending: dict | None = None     # {name, turn, at}
        self._turn = 0                         # Commander-utterance counter (confirm gate)
        # Raised by the hard abort to stop a running sequence between steps. The executor's
        # release_all() is the key-level guarantee; this is the loop-level one so an abort ends the
        # sequence rather than firing its remaining steps. INJECTED (defaulting to a fresh one) so
        # it can be SHARED with the custom-macro capability (#50) — then one hard abort stops a
        # running sequence from either. It gives EACH run its own abort token (#154) so a
        # concurrently-starting macro can't wipe an abort meant for this run. (Named `_aborter`,
        # not `_abort`, to avoid shadowing the `_abort()` tool handler below.)
        self._aborter = abort_controller or AbortController()

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        """Arm tools for allowlisted+known macros valid in the CURRENT game mode, plus confirm
        + abort. Allowlist and mode are both enforced again at run time. When the mode is
        unknown (no ED telemetry), we can't gate, so all allowlisted macros are advertised —
        no worse than before mode-gating, since the combat guard already refuses at run time
        when telemetry is unavailable."""
        out = [_arm_tool(m) for m in self._advertised_macros()]
        out.append(_CONFIRM_TOOL)
        out.append(_ABORT_TOOL)
        # `focus_game` is NOT allowlist/mode/combat-gated — foregrounding a window is always safe —
        # so advertise it whenever keybinds are enabled, even with an empty macro allowlist. Only
        # offered when a focuser exists (absent off-Windows), so the tool never dead-ends (#105).
        if self._focuser is not None:
            out.append(_FOCUS_TOOL)
        return out

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="ship controls",
            group="your ship",
            one_liner=("I can press the ship, SRV, and on-foot controls you've allowlisted — "
                       "landing gear, throttle, ship-systems toggles, panels, suit tools, buggy "
                       "controls — and run multi-step sequences (like a pad launch) that check "
                       "your game status between steps instead of firing blind. Always mode-aware "
                       "and behind a combat safety check; disruptive actions need a separate "
                       "spoken confirmation — and when you confirm I read back the exact armed "
                       "action so you hear what's actually about to fire — and 'abort' stops "
                       "everything and releases held keys. "
                       "I can also bring the Elite window to the front on command ('focus Elite'), "
                       "and I pull it forward before pressing a control so the key can't land in "
                       "the wrong window."),
            example="toggle my landing gear",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "abort_keybinds":
                return self._abort()
            if name == "focus_game":
                return self._focus_game()
            if name == "confirm_keybind":
                return self._confirm()
            for m in self._allowed_macros():
                if m.tool == name:
                    return self._arm(m)
            return f"Unknown or disallowed ship action: {name}"
        except Exception as e:  # noqa: BLE001 — the loop must survive any tool error
            self._logline(f"error in {name}: {e}")
            return f"Ship-control error: {e}"

    def new_turn(self) -> None:
        """Called by the app once per Commander utterance. Advances the turn counter so a
        confirmation is only accepted when it arrives on a genuinely new command (see
        `_confirm`) — the model can't arm and confirm within a single turn."""
        with self._lock:
            self._turn += 1

    # -- arm / confirm / abort --------------------------------------------------------
    def _arm(self, macro: Macro) -> str:
        problem = self._binding_problem(macro)
        if problem is not None:
            self._logline(f"{macro.name} unusable: {problem}")
            return problem

        guard = self._guard()
        if guard is not None:
            self._logline(f"{macro.name} blocked by guard: {guard}")
            return guard

        mguard = self._mode_guard(macro)
        if mguard is not None:
            self._logline(f"{macro.name} blocked by mode guard: {mguard}")
            return mguard

        if not (self._cfg.require_confirmation and macro.confirm_required):
            # Confirmation not required (globally off, or this macro is benign/read-only) —
            # still gated by allowlist + combat + mode guards above.
            return self._execute(macro)

        with self._lock:
            self._pending = {"name": macro.name, "turn": self._turn, "at": self._clock()}
        self._logline(f"armed {macro.name}, awaiting confirmation")
        return (f"Ready to {macro.arm_phrase}. This is armed but NOT done yet — ask the "
                f"Commander to confirm (say 'confirm' or 'do it') on a separate command, "
                f"then call confirm_keybind. Say 'abort' to cancel.")

    def _confirm(self) -> str:
        with self._lock:
            p = self._pending
            if not p:
                return "Nothing to confirm — no ship action is armed."
            # Turn gate: a real confirmation is a NEW utterance after the arm.
            if self._turn <= p["turn"]:
                return ("That isn't a separate confirmation yet — the Commander must confirm "
                        "on a new command. Tell them what's armed and wait for them to say it.")
            if self._clock() - p["at"] > self._cfg.confirm_window:
                self._pending = None
                return "That action expired for safety — ask for it again if you still want it."
            macro = self._macros.get(p["name"])
            self._pending = None
        if macro is None:
            return "The armed action is no longer available."

        # Re-check the guards at execution time: danger may have started, or the Commander may
        # have changed mode (e.g. disembarked), since arming.
        guard = self._guard()
        if guard is not None:
            self._logline(f"{macro.name} blocked at confirm: {guard}")
            return guard
        mguard = self._mode_guard(macro)
        if mguard is not None:
            self._logline(f"{macro.name} blocked at confirm by mode guard: {mguard}")
            return mguard
        problem = self._binding_problem(macro)
        if problem is not None:
            return problem
        # Re-state the ACTUAL armed action from the deterministic pending payload — never the
        # model's earlier narration (issue #190, DESIGN §6 confused-deputy note). A separate turn
        # only proves a new utterance occurred; it does NOT prove the Commander consented to THIS
        # action. Leading the confirm output with the armed macro's own arm_phrase means a
        # bait-and-switch — arming B while the model narrated A — is audible the moment it fires.
        return f"Confirming — {macro.arm_phrase}. {self._execute(macro)}"

    def _abort(self) -> str:
        with self._lock:
            self._pending = None
        # Stop any in-flight sequence between steps (the runner polls this), then release keys.
        # abort() marks every currently-running sequence/macro (shared controller) — it does NOT
        # clear a flag a concurrent run could re-clear, so a fresh run can't wipe this signal (#154).
        self._aborter.abort()
        try:
            release = getattr(self._executor, "release_all", None)
            if release is not None:
                release()
        except Exception as e:  # noqa: BLE001 — an abort must always complete
            self._logline(f"release_all error during abort: {e}")
        self._logline("aborted — cleared pending, released keys")
        return "Aborted — cleared any armed action and released all keys."

    # -- focus (#105) -----------------------------------------------------------------
    def _focus_game(self) -> str:
        """Explicit `focus_game` tool: bring ED to the front on command. Not allowlist/mode/combat
        gated — foregrounding is always safe. Fail-soft Commander-facing returns; never raises
        (run_tool wraps it too, but keep the message clean)."""
        if self._focuser is None:
            return "I can't focus the Elite window on this system."
        try:
            if self._focuser.find_ed_window() is None:
                return "I can't find the Elite window — is the game running?"
            if self._focuser.ensure_foreground():
                self._logline("focused ED window")
                return "Brought Elite Dangerous to the front."
            return ("I found Elite but couldn't bring it to the front — try alt-tabbing to it "
                    "once, then ask again.")
        except Exception as e:  # noqa: BLE001 — focus is best-effort; never crash the loop
            self._logline(f"focus_game error: {e}")
            return "I couldn't focus the Elite window just now."

    def _maybe_focus(self) -> None:
        """Auto-focus pre-step (#105): pull ED to the front right before a deliberate macro fires,
        so the keypress can't misfire into a window that stole focus. Gated on
        `focus_before_inject` and a fast no-op when ED is already frontmost (the hot path — no
        enumeration). Best-effort: a focus failure is logged and we still attempt the press, which
        preserves the old ambient-focus behaviour rather than refusing the command. Wired ONLY
        here and in the comms send path — never a global pre-hook, never on combat reflexes."""
        if self._focuser is None or not self._cfg.focus_before_inject:
            return
        try:
            self._focuser.ensure_foreground()
        except Exception as e:  # noqa: BLE001 — never let a focus fault block the macro
            self._logline(f"auto-focus before macro failed: {e}")

    # -- execution + guard ------------------------------------------------------------
    def _execute(self, macro: Macro) -> str:
        """Run the macro — a status-checked SEQUENCE (issue #33) or a single press/hold. The
        allowlist/combat/mode guards and (for consequential macros) confirmation have already
        passed by the time we get here."""
        self._maybe_focus()   # pull ED forward first so the key lands in the game (#105)
        if macro.steps:
            return self._execute_sequence(macro)
        binding = self._binds.get(macro.action)
        try:
            if macro.kind == "hold":
                self._executor.hold(binding, macro.hold_seconds)
            else:
                self._executor.press(binding)
        except ExecutorError as e:
            self._logline(f"{macro.name} injection failed: {e}")
            return f"Couldn't send that key: {e}"
        except Exception as e:  # noqa: BLE001 — never crash the loop on an injection fault
            self._logline(f"{macro.name} injection error: {e}")
            return f"Ship-control injection failed: {e}"
        self._logline(f"executed {macro.name} -> {binding.key}")
        return f"{macro.done_phrase} (sent {binding.key})."

    def _execute_sequence(self, macro: Macro) -> str:
        """Run a sequenced macro through the deterministic runner, reading Status.json between
        steps to gate/verify. Each run takes its OWN abort token (#154): a fresh token starts
        un-aborted (so a stale abort can't kill this run) WITHOUT clearing any shared flag, so a
        concurrently-starting macro can't erase an abort meant for this run. The runner polls the
        token (and calls release_all on abort/failure); the token is retired in `finally`."""
        token = self._aborter.begin()
        try:
            outcome = run_sequence(
                macro.steps,
                executor=self._executor,
                binds=self._binds,
                status=self._status,
                sleep=self._sleep,
                clock=self._clock,
                abort=lambda: self._aborter.is_aborted(token),
            )
        finally:
            self._aborter.end(token)
        if outcome.status == "done":
            self._logline(f"executed sequence {macro.name}")
            return macro.done_phrase
        if outcome.status == "aborted":
            self._logline(f"sequence {macro.name} aborted mid-run")
            return outcome.message
        self._logline(f"sequence {macro.name} failed: {outcome.message}")
        return f"Couldn't complete {macro.arm_phrase} — {outcome.message}"

    def _binding_problem(self, macro: Macro) -> str | None:
        """A Commander-facing reason the macro can't run because a key it needs isn't bound to a
        keyboard key, or None when every key it needs is usable. For a sequence, checks every
        press/hold/release step's token; for a single-key macro, checks `macro.action`."""
        if macro.steps:
            for step in macro.steps:
                if not step.action:      # wait/status steps press nothing
                    continue
                b = self._binds.get(step.action)
                if b is None:
                    return (f"'{step.action}' isn't in your Elite Dangerous bindings — bind it "
                            f"to a key in-game so I can run the {macro.name} sequence.")
                if not b.usable:
                    return b.unusable_reason or f"'{step.action}' has no keyboard binding."
            return None
        b = self._binds.get(macro.action)
        if b is None:
            return (f"'{macro.action}' isn't in your Elite Dangerous bindings — bind it to a key "
                    f"in-game so I can press it.")
        if not b.usable:
            return b.unusable_reason
        return None

    def _guard(self) -> str | None:
        """The combat/interdiction guard. Returns a refusal message when it's not safe to
        act, or None when clear. Skipped entirely if `combat_guard` is off in config."""
        if not self._cfg.combat_guard:
            return None
        snap = self._status() if self._status is not None else None
        state = combat_state(snap)
        return None if state == SAFE else _GUARD_MESSAGES[state]

    def _allowed_macros(self) -> list[Macro]:
        """Macros that are both allowlisted and known — the run-time lookup set (mode-
        independent, so startup readiness reporting lists every wired macro)."""
        return [self._macros[n] for n in self._cfg.allowlist if n in self._macros]

    def _advertised_macros(self) -> list[Macro]:
        """The allowlisted macros valid in the CURRENT game mode — what gets advertised to the
        model. When mode-gating is off, or the mode is unknown (no telemetry), or a macro is
        mode-agnostic (empty `modes`), it isn't filtered out here."""
        if not self._cfg.mode_guard:
            return self._allowed_macros()
        mode = self._current_mode()
        if mode is None:
            return self._allowed_macros()
        return [m for m in self._allowed_macros() if not m.modes or mode in m.modes]

    def _current_mode(self) -> str | None:
        """The Commander's current game mode from the ED status snapshot, or None (unknown)
        when ED monitoring is off or the mode can't be determined."""
        snap = self._status() if self._status is not None else None
        return snap.get("game_mode") if isinstance(snap, dict) else None

    def _mode_guard(self, macro: Macro) -> str | None:
        """Returns a refusal message when `macro` isn't valid in the current mode, else None.
        Skipped when mode-gating is off, the macro is mode-agnostic, or the mode is unknown
        (can't prove a mismatch — the combat guard already covers the no-telemetry case)."""
        if not self._cfg.mode_guard or not macro.modes:
            return None
        mode = self._current_mode()
        if mode is None or mode in macro.modes:
            return None
        where = _MODE_LABEL.get(mode, "in your current mode")
        allowed = ", ".join(_MODE_LABEL.get(m, m) for m in sorted(macro.modes))
        return (f"Can't {macro.arm_phrase} while you're {where} — that action only works "
                f"{allowed}.")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
