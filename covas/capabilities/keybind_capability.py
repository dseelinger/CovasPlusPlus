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
from dataclasses import dataclass
from typing import Callable

from ..keybinds.binds import KeyBinding
from ..keybinds.executor import ExecutorError

# ---- macros -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Macro:
    """A named, deterministic ship action the LLM may select. `action` is the ED binding
    token the executor presses; `kind` is press (a tap) or hold (press for `hold_seconds`)."""
    name: str            # allowlist key + identity (e.g. "landing_gear")
    tool: str            # the tool name advertised to the LLM (e.g. "toggle_landing_gear")
    action: str          # ED action token in the .binds file (e.g. "LandingGearToggle")
    arm_phrase: str      # what we're about to do, for the confirmation prompt
    done_phrase: str     # spoken result once executed
    kind: str = "press"  # "press" | "hold"
    hold_seconds: float = 0.0


# The prototype exposes exactly ONE macro. Generalizing = adding entries here (each still
# gated by the allowlist), not touching the executor or the loop.
DEFAULT_MACROS: dict[str, Macro] = {
    "landing_gear": Macro(
        name="landing_gear",
        tool="toggle_landing_gear",
        action="LandingGearToggle",
        arm_phrase="toggle the landing gear (deploy if up, retract if down)",
        done_phrase="Landing gear toggled",
    ),
}


# ---- config -------------------------------------------------------------------------------


@dataclass(frozen=True)
class KeybindConfig:
    """Immutable snapshot of `[keybinds]`. Off by default; the capability isn't even
    registered unless `enabled`."""
    enabled: bool = False
    require_confirmation: bool = True
    combat_guard: bool = True
    confirm_window: float = 60.0            # seconds an armed action stays confirmable
    allowlist: tuple[str, ...] = ("landing_gear",)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "KeybindConfig":
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
            confirm_window=float(k.get("confirm_window", d.confirm_window)),
            allowlist=allow,
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

    def __init__(
        self,
        *,
        binds: dict[str, KeyBinding],
        executor: object,
        config: KeybindConfig,
        macros: dict[str, Macro] | None = None,
        status_snapshot: Callable[[], dict | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._binds = binds or {}
        self._executor = executor
        self._cfg = config
        self._macros = macros or dict(DEFAULT_MACROS)
        self._status = status_snapshot
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()
        self._pending: dict | None = None     # {name, turn, at}
        self._turn = 0                         # Commander-utterance counter (confirm gate)

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        """Arm tools for allowlisted+known macros, plus confirm + abort. A macro not in the
        allowlist is never advertised (enforced again at run time)."""
        out = [_arm_tool(m) for m in self._allowed_macros()]
        out.append(_CONFIRM_TOOL)
        out.append(_ABORT_TOOL)
        return out

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "abort_keybinds":
                return self._abort()
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
        binding = self._binds.get(macro.action)
        if binding is None or not binding.usable:
            reason = (binding.unusable_reason if binding is not None
                      else f"'{macro.action}' isn't in your Elite Dangerous bindings — bind "
                            f"it to a key in-game so I can press it.")
            self._logline(f"{macro.name} unusable: {reason}")
            return reason

        guard = self._guard()
        if guard is not None:
            self._logline(f"{macro.name} blocked by guard: {guard}")
            return guard

        if not self._cfg.require_confirmation:
            # Confirmation disabled by config — still gated by allowlist + combat guard above.
            return self._execute(macro, binding)

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

        # Re-check the combat guard at execution time: danger may have started since arming.
        guard = self._guard()
        if guard is not None:
            self._logline(f"{macro.name} blocked at confirm: {guard}")
            return guard
        binding = self._binds.get(macro.action)
        if binding is None or not binding.usable:
            return (binding.unusable_reason if binding is not None
                    else f"'{macro.action}' is no longer bound to a key.")
        return self._execute(macro, binding)

    def _abort(self) -> str:
        with self._lock:
            self._pending = None
        try:
            release = getattr(self._executor, "release_all", None)
            if release is not None:
                release()
        except Exception as e:  # noqa: BLE001 — an abort must always complete
            self._logline(f"release_all error during abort: {e}")
        self._logline("aborted — cleared pending, released keys")
        return "Aborted — cleared any armed action and released all keys."

    # -- execution + guard ------------------------------------------------------------
    def _execute(self, macro: Macro, binding: KeyBinding) -> str:
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

    def _guard(self) -> str | None:
        """The combat/interdiction guard. Returns a refusal message when it's not safe to
        act, or None when clear. Skipped entirely if `combat_guard` is off in config."""
        if not self._cfg.combat_guard:
            return None
        snap = self._status() if self._status is not None else None
        state = combat_state(snap)
        return None if state == SAFE else _GUARD_MESSAGES[state]

    def _allowed_macros(self) -> list[Macro]:
        """Macros that are both allowlisted and known — the only ones ever advertised/run."""
        return [self._macros[n] for n in self._cfg.allowlist if n in self._macros]

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
