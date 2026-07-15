"""Macro registry — the seam that keeps ship-action batches modular (DESIGN §6, issue #29).

A `Macro` is a named, deterministic ship action the LLM may SELECT (it never synthesizes
raw keys — the tool schema exposes named actions). Action batches register their macros here
from their OWN module (`keybinds/actions/*.py`), so growing the action set is a NEW module
import — not an edit to `KeybindCapability`. This is the Phase-1 lever: the nav/combat/etc.
batches (#30–#35) each add a `keybinds/actions/` file rather than all piling into the
capability.

Two declarative fields on every macro make the safety layer reusable across batches:
  * `modes` — the game modes the action is valid in (mode-gating; empty = any mode).
  * `confirm_required` — whether the action must be armed-and-confirmed (consequential) or may
    fire immediately (benign/read-only), still behind the allowlist + combat + mode guards.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Macro:
    """A named, deterministic ship action the LLM may select. `action` is the ED binding
    token the executor presses; `kind` is press (a tap) or hold (press for `hold_seconds`).

    A macro is EITHER a single key (`action` + `kind`) or a status-checked SEQUENCE (`steps`
    non-empty — a tuple of `keybinds.sequence.Step`). When `steps` is set the capability runs
    the sequence runner instead of a single press/hold, and `action`/`kind`/`hold_seconds` are
    ignored (pass `action=""`). Either way the LLM only SELECTS the named macro — it never
    assembles the keys/steps itself (issue #33)."""
    name: str            # allowlist key + identity (e.g. "landing_gear")
    tool: str            # the tool name advertised to the LLM (e.g. "toggle_landing_gear")
    action: str          # ED action token in the .binds file (e.g. "LandingGearToggle")
    arm_phrase: str      # what we're about to do, for the confirmation prompt
    done_phrase: str     # spoken result once executed
    kind: str = "press"  # "press" | "hold" (single-key macros only)
    hold_seconds: float = 0.0
    # A status-checked timed sequence (issue #33). Empty = a single-key macro (above). Typed as
    # a string annotation (`from __future__ import annotations`) so registry stays decoupled
    # from the sequence module — no runtime import needed for the default empty tuple.
    steps: "tuple[Step, ...]" = ()
    # Game modes this action is valid in (ed/modes vocabulary). EMPTY = valid in any mode
    # (e.g. a global control). Mode-gating hides an action when the Commander isn't in one of
    # these modes — so on-foot actions aren't offered while flying, and vice-versa.
    modes: frozenset[str] = field(default_factory=frozenset)
    # Consequential actions arm-and-confirm (default); a benign/read-only action can set this
    # False to fire immediately. The global [keybinds].require_confirmation still gates it:
    # effective confirmation = require_confirmation AND confirm_required.
    confirm_required: bool = True


# The process-wide registry, populated by importing `keybinds.actions` (each batch module
# calls register() at import). Keyed by macro name.
_REGISTRY: dict[str, Macro] = {}


def register(macro: Macro) -> Macro:
    """Register a macro under its name (last registration wins). Returns the macro so an
    action module can `LANDING_GEAR = register(Macro(...))` if it wants the reference."""
    _REGISTRY[macro.name] = macro
    return macro


def registered_macros() -> dict[str, Macro]:
    """A copy of every registered macro, keyed by name — the capability's DEFAULT_MACROS."""
    return dict(_REGISTRY)


def clear_registry() -> None:
    """Empty the registry. For tests that register throwaway macros; not used at runtime."""
    _REGISTRY.clear()
