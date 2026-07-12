"""Cue registry (C2) — the structural spine of the audio layer.

Mirrors the help registry (covas/capabilities/base.py): a small frozen definition, a single
`cue_problems()` function that IS the contract, a `register()` that refuses an incomplete cue
at wiring time, and a `contract_violations()` a test uses to assert the real registry is
clean. Every SFX, chatter category, music context, and comms cue registers here declaring:

  * `bus`             — which audio bus it plays on (must be a REAL bus — see covas.mixer,
                        so a cue can never target one that doesn't exist);
  * `eligible_states` — the game-state tokens in which it MAY play (C3 computes the live set
                        and asks the registry which cues are eligible);
  * `cooldown_s`      — its own throttle (the C3 governor enforces cooldowns + a global cap);
  * a payload — a `phrasings` pool (chatter), a `samples` set (SFX), or a `context_tag`
                (music). One cue carries whichever fits its kind.

Contract (structural, not prose): a cue WITHOUT a valid bus, or WITHOUT a declared
eligibility set, FAILS. An EMPTY eligibility set is valid — the cue is simply never eligible
(silent), never an error. That's what lets a cue be shipped gated-off without breaking the
contract. The LLM is never in this path; C2 only declares and routes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .buses import BUS_NAMES

_KNOWN_BUSES = frozenset(BUS_NAMES)


@dataclass(frozen=True)
class Cue:
    """One registered cue. Immutable; `eligible_states` coerces to a frozenset and the payload
    pools to tuples so a cue is hashable and can't be mutated after registration."""

    name: str
    bus: str
    eligible_states: frozenset  # tokens in which the cue MAY play; empty = never (silent)
    cooldown_s: float = 0.0
    phrasings: tuple[str, ...] = ()   # chatter phrasing pool (deterministic rotation, C6)
    samples: tuple[str, ...] = ()     # SFX sample set (file names / ids, C8)
    context_tag: str = ""             # music context tag (C7)

    def __post_init__(self) -> None:
        # Coerce ergonomic inputs (a list/tuple/set of states, list payloads) into the
        # immutable shapes, WITHOUT rescuing None/invalid — those must survive so the
        # contract can flag "declared no eligibility set".
        st = self.eligible_states
        if st is not None and not isinstance(st, frozenset) and isinstance(st, (set, list, tuple)):
            object.__setattr__(self, "eligible_states", frozenset(str(s) for s in st))
        if not isinstance(self.phrasings, tuple):
            object.__setattr__(self, "phrasings", tuple(self.phrasings or ()))
        if not isinstance(self.samples, tuple):
            object.__setattr__(self, "samples", tuple(self.samples or ()))

    def is_eligible(self, active_states: frozenset) -> bool:
        """True when at least one of this cue's eligible states is currently active. An empty
        eligibility set never matches — the cue stays silent, no error."""
        if not isinstance(self.eligible_states, (set, frozenset)):
            return False
        return bool(self.eligible_states & active_states)

    def phrasing_at(self, index: int) -> str:
        """Deterministic rotation over the phrasing pool (the C-series prefers rotation to
        random so tests are stable). Empty pool -> ''."""
        if not self.phrasings:
            return ""
        return self.phrasings[index % len(self.phrasings)]


_REQUIRED_STATE_TYPES = (set, frozenset)


def cue_problems(cue: object) -> list[str]:
    """Every reason `cue` is not a COMPLETE, registrable cue, as human-readable strings (empty
    list = complete). This is the SINGLE structural definition of the contract — `validate_cue`
    (wiring guard) and `CueRegistry.contract_violations` (test guard) both build on it so the
    policy can't drift. A cue must have a real bus and a declared eligibility set; an empty set
    is allowed (silent)."""
    if not isinstance(cue, Cue):
        return [f"cue must be a Cue, got {type(cue).__name__}"]
    problems: list[str] = []
    label = str(cue.name or "").strip() or "?"
    bus = str(cue.bus or "").strip()
    if not bus:
        problems.append(f"cue '{label}' has no target bus")
    elif bus not in _KNOWN_BUSES:
        problems.append(
            f"cue '{label}' targets unknown bus {cue.bus!r} (known: {sorted(_KNOWN_BUSES)})")
    if not isinstance(cue.eligible_states, _REQUIRED_STATE_TYPES):
        problems.append(
            f"cue '{label}' must declare an eligibility set "
            f"(got {type(cue.eligible_states).__name__})")
    try:
        if float(cue.cooldown_s) < 0.0:
            problems.append(f"cue '{label}' has a negative cooldown_s")
    except (TypeError, ValueError):
        problems.append(f"cue '{label}' has a non-numeric cooldown_s")
    return problems


def validate_cue(cue: Cue) -> Cue:
    """Reject an incomplete cue so the registry never carries one (raises ValueError with the
    first specific problem); returns the cue unchanged when complete."""
    problems = cue_problems(cue)
    if problems:
        raise ValueError(problems[0])
    return cue


class CueRegistry:
    """Aggregates cues so C3 can ask, for the live game state, which cues are eligible per bus.

    `register()` refuses an incomplete cue and a duplicate name, so a built registry is always
    contract-clean; `contract_violations()` lets a test prove that structurally."""

    def __init__(self, cues: Iterable[Cue] | None = None) -> None:
        self._cues: list[Cue] = []
        self._by_name: dict[str, Cue] = {}
        for cue in cues or []:
            self.register(cue)

    def register(self, cue: Cue) -> None:
        validate_cue(cue)
        name = str(cue.name or "").strip()
        if not name:
            raise ValueError("cue has no name")
        if name in self._by_name:
            raise ValueError(f"duplicate cue name: {name!r}")
        self._cues.append(cue)
        self._by_name[name] = cue

    def cues(self) -> list[Cue]:
        """All registered cues, in registration order."""
        return list(self._cues)

    def get(self, name: str) -> Cue | None:
        return self._by_name.get(str(name or "").strip())

    def contract_violations(self) -> list[str]:
        """Every registered cue's contract problems, flattened (empty = every cue is complete).
        `register()` already refuses incomplete cues, so this is normally empty; it exists so a
        test can assert the REAL registry is clean — the guard every later C-prompt inherits."""
        out: list[str] = []
        for cue in self._cues:
            out.extend(cue_problems(cue))
        return out

    # -- eligibility queries C3 consumes ----------------------------------------
    def eligible(self, active_states: Iterable[str]) -> list[Cue]:
        """Cues eligible for the given active game-state set, in registration order. A cue with
        an empty eligibility set never appears (silent)."""
        active = frozenset(str(s) for s in (active_states or ()))
        return [c for c in self._cues if c.is_eligible(active)]

    def eligible_by_bus(self, active_states: Iterable[str]) -> dict[str, list[Cue]]:
        """Eligible cues grouped by target bus — the shape C3's driver hands to the mixer.
        Buses with no eligible cue are omitted."""
        out: dict[str, list[Cue]] = {}
        for cue in self.eligible(active_states):
            out.setdefault(cue.bus, []).append(cue)
        return out
