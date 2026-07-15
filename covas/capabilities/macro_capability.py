"""Custom-macro capability (issue #50) — the Commander AUTHORS named, triggerable macros.

The Tier-1 headline differentiator. Where `KeybindCapability` exposes a FIXED catalog of named
actions, this lets the Commander COMPOSE a new named macro conversationally ("call it Dock ASAP;
when docking is granted, throttle to zero and drop the gear"), which is then:
  * validated against the action/trigger REGISTRY (structural anti-hallucination — see
    `macros.compile`), so it can only reference actions the app really has (and the Commander has
    allowlisted) and triggers it really folds;
  * persisted across sessions (`macros.store`, a git-ignored JSONL file);
  * invoked by name ("run Dock ASAP") OR auto-run when its bound journal/Status event fires.

Nothing about execution is new: a compiled custom macro is an ordinary `keybinds.registry.Macro`
with `steps`, run through the SAME `keybinds.sequence.run_sequence` runner, behind the SAME
Tier-1 combat guard, using the SAME shared key executor and the SAME hard-abort event — so one
"abort" releases keys held by a custom macro too, and a custom macro refuses in combat exactly
like a shipped one. The only genuinely new policy is authoring, and its safety is that the
validator can't be talked into an action outside the allowlist.

Safety layer (inherited + reaffirmed):
  1. **Allowlist** — a macro may only use actions in `[keybinds].allowlist` (enforced at
     authoring AND re-checked at run, since the allowlist can change between the two).
  2. **Confirmation** — a consequential macro (any step needs confirmation, or the author asked
     for it) ARMS and waits for a SEPARATE spoken confirm, via the shared turn-gated `ConfirmGate`.
     A triggered consequential macro arms + speaks a prompt rather than firing itself.
  3. **Combat/interdiction guard** — refuses while ED Status shows danger, or when Status is
     unavailable (can't prove it's safe). Re-checked at confirm/run time.
  4. **Hard abort** — `abort_macros` clears any pending arm, sets the shared abort event (stops a
     running sequence between steps), and releases every held key.

Everything is injected (store, binds, executor, status snapshot, allowlist provider, speak,
spawn, clock, sleep, abort event) so the whole path — author -> validate -> persist -> run, and
a triggered run — is unit-tested offline with a recording fake executor and a fake Status feed.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from ..keybinds import actions as _actions  # noqa: F401 — import populates the macro registry
from ..keybinds.binds import KeyBinding
from ..keybinds.confirm import CONFIRM_EXPIRED, CONFIRM_OK, CONFIRM_SAME_TURN, ConfirmGate
from ..keybinds.registry import Macro, registered_macros
from ..keybinds.sequence import run_sequence
from ..macros.compile import MacroValidationError, compile_macro
from ..macros.registry import STATUS_CONDITIONS, TRIGGERS, triggers_for_event
from ..macros.spec import (ACTION, AWAIT_STATUS, REQUIRE_STATUS, WAIT, MacroSpec, MacroStepSpec)
from ..macros.store import MacroStore
from .base import HelpMeta
from .keybind_capability import SAFE, _GUARD_MESSAGES, combat_state

# How long to ignore a re-fire of the SAME macro's trigger, so the journal's `Docked` and the
# Status watcher's `Docked` transition (which arrive within a second of each other) can't run a
# macro twice. Also swallows a rapid double journal write.
_TRIGGER_COOLDOWN = 4.0


# ---- config -------------------------------------------------------------------------------


@dataclass(frozen=True)
class MacroConfig:
    """Immutable snapshot of `[macros]`. OFF by default — the capability isn't registered unless
    `enabled`, so default behaviour is exactly as if custom macros didn't exist."""
    enabled: bool = False
    require_confirmation: bool = True
    combat_guard: bool = True
    mode_guard: bool = True
    confirm_window: float = 60.0

    @classmethod
    def from_cfg(cls, cfg: dict) -> "MacroConfig":
        m = cfg.get("macros", {}) or {}
        d = cls()
        return cls(
            enabled=bool(m.get("enabled", False)),
            require_confirmation=bool(m.get("require_confirmation", True)),
            combat_guard=bool(m.get("combat_guard", True)),
            mode_guard=bool(m.get("mode_guard", True)),
            confirm_window=float(m.get("confirm_window", d.confirm_window)),
        )


# ---- tool schemas -------------------------------------------------------------------------

_CONFIRM_TOOL = {
    "name": "confirm_macro",
    "description": (
        "Confirm and RUN the custom macro you previously armed. Only call this after the "
        "Commander has explicitly confirmed on a NEW, separate command ('confirm', 'do it', "
        "'yes', 'run it'). NEVER call it in the same turn you armed the macro."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_ABORT_TOOL = {
    "name": "abort_macros",
    "description": (
        "Hard abort for custom macros: cancel any armed macro and immediately release every held "
        "key. Call the moment the Commander says stop/cancel/abort, or if a macro seems wrong."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_DELETE_TOOL = {
    "name": "delete_macro",
    "description": "Delete a saved custom macro by name.",
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The macro's name."}},
        "required": ["name"],
    },
}

_LIST_TOOL = {
    "name": "list_macros",
    "description": "List the Commander's saved custom macros (name, trigger, and steps).",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_RUN_TOOL = {
    "name": "run_macro",
    "description": (
        "Run a saved custom macro by name (e.g. the Commander says 'run Dock ASAP'). If the macro "
        "is consequential it will ARM and ask for a separate spoken confirmation first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The macro's name."}},
        "required": ["name"],
    },
}


class MacroCapability:
    """Advertises the authoring + run/confirm/abort tools, runs authored macros behind the same
    safety layer as the keybind capability, and auto-runs triggered macros off the event bus."""

    def __init__(
        self,
        *,
        store: MacroStore,
        config: MacroConfig,
        binds: dict[str, KeyBinding],
        executor: object,
        allowlist: Callable[[], frozenset[str]],
        status_snapshot: Optional[Callable[[], Optional[dict]]] = None,
        actions: Optional[dict[str, Macro]] = None,
        abort_event: Optional[threading.Event] = None,
        speak: Optional[Callable[[str], object]] = None,
        spawn: Optional[Callable[[Callable[[], None]], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._store = store
        self._cfg = config
        self._binds = binds or {}
        self._executor = executor
        self._allowlist = allowlist
        self._status = status_snapshot
        self._actions = actions if actions is not None else registered_macros()
        # Shared with the keybind capability so ONE hard abort stops a running sequence from
        # either; each run clears it first so a stale abort can't kill a fresh run.
        self._abort_flag = abort_event or threading.Event()
        self._speak = speak
        self._spawn = spawn or _default_spawn
        self._clock = clock
        self._sleep = sleep
        self._log = log
        self._gate: ConfirmGate[Macro] = ConfirmGate(confirm_window=config.confirm_window,
                                                      clock=clock)
        self._cooldowns: dict[str, float] = {}   # spec id -> last trigger-fire monotonic time
        self._cooldown_lock = threading.Lock()

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        """The authoring + control tools. The create tool's schema enumerates the CURRENT
        allowlisted actions, valid status keys, and valid triggers, so the model is guided to
        real options — the compiler is the structural guarantee, this is the ergonomic nudge."""
        return [self._create_tool(), _RUN_TOOL, _LIST_TOOL, _DELETE_TOOL,
                _CONFIRM_TOOL, _ABORT_TOOL]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="custom macros",
            group="your ship",
            one_liner=("You can invent your OWN named macros by voice — 'create a macro called "
                       "Dock ASAP: when docking is granted, throttle to zero and drop the gear'. "
                       "I only let a macro use actions you've allowlisted and triggers I actually "
                       "track, so it can't do anything you haven't already enabled. Saved macros "
                       "persist, run on command ('run Dock ASAP') or fire on their trigger, and "
                       "consequential ones still need a spoken confirm; 'abort' stops everything."),
            example="create a macro called gear up: retract the landing gear",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "abort_macros":
                return self._abort()
            if name == "confirm_macro":
                return self._confirm()
            if name == "list_macros":
                return self._list()
            if name == "delete_macro":
                return self._delete(str(inp.get("name") or ""))
            if name == "create_macro":
                return self._create(inp)
            if name == "run_macro":
                return self._run_by_name(str(inp.get("name") or ""))
            return f"Unknown custom-macro tool: {name}"
        except Exception as e:  # noqa: BLE001 — the loop must survive any tool error
            self._logline(f"error in {name}: {e}")
            return f"Custom-macro error: {e}"

    def new_turn(self) -> None:
        """Advance the confirmation gate's utterance counter (app calls it per Commander turn),
        so an armed macro can only be confirmed on a genuinely separate command."""
        self._gate.new_turn()

    # -- authoring --------------------------------------------------------------------
    def _create(self, inp: dict) -> str:
        """Author a macro: build a spec from the tool input, COMPILE it against the registry
        (anti-hallucination — an unknown/disallowed action, status, or trigger fails here with a
        templated message and NOTHING is persisted), then save it."""
        try:
            spec = _spec_from_input(inp)
        except ValueError as e:
            return f"I couldn't build that macro: {e}"
        try:
            compiled = compile_macro(spec, actions=self._actions, allowlist=self._allowlist())
        except MacroValidationError as e:
            # The structural refusal: the model is told exactly why, with real options, and the
            # macro is NOT saved. This is the anti-hallucination surface.
            self._logline(f"rejected macro {spec.name!r}: {e}")
            return str(e)
        self._store.add(spec)
        self._logline(f"saved macro {spec.name!r} ({len(spec.steps)} steps, "
                      f"trigger={spec.trigger or 'none'})")
        return self._describe_saved(spec, compiled)

    def _describe_saved(self, spec: MacroSpec, compiled: Macro) -> str:
        trig = (f" It'll run automatically when {TRIGGERS[spec.trigger].when}."
                if spec.trigger in TRIGGERS else "")
        confirm = (" It's consequential, so I'll ask you to confirm before running it."
                   if compiled.confirm_required and self._cfg.require_confirmation else "")
        return (f"Saved '{spec.name}' with {len(spec.steps)} "
                f"step{'s' if len(spec.steps) != 1 else ''}.{trig}{confirm} "
                f"Say 'run {spec.name}' any time.")

    def _list(self) -> str:
        specs = self._store.all()
        if not specs:
            return ("You haven't saved any custom macros yet. Try 'create a macro called gear up: "
                    "retract the landing gear'.")
        lines = []
        for s in specs:
            trig = f", auto-runs when {TRIGGERS[s.trigger].when}" if s.trigger in TRIGGERS else ""
            lines.append(f"{s.name}: {_summarize_steps(s)}{trig}")
        return "Your macros: " + "; ".join(lines) + "."

    def _delete(self, name: str) -> str:
        if not name.strip():
            return "Which macro should I delete?"
        if self._store.delete(name):
            self._logline(f"deleted macro {name!r}")
            return f"Deleted '{name}'."
        return f"You don't have a macro called '{name}'."

    # -- run / confirm / abort --------------------------------------------------------
    def _run_by_name(self, name: str) -> str:
        """Voice entry point ('run <name>'). Compile fresh (so a changed allowlist is honoured),
        then arm-or-run behind the guards."""
        spec = self._store.get(name)
        if spec is None:
            return f"You don't have a macro called '{name}'."
        try:
            macro = compile_macro(spec, actions=self._actions, allowlist=self._allowlist())
        except MacroValidationError as e:
            # A previously-valid macro can go stale (an action was removed from the allowlist).
            return f"I can't run '{spec.name}' as saved — {e}"
        return self._arm_or_run(macro, spoken=True)

    def _arm_or_run(self, macro: Macro, *, spoken: bool) -> str:
        """Guard, then either execute immediately (benign) or arm for a separate confirmation
        (consequential). `spoken` distinguishes a voice invocation (return the arm prompt to the
        model) from a trigger (the caller handles speaking)."""
        problem = self._preflight(macro)
        if problem is not None:
            return problem
        if not (self._cfg.require_confirmation and macro.confirm_required):
            return self._execute(macro)
        self._gate.arm(macro)
        self._logline(f"armed macro {macro.name!r}, awaiting confirmation")
        return (f"Ready to {macro.arm_phrase}. This is armed but NOT run yet — confirm on a "
                f"separate command (say 'confirm' or 'run it'), then I'll call confirm_macro. "
                f"Say 'abort' to cancel.")

    def _confirm(self) -> str:
        verdict = self._gate.confirm()
        if verdict.status == CONFIRM_SAME_TURN:
            return ("That isn't a separate confirmation yet — the Commander must confirm on a new "
                    "command. Tell them what's armed and wait for them to say it.")
        if verdict.status == CONFIRM_EXPIRED:
            return "That macro expired for safety — ask for it again if you still want it."
        if verdict.status != CONFIRM_OK or verdict.payload is None:
            return "Nothing to confirm — no macro is armed."
        macro = verdict.payload
        # Re-check the guards at execution time — danger may have started since arming.
        problem = self._preflight(macro)
        if problem is not None:
            self._logline(f"macro {macro.name!r} blocked at confirm: {problem}")
            return problem
        return self._execute(macro)

    def _abort(self) -> str:
        self._gate.clear()
        self._abort_flag.set()   # stop a running sequence between steps (the runner polls this)
        try:
            release = getattr(self._executor, "release_all", None)
            if release is not None:
                release()
        except Exception as e:  # noqa: BLE001 — an abort must always complete
            self._logline(f"release_all error during abort: {e}")
        self._logline("aborted — cleared pending macro, released keys")
        return "Aborted — cleared any armed macro and released all keys."

    # -- guards + execution -----------------------------------------------------------
    def _preflight(self, macro: Macro) -> str | None:
        """Every reason a macro can't run right now (binding gap, combat, wrong mode), or None
        when it's clear. Ordered so the most actionable message wins."""
        problem = self._binding_problem(macro)
        if problem is not None:
            return problem
        guard = self._guard()
        if guard is not None:
            return guard
        return self._mode_guard(macro)

    def _execute(self, macro: Macro) -> str:
        """Run the compiled macro through the #33 sequence runner. Clears the shared abort flag
        first so a prior abort can't kill this run; the runner then polls it and calls
        release_all on abort/failure. Never raises — turns the outcome into a spoken result."""
        self._abort_flag.clear()
        outcome = run_sequence(
            macro.steps,
            executor=self._executor,
            binds=self._binds,
            status=self._status,
            sleep=self._sleep,
            clock=self._clock,
            abort=self._abort_flag.is_set,
        )
        if outcome.status == "done":
            self._logline(f"ran macro {macro.name!r}")
            return macro.done_phrase
        if outcome.status == "aborted":
            self._logline(f"macro {macro.name!r} aborted mid-run")
            return outcome.message
        self._logline(f"macro {macro.name!r} failed: {outcome.message}")
        return f"Couldn't finish '{macro.name}' — {outcome.message}"

    def _binding_problem(self, macro: Macro) -> str | None:
        """A Commander-facing reason a key the macro presses isn't bound to a keyboard key, or
        None when every key is usable (mirrors the keybind capability's sequence check)."""
        for step in macro.steps:
            if not step.action:   # wait/status steps press nothing
                continue
            b = self._binds.get(step.action)
            if b is None:
                return (f"'{step.action}' isn't in your Elite Dangerous bindings — bind it to a "
                        f"key in-game so I can run '{macro.name}'.")
            if not b.usable:
                return b.unusable_reason or f"'{step.action}' has no keyboard binding."
        return None

    def _guard(self) -> str | None:
        """Tier-1 combat/interdiction guard, shared with the keybind capability. Returns a refusal
        message when it's not safe to act (or Status is unavailable), else None."""
        if not self._cfg.combat_guard:
            return None
        snap = self._status() if self._status is not None else None
        state = combat_state(snap)
        return None if state == SAFE else _GUARD_MESSAGES[state]

    def _mode_guard(self, macro: Macro) -> str | None:
        """Refuse a macro whose actions aren't valid in the current game mode. Skipped when
        mode-gating is off, the macro is mode-agnostic, or the mode is unknown (can't prove a
        mismatch — the combat guard already covers the no-telemetry case)."""
        if not self._cfg.mode_guard or not macro.modes:
            return None
        snap = self._status() if self._status is not None else None
        mode = snap.get("game_mode") if isinstance(snap, dict) else None
        if mode is None or mode in macro.modes:
            return None
        return (f"Can't run '{macro.name}' right now — its actions only work in a different game "
                f"mode than you're in.")

    # -- trigger binding (event bus) --------------------------------------------------
    def on_event(self, event: dict) -> None:
        """Bus hook (event-pump thread). Auto-run any saved macro bound to the folded event that
        just fired. Must never raise (a bad event mustn't take the pump down) and must not block
        the pump for a macro's duration — execution runs on the injected spawner."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            name = event.get("event")
            if not name:
                return
            trig_ids = triggers_for_event(str(name))
            if not trig_ids:
                return
            for spec in self._store.all():
                if spec.trigger and spec.trigger in trig_ids:
                    self._fire_trigger(spec)
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass

    def _fire_trigger(self, spec: MacroSpec) -> None:
        """A saved macro's trigger fired. Debounce (so a doubled journal/Status event can't run it
        twice), compile fresh, then either arm+speak (consequential) or run (benign) off-thread."""
        if not self._cooldown_ok(spec.id):
            return
        try:
            macro = compile_macro(spec, actions=self._actions, allowlist=self._allowlist())
        except MacroValidationError as e:
            self._logline(f"trigger for {spec.name!r} skipped — no longer valid: {e}")
            return

        if self._cfg.require_confirmation and macro.confirm_required:
            # A consequential triggered macro does NOT fire itself — it arms and asks. The
            # Commander's spoken 'confirm' advances the gate turn, so the confirm passes.
            self._gate.arm(macro)
            when = TRIGGERS[spec.trigger].when if spec.trigger in TRIGGERS else "that happened"
            self._logline(f"trigger armed macro {spec.name!r} ({when})")
            self._say(f"{when.capitalize()} — say 'confirm' to run your '{spec.name}' macro, "
                      f"or 'abort' to skip it.")
            return

        # A benign triggered macro runs immediately behind the guards, off the pump thread.
        self._logline(f"trigger firing macro {spec.name!r}")
        self._spawn(lambda: self._run_triggered(macro))

    def _run_triggered(self, macro: Macro) -> None:
        result = self._arm_or_run(macro, spoken=False)
        # Speak a benign trigger's outcome (success or a meaningful refusal) so the Commander
        # isn't left guessing; a guard refusal is logged inside the guard already.
        self._say(result)

    def _cooldown_ok(self, spec_id: str) -> bool:
        """True (and stamp now) if this macro hasn't triggered within _TRIGGER_COOLDOWN, else
        False. Kills the journal-vs-Status double fire for the same real-world moment."""
        now = self._clock()
        with self._cooldown_lock:
            last = self._cooldowns.get(spec_id, -1e9)
            if now - last < _TRIGGER_COOLDOWN:
                return False
            self._cooldowns[spec_id] = now
            return True

    # -- helpers ----------------------------------------------------------------------
    def _create_tool(self) -> dict:
        """Build the create_macro schema with the CURRENT allowlisted actions, status keys, and
        triggers enumerated as enums, so the model is nudged toward real options."""
        allowed_actions = sorted(self._allowlist() & set(self._actions))
        status_keys = list(STATUS_CONDITIONS.keys())
        trigger_ids = list(TRIGGERS.keys())
        # An empty `enum: []` is rejected by some providers, so constrain the action field to the
        # allowlist only when it's non-empty; otherwise leave it a free string (the compiler still
        # refuses anything not allowlisted — the enum is a nudge, the validator is the guarantee).
        action_field = {"type": "string",
                        "description": "For type=action: the ship action."}
        if allowed_actions:
            action_field["enum"] = allowed_actions
        return {
            "name": "create_macro",
            "description": (
                "Create a NEW named custom macro from the Commander's description, and save it. "
                "A macro is an ordered list of steps. Each step is one of: "
                "{'type':'action','action':<name>} to perform an allowlisted ship action; "
                "{'type':'wait','seconds':N} to pause; "
                "{'type':'require_status','status':<key>,'expect':true|false} to REFUSE unless a "
                "game-status flag matches; "
                "{'type':'await_status','status':<key>,'expect':true|false,'seconds':N} to WAIT "
                "until it matches (up to N seconds). Optionally set 'trigger' to auto-run the "
                "macro on a game event, and 'confirm' (default true) to require a spoken confirm. "
                "You may ONLY use the action names, status keys, and triggers listed in the "
                "schema — if the Commander asks for anything else, tell them it isn't available "
                "rather than inventing it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "A short spoken name, e.g. 'Dock ASAP'."},
                    "trigger": {"type": "string", "enum": trigger_ids,
                                "description": "Optional. Auto-run the macro on this game event."},
                    "confirm": {"type": "boolean",
                                "description": "Require a spoken confirmation before running "
                                               "(default true; kept true for consequential steps)."},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string",
                                         "enum": [ACTION, WAIT, REQUIRE_STATUS, AWAIT_STATUS]},
                                "action": action_field,
                                "seconds": {"type": "number",
                                            "description": "For wait/await: seconds."},
                                "status": {"type": "string", "enum": status_keys,
                                           "description": "For require/await_status: the flag."},
                                "expect": {"type": "boolean",
                                           "description": "Desired value of the status flag."},
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["name", "steps"],
            },
        }

    def _say(self, text: str) -> None:
        """Speak a triggered macro's prompt/outcome through the injected seam if wired; else log."""
        text = (text or "").strip()
        if not text:
            return
        if self._speak is not None:
            try:
                self._speak(text)
                return
            except Exception:  # noqa: BLE001 — a TTS hiccup must not crash the trigger thread
                pass
        self._logline(f"(would speak): {text}")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


# ---- input parsing --------------------------------------------------------------------


def _spec_from_input(inp: dict) -> MacroSpec:
    """Turn a create_macro tool payload into a `MacroSpec`. Structural shape only — it raises
    ValueError for a malformed payload (no name/steps, or a step with no usable type). The
    VALUES (action/status/trigger names) are validated by the compiler, not here, so the
    Commander gets the templated 'no such action' message rather than a generic parse error."""
    if not isinstance(inp, dict):
        raise ValueError("the macro must be an object")
    name = str(inp.get("name") or "").strip()
    if not name:
        raise ValueError("a macro needs a name")
    raw_steps = inp.get("steps")
    if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
        raise ValueError("a macro needs at least one step")
    steps = tuple(_step_from_input(s) for s in raw_steps)
    return MacroSpec(
        name=name,
        steps=steps,
        trigger=str(inp.get("trigger") or "").strip(),
        confirm=bool(inp.get("confirm", True)),
    )


def _step_from_input(s: dict) -> MacroStepSpec:
    """One tool-step dict -> MacroStepSpec. Accepts the 'type' discriminator the tool schema uses
    (also tolerating 'kind'). Raises ValueError for a step whose type is missing/unknown."""
    if not isinstance(s, dict):
        raise ValueError("each step must be an object")
    kind = str(s.get("type") or s.get("kind") or "").strip()
    if kind == ACTION:
        return MacroStepSpec(kind=ACTION, action=str(s.get("action") or "").strip())
    if kind == WAIT:
        return MacroStepSpec(kind=WAIT, seconds=_as_float(s.get("seconds")))
    if kind in (REQUIRE_STATUS, AWAIT_STATUS):
        return MacroStepSpec(kind=kind, status=str(s.get("status") or "").strip(),
                             expect=bool(s.get("expect", True)), seconds=_as_float(s.get("seconds")))
    raise ValueError(f"a step has an unknown type {kind!r}")


def _summarize_steps(spec: MacroSpec) -> str:
    """A short human phrase for a spec's body, for list/help readouts."""
    parts = []
    for st in spec.steps:
        if st.kind == ACTION:
            parts.append(st.action)
        elif st.kind == WAIT:
            parts.append(f"wait {st.seconds:g}s")
        elif st.kind == REQUIRE_STATUS:
            parts.append(f"need {st.status}={st.expect}")
        elif st.kind == AWAIT_STATUS:
            parts.append(f"await {st.status}={st.expect}")
    return " -> ".join(parts)


def _as_float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _default_spawn(fn: Callable[[], None]) -> None:
    """Run a triggered macro on a daemon thread so its duration never blocks the event pump."""
    threading.Thread(target=fn, name="custom-macro", daemon=True).start()
