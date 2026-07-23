"""Compile an authored `MacroSpec` into a runnable macro — the anti-hallucination gate (#50).

This is the structural guarantee the feature turns on: authoring can NEVER invent an action or
a trigger. `compile_macro` resolves every step and the trigger against the REGISTRIES —
  * an ACTION must name a macro in the live keybind action registry AND be in the Commander's
    `[keybinds].allowlist` (so a custom macro is confined to exactly the actions they've already
    opted into — a weapon/eject action can't be smuggled in);
  * a status gate must name a key in `macros.registry.STATUS_CONDITIONS`;
  * a trigger must name an id in `macros.registry.TRIGGERS`.
Anything unknown raises `MacroValidationError` with a TEMPLATED message that lists the real,
allowed options — the model never sees a free-text "make up an action" path.

On success it produces an ordinary `keybinds.registry.Macro` whose `steps` are a FLAT tuple of
`keybinds.sequence.Step` (a referenced single-key action expands to one press/hold; a referenced
sequence action inlines its steps). That macro is then run by the exact same #33 runner behind
the exact same guards — there is no second executor.

Two safety properties are computed, not trusted from the author:
  * effective confirmation = requested `confirm` OR any referenced action's own
    `confirm_required` — a custom macro is never LESS cautious than its most-consequential step.
  * modes = the INTERSECTION of the referenced actions' mode sets — the macro is only valid where
    every step is. A spec mixing incompatible modes (e.g. a ship action + an on-foot action) has
    an empty intersection and is REJECTED at authoring, not left to misfire.
"""
from __future__ import annotations

from ..keybinds.registry import Macro
from ..keybinds.sequence import AWAIT_STATUS as _AWAIT
from ..keybinds.sequence import HOLD as _HOLD
from ..keybinds.sequence import PRESS as _PRESS
from ..keybinds.sequence import REQUIRE_STATUS as _REQUIRE
from ..keybinds.sequence import WAIT as _WAIT
from ..keybinds.sequence import Step
from .registry import STATUS_CONDITIONS, TRIGGERS
from .spec import ACTION, AWAIT_STATUS, REQUIRE_STATUS, WAIT, MacroSpec

# A synthetic prefix so a compiled custom macro's `tool`/`name` never collides with a shipped
# keybind macro name (custom macros aren't advertised as individual tools — they're invoked via
# the `run_macro` tool by name — but the field must still be populated and unique).
CUSTOM_TOOL_PREFIX = "custom_macro:"

# Await-timeout fallback when a spec's await step gives no positive timeout (mirrors the runner's
# own DEFAULT_AWAIT_TIMEOUT intent without importing a private constant).
_DEFAULT_AWAIT_TIMEOUT = 10.0


class MacroValidationError(Exception):
    """An authored macro couldn't be compiled: an unknown/disallowed action, an unknown status
    key or trigger, a cross-mode step mix, or an empty body. Carries a Commander-facing,
    templated message that names the REAL allowed options — never invents one."""


def compile_macro(spec: MacroSpec, *, actions: dict[str, Macro],
                  allowlist: frozenset[str] | set[str] | tuple[str, ...]) -> Macro:
    """Validate `spec` against the registries and return a runnable `Macro`, or raise
    `MacroValidationError`. `actions` is the live keybind action registry (name -> Macro);
    `allowlist` is `[keybinds].allowlist` — the actions the Commander has opted into. Pure and
    side-effect free, so it's the same call the authoring tool and the loader both make."""
    allow = frozenset(allowlist)

    # 1. Trigger must be a known folded event (or empty for manual-only).
    if spec.trigger and spec.trigger not in TRIGGERS:
        raise MacroValidationError(
            f"I don't have a trigger called {spec.trigger!r}. I can auto-run a macro "
            f"when {_options(TRIGGERS.keys())}, or leave it manual (run it by name).")

    steps: list[Step] = []
    mode_intersection: frozenset[str] | None = None   # None == universal (no constraint yet)
    any_confirm = False
    action_count = 0

    # 2. Resolve every step. An unknown action/status is a hard, templated failure.
    for i, st in enumerate(spec.steps, start=1):
        if st.kind == ACTION:
            macro = _resolve_action(st.action, actions=actions, allow=allow)
            action_count += 1
            any_confirm = any_confirm or macro.confirm_required
            mode_intersection = _intersect_modes(mode_intersection, macro, spec.name)
            steps.extend(_action_steps(macro))
        elif st.kind == WAIT:
            steps.append(Step(_WAIT, seconds=max(0.0, st.seconds)))
        elif st.kind in (REQUIRE_STATUS, AWAIT_STATUS):
            _validate_status(st.status)
            if st.kind == REQUIRE_STATUS:
                steps.append(Step(_REQUIRE, status_key=st.status, expect=st.expect,
                                  describe=_status_describe(st.status, st.expect, timed_out=False)))
            else:
                timeout = st.seconds if st.seconds > 0 else _DEFAULT_AWAIT_TIMEOUT
                steps.append(Step(_AWAIT, status_key=st.status, expect=st.expect, seconds=timeout,
                                  describe=_status_describe(st.status, st.expect, timed_out=True)))
        else:  # pragma: no cover — MacroStepSpec.from_dict already rejects unknown kinds
            raise MacroValidationError(f"step {i} has an unknown kind {st.kind!r}.")

    # 3. A macro with no action does nothing — reject it rather than persist a no-op.
    if action_count == 0:
        raise MacroValidationError(
            f"{spec.name!r} has no ship actions — a macro needs at least one thing to do.")

    modes = frozenset() if mode_intersection is None else mode_intersection
    confirm_required = bool(spec.confirm) or any_confirm

    return Macro(
        name=spec.name,
        tool=CUSTOM_TOOL_PREFIX + spec.id,
        action="",                       # sequence macro — action/kind/hold unused
        arm_phrase=f"run your '{spec.name}' macro",
        done_phrase=f"Ran '{spec.name}'.",
        steps=tuple(steps),
        modes=modes,
        confirm_required=confirm_required,
    )


def _resolve_action(name: str, *, actions: dict[str, Macro],
                    allow: frozenset[str]) -> Macro:
    """Resolve an action step's name to a registered, allowlisted `Macro`, or raise a templated
    error. The order matters: an UNKNOWN action lists what actions exist; a known-but-not-allowed
    action tells the Commander to allowlist it (never silently permits it)."""
    if name not in actions:
        raise MacroValidationError(
            f"I don't have a ship action called {name!r}. The actions you can use are "
            f"{_options(sorted(allow & set(actions)))}." if (allow & set(actions)) else
            f"I don't have a ship action called {name!r}, and your allowlist is empty — "
            f"add actions to [keybinds].allowlist first.")
    if name not in allow:
        raise MacroValidationError(
            f"{name!r} isn't in your allowlist, so I can't use it in a macro. Add it to "
            f"[keybinds].allowlist to enable it. Allowed right now: "
            f"{_options(sorted(allow & set(actions))) or 'nothing'}.")
    return actions[name]


def _action_steps(macro: Macro) -> list[Step]:
    """The runnable steps a referenced action contributes: a sequence action inlines its own
    steps; a single-key action becomes one press or hold. Keeps the compiled macro a FLAT list
    the #33 runner consumes with no special-casing."""
    if macro.steps:
        return list(macro.steps)
    if macro.kind == "hold":
        return [Step(_HOLD, action=macro.action, seconds=macro.hold_seconds)]
    return [Step(_PRESS, action=macro.action)]


def _intersect_modes(current: frozenset[str] | None, macro: Macro,
                     macro_name: str) -> frozenset[str] | None:
    """Fold one action's mode set into the running intersection. A mode-agnostic action (empty
    `modes`) adds no constraint. When two constrained actions share no mode the macro can never
    be valid anywhere, so that's a templated authoring failure — caught here, not at run time."""
    if not macro.modes:
        return current
    if current is None:
        return macro.modes
    combined = current & macro.modes
    if not combined:
        raise MacroValidationError(
            f"{macro_name!r} mixes actions from different game modes (for example ship controls "
            f"and on-foot controls), which can never run together. Keep a macro to one mode.")
    return combined


def _validate_status(status: str) -> None:
    if status not in STATUS_CONDITIONS:
        raise MacroValidationError(
            f"I can't check a status called {status!r}. I can gate on "
            f"{_options(STATUS_CONDITIONS.keys())}.")


def _status_describe(status: str, expect: bool, *, timed_out: bool) -> str:
    """A clear Commander-facing failure phrase for a status gate, reusing the registry's label."""
    cond = STATUS_CONDITIONS[status]
    want = cond.label if expect else f"NOT ({cond.label})"
    return (f"timed out waiting until {want}" if timed_out
            else f"can't run that yet — I need {want}")


def _options(values) -> str:
    """Render an allowed-value set as a spoken list for a templated error — the anti-hallucination
    payload: the model is shown only REAL options, never asked to guess."""
    vals = [str(v) for v in values]
    return ", ".join(vals)
