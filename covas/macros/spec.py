"""The persisted custom-macro spec (issue #50) — high-level, serializable, game-agnostic.

A `MacroSpec` is what the Commander authors and what we store on disk: a name, an ordered list
of `MacroStepSpec`s, and an optional trigger. It is DELIBERATELY high-level — an action step
names a keybind action (e.g. `throttle_zero`), never a raw key or a `keybinds.sequence.Step`.
Compiling it into a runnable macro (resolving the action name, expanding it to press/hold steps,
validating every reference) is `compile.py`'s job; keeping the two apart means the stored form
stays readable/editable and can't smuggle in an un-vetted key sequence.

Four step kinds mirror the sequence framework's user-meaningful subset (press/hold are hidden
inside an `action`; `release` is an executor internal a macro never scripts directly):

    action(name)                   -> perform an allowlisted named ship action
    wait(seconds)                  -> a fixed pause
    require_status(status, expect) -> PRECONDITION: fail now unless the flag matches
    await_status(status, expect, seconds) -> BLOCK until the flag matches, or fail on timeout

Persistence is fail-soft JSONL (see `store.py`): every field round-trips through `to_dict` /
`from_dict`, and `from_dict` is forgiving of a hand-edited file (missing optionals default;
only a blank name or empty steps is rejected, since those can't be a macro).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Step kinds (the persisted vocabulary). PRESS/HOLD/RELEASE from the sequence framework are an
# implementation detail an `ACTION` compiles down to — the spec never spells them.
ACTION = "action"
WAIT = "wait"
REQUIRE_STATUS = "require_status"
AWAIT_STATUS = "await_status"
STEP_KINDS = frozenset({ACTION, WAIT, REQUIRE_STATUS, AWAIT_STATUS})


def _now_iso() -> str:
    """UTC second-precision timestamp — enough to order macros, no locale surprises."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class MacroStepSpec:
    """One authored step. Which fields matter depends on `kind`:
      * ACTION          -> `action` (a keybind action name).
      * WAIT            -> `seconds`.
      * REQUIRE/AWAIT   -> `status` + `expect` (+ `seconds` = the await timeout).
    Unused fields keep their defaults, so a step is always fully-formed on disk."""
    kind: str
    action: str = ""
    seconds: float = 0.0
    status: str = ""
    expect: bool = True

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind}
        if self.kind == ACTION:
            d["action"] = self.action
        elif self.kind == WAIT:
            d["seconds"] = self.seconds
        else:  # require/await status
            d["status"] = self.status
            d["expect"] = self.expect
            if self.kind == AWAIT_STATUS:
                d["seconds"] = self.seconds
        return d

    @classmethod
    def from_dict(cls, d: dict) -> MacroStepSpec:
        """Build from a parsed dict, forgiving of hand edits. Raises ValueError only when the
        kind itself is unusable — a wrong field is tolerated (defaults fill in) so the compiler,
        not the loader, produces the templated 'unknown action/status' message the user sees."""
        if not isinstance(d, dict):
            raise ValueError("a macro step must be an object")
        kind = str(d.get("kind", "")).strip()
        if kind not in STEP_KINDS:
            raise ValueError(f"unknown step kind {kind!r}")
        return cls(
            kind=kind,
            action=str(d.get("action", "") or "").strip(),
            seconds=_as_float(d.get("seconds", 0.0)),
            status=str(d.get("status", "") or "").strip(),
            expect=_as_bool(d.get("expect", True), default=True),
        )


@dataclass(frozen=True)
class MacroSpec:
    """A named, authored macro. `steps` is the ordered body; `trigger` (a `registry` trigger id)
    is the folded event it auto-runs on, or "" for manual-only ("run <name>"). `confirm` is the
    Commander's requested confirmation policy — the compiler can only ever RAISE it (a macro is
    never less cautious than its most-consequential action), never lower it."""
    name: str
    steps: tuple[MacroStepSpec, ...]
    trigger: str = ""
    confirm: bool = True
    id: str = ""
    when: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        # Frozen dataclass: fill an absent id/timestamp through object.__setattr__.
        if not self.id:
            object.__setattr__(self, "id", uuid.uuid4().hex)
        if not self.when:
            object.__setattr__(self, "when", _now_iso())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "trigger": self.trigger,
            "confirm": self.confirm,
            "steps": [s.to_dict() for s in self.steps],
            "when": self.when,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MacroSpec:
        """Parse one stored macro. Raises ValueError for a spec with no name or no steps (those
        can't be a macro); everything else defaults. Trigger/action/status VALUES are not checked
        here — that's the compiler's templated-error job, so a spec that references an action you
        later removed still LOADS (and fails loudly only when run/compiled)."""
        if not isinstance(d, dict):
            raise ValueError("a macro must be an object")
        name = str(d.get("name", "") or "").strip()
        if not name:
            raise ValueError("a macro needs a name")
        raw_steps = d.get("steps")
        if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
            raise ValueError(f"macro {name!r} has no steps")
        steps = tuple(MacroStepSpec.from_dict(s) for s in raw_steps)
        return cls(
            name=name,
            steps=steps,
            trigger=str(d.get("trigger", "") or "").strip(),
            confirm=_as_bool(d.get("confirm", True), default=True),
            id=str(d.get("id", "") or ""),
            when=str(d.get("when", "") or ""),
        )


def _as_float(v: object) -> float:
    """Coerce a possibly-hand-edited numeric field to float, defaulting to 0.0 (never raises)."""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# Strings a hand-editor might reasonably type for a boolean. A raw `bool("false")` is True (any
# non-empty string is truthy), which would silently INVERT a precondition — so parse these
# explicitly rather than trusting `bool()`.
_FALSE_STRINGS = frozenset({"false", "0", "no", "off", "n", "f"})
_TRUE_STRINGS = frozenset({"true", "1", "yes", "on", "y", "t"})


def _as_bool(v: object, *, default: bool = True) -> bool:
    """Coerce a possibly-hand-edited boolean field robustly (never raises). A real bool passes
    through; a string is matched case-insensitively against the true/false vocabularies above so
    `"false"`/`"0"`/`"no"` become False (not True as `bool()` would give); numbers use their
    truthiness; None and any unrecognised value fall back to `default` (don't guess)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _FALSE_STRINGS:
            return False
        if s in _TRUE_STRINGS:
            return True
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    if v is None:
        return default
    return bool(v)
