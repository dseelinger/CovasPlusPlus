"""Tier-2 combat reflexes — a SEPARATE, combat-permissive control policy (DESIGN §6).

This is the deliberate INVERSION of the Tier-1 keybind safety model, and it is a distinct
policy object — NOT the Tier-1 guard relaxed:

  * **Tier-1** (`KeybindCapability`) REFUSES to touch controls during combat/interdiction. Its
    guard permits an action only when ED Status proves it's SAFE. That's right for landing
    gear, panels, jumps — things you do when nothing is shooting at you.
  * **Tier-2** (this module) exists *for* combat. A small set of DEFENSIVE/EVASIVE reflexes —
    chaff, heat sink, shield cell, boost — only make sense while you're in danger, so the
    combat-permissive guard permits them ONLY when Status reports danger/interdiction, and
    still HARD-REFUSES a dangerous set (eject cargo, self-destruct, landing gear) at all times.

The two guards live side by side; neither weakens the other. The Tier-1 default allowlist
(`["landing_gear"]`) is untouched, and this whole Tier-2 reflex path is opt-in: `[reflex]` is
off by default and its allowlist ships empty, so a Commander must deliberately enable it and
name each reflex.

This is a PROTOTYPE: exactly one reflex — **fire chaff** — is proven end-to-end on the shared
scancode executor, behind the new combat-permissive guard and the hard abort (`release_all()`).
Dispatch here is the simplest possible path: a direct LLM tool (`fire_chaff`). The fast paths —
an auto-reflex framework (#37) and a local phrase-spotter (#38) — are SEPARATE later issues that
build ON this guard; they are intentionally NOT built here.

Everything is injected (binds, executor, status snapshot) so the whole path is unit-testable
offline with a recording fake executor and a fake Status feed — no real key presses, no real
sleeps, no journal.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from ..keybinds.binds import KeyBinding
from ..keybinds.executor import ExecutorError
from .base import HelpMeta
from .keybind_capability import COMBAT, INTERDICTION, SAFE, UNKNOWN, combat_state

# ---- reflex actions -----------------------------------------------------------------------


@dataclass(frozen=True)
class ReflexAction:
    """A named, deterministic combat reflex the executor can fire. `name` is the allowlist +
    policy key (must be in COMBAT_PERMISSIVE); `action` is the ED `.binds` token pressed; `tool`
    is the LLM tool advertised for it. Like a keybind `Macro` the LLM only ever SELECTS the named
    reflex — it never synthesizes keys. A reflex fires IMMEDIATELY (no arm-and-confirm): speed is
    the point, and the combat-permissive guard is what keeps it safe."""
    name: str
    tool: str
    action: str            # ED .binds element name pressed by the executor
    done_phrase: str
    kind: str = "press"    # "press" (a tap) — reflexes are taps; "hold" reserved for future
    hold_seconds: float = 0.0


# The combat-permissive reflex SET (the "small set" the Tier-2 guard may permit — DESIGN §6).
# These are defensive/evasive: they only make sense while under fire, so the guard permits them
# ONLY in combat/interdiction. Membership here is policy; being wired to dispatch is separate
# (REFLEX_ACTIONS below) — this prototype ships only chaff end-to-end.
COMBAT_PERMISSIVE = frozenset({"chaff", "heat_sink", "shields", "boost"})

# The ALWAYS-REFUSED set — dangerous/irreversible actions the combat-permissive guard NEVER
# permits, in combat or out. This is the teeth of the inverted policy: relaxing the guard *for*
# combat must not become a backdoor to eject cargo / self-destruct / drop landing gear mid-fight.
ALWAYS_REFUSED = frozenset({"eject_cargo", "self_destruct", "landing_gear"})

# The reflexes actually wired to dispatch. The prototype validates exactly ONE end-to-end —
# fire chaff — on the shared executor. The other COMBAT_PERMISSIVE members are recognized by the
# guard (so the policy is real) but not yet dispatchable; #37/#38 and follow-up batches add them.
REFLEX_ACTIONS: dict[str, ReflexAction] = {
    "chaff": ReflexAction(
        name="chaff",
        tool="fire_chaff",
        action="FireChaffLauncher",      # ED .binds token for the chaff launcher
        done_phrase="Chaff away",
    ),
}


# ---- combat-permissive guard (pure, INVERTS Tier-1) ---------------------------------------

# A short label per always-refused action, for a clear refusal message.
_REFUSED_WHY = {
    "eject_cargo": "ejecting cargo",
    "self_destruct": "self-destruct",
    "landing_gear": "dropping the landing gear",
}


def combat_permissive_verdict(action: str, snap: dict | None) -> str | None:
    """The Tier-2 combat-permissive policy, as a pure function. Returns None when `action` is
    PERMITTED to fire right now, or a Commander-facing refusal string when it isn't.

    This deliberately INVERTS the Tier-1 combat guard (`combat_state`): where Tier-1 permits
    only SAFE, Tier-2 permits a combat reflex only in COMBAT/INTERDICTION. The classification of
    the ED snapshot is reused from Tier-1 so both read danger identically — only the verdict
    flips. Three outcomes the tests pin:

      1. a COMBAT_PERMISSIVE action while in danger/interdiction  -> permitted (None);
      2. an ALWAYS_REFUSED action                                 -> refused, in combat or not;
      3. a COMBAT_PERMISSIVE action while NOT in combat           -> refused (reflexes are FOR
         combat), and likewise refused when Status is unavailable (can't prove we're in danger).
    """
    if action in ALWAYS_REFUSED:
        why = _REFUSED_WHY.get(action, action)
        return (f"Refusing — {why} is never a combat reflex. Tier-2 reflexes are defensive "
                f"only; that action is off-limits.")
    if action not in COMBAT_PERMISSIVE:
        return f"'{action}' isn't a Tier-2 combat reflex."
    # A permitted reflex: fire ONLY when Status positively confirms danger. UNKNOWN (no
    # telemetry) can't prove danger, so it's refused too — never fire a reflex "blind".
    state = combat_state(snap)
    if state in (COMBAT, INTERDICTION):
        return None
    if state == UNKNOWN:
        return ("Can't confirm you're in danger — Elite Dangerous status isn't available, so "
                "I'm holding the reflex. Turn on ED monitoring ([elite].enabled) to use it.")
    # SAFE.
    return ("You're not in combat — reflexes like chaff only fire when Status shows you're in "
            "danger or being interdicted.")


# ---- config -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReflexConfig:
    """Immutable snapshot of `[reflex]`. OFF by default and the allowlist ships EMPTY — the whole
    Tier-2 path is opt-in, so default behaviour is exactly as if this capability didn't exist.

      * `combat_guard` — enforce the combat-permissive guard (leave ON; off is an escape hatch
        that lets a permitted reflex fire regardless of Status, but ALWAYS_REFUSED still holds).
      * `allowlist` — the reflex NAMES the companion may fire, the Tier-2 analogue of the Tier-1
        allowlist and entirely SEPARATE from it. Default empty.
    """
    enabled: bool = False
    combat_guard: bool = True
    allowlist: tuple[str, ...] = ()

    @classmethod
    def from_cfg(cls, cfg: dict) -> "ReflexConfig":
        r = cfg.get("reflex", {}) or {}
        d = cls()
        allow = r.get("allowlist")
        if isinstance(allow, (list, tuple)):
            allow = tuple(str(a) for a in allow)
        else:
            allow = d.allowlist
        return cls(
            enabled=bool(r.get("enabled", False)),
            combat_guard=bool(r.get("combat_guard", True)),
            allowlist=allow,
        )


# ---- tools --------------------------------------------------------------------------------

_ABORT_TOOL = {
    "name": "abort_reflex",
    "description": (
        "Hard abort for combat reflexes: immediately release every held key. Call the moment "
        "the Commander says stop/cancel/abort, or if anything about a reflex seems wrong. Shares "
        "the same key executor as the ship controls, so it releases those too."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def _reflex_tool(reflex: ReflexAction) -> dict:
    return {
        "name": reflex.tool,
        "description": (
            f"Fire a combat reflex: {reflex.done_phrase.lower()}. This is a DEFENSIVE reflex that "
            "fires immediately — call it the instant the Commander asks for it while under fire "
            "(e.g. 'chaff!', 'break their lock'). It only works when Elite Dangerous status shows "
            "you're in danger or being interdicted; it is refused when you're safe. Deterministic "
            "named action; you never specify keys."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }


class ReflexCapability:
    """Advertises the allowlisted combat reflexes (chaff today) + a hard abort, and fires them
    behind the combat-permissive guard.

    Injected seams (so the default test run is offline and deterministic):
      * `binds`  — {action_token: KeyBinding} parsed from the active .binds file (may be {}).
      * `executor` — a KeyExecutor (or a fake recorder in tests). SHARED with the keybind /
        honk capabilities when they're on, so one hard abort (`release_all()`) lifts every key.
      * `status_snapshot` — Callable[[], dict|None] returning the live EDContext snapshot for the
        combat-permissive guard, or None when ED monitoring isn't running.
    """

    def __init__(
        self,
        *,
        binds: dict[str, KeyBinding],
        executor: object,
        config: ReflexConfig,
        reflexes: dict[str, ReflexAction] | None = None,
        status_snapshot: Optional[Callable[[], Optional[dict]]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._binds = binds or {}
        self._executor = executor
        self._cfg = config
        self._reflexes = reflexes or dict(REFLEX_ACTIONS)
        self._status = status_snapshot
        self._log = log
        self._lock = threading.Lock()

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        """A fire tool per allowlisted+wired reflex, plus the hard abort. The allowlist and the
        combat-permissive guard are BOTH re-enforced at run time in `run_tool`."""
        out = [_reflex_tool(r) for r in self._allowed_reflexes()]
        out.append(_ABORT_TOOL)
        return out

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="combat reflexes",
            group="your ship",
            one_liner=("When you're under fire I can fire defensive reflexes on command — chaff "
                       "today (heat sink, shield cell, and boost are on the way). Unlike the ship "
                       "controls, which lock down in combat, these are the opposite: they only "
                       "fire WHILE you're in danger or being interdicted, and dangerous actions "
                       "stay off-limits. Bind a second push-to-talk ([reflex].ptt) and a snap "
                       "'chaff!' on it fires locally in a heartbeat — no thinking, no delay. Say "
                       "'abort' to release everything."),
            example="chaff!",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == "abort_reflex":
                return self._abort()
            for r in self._allowed_reflexes():
                if r.tool == name:
                    return self._fire(r)
            return f"Unknown or disallowed combat reflex: {name}"
        except Exception as e:  # noqa: BLE001 — the loop must survive any tool error
            self._logline(f"error in {name}: {e}")
            return f"Combat-reflex error: {e}"

    # -- phrase-spotter fast-path entry (issue #38) -----------------------------------
    def fire_reflex(self, name: str) -> str:
        """Fire an allowlisted reflex BY NAME — the local phrase-spotter's dispatch point (#38).

        The Tier-2 fast path (a second PTT + a local keyword match) bypasses the LLM but MUST NOT
        bypass the safety layer: this routes through the exact same allowlist check + combat-
        permissive guard + shared executor as the LLM `fire_*` tool (`_fire`). There is deliberately
        NO second guard here. The spotter's ABORT sentinel ("abort"/"stop"/…) maps to the shared
        hard abort so a snap "abort!" releases every held key just as fast as a reflex fires. Fail
        soft like `run_tool` — the voice loop must survive any error. Returns the Commander-facing
        result/refusal string (spoken back as feedback; the keypress, if any, already went out)."""
        try:
            if name == "abort":
                return self._abort()
            for r in self._allowed_reflexes():
                if r.name == name:
                    return self._fire(r)
            return f"Unknown or disallowed combat reflex: {name}"
        except Exception as e:  # noqa: BLE001 — the reflex path must fail soft, like run_tool
            self._logline(f"error firing reflex {name}: {e}")
            return f"Combat-reflex error: {e}"

    # -- fire / abort -----------------------------------------------------------------
    def _fire(self, reflex: ReflexAction) -> str:
        """Fire a reflex end-to-end: bind check -> combat-permissive guard -> press. No arm and
        no confirmation — a reflex is meant to be instant; the guard is the safety, not a prompt."""
        problem = self._binding_problem(reflex)
        if problem is not None:
            self._logline(f"{reflex.name} unusable: {problem}")
            return problem

        snap = self._status() if self._status is not None else None
        if self._cfg.combat_guard:
            verdict = combat_permissive_verdict(reflex.name, snap)
            if verdict is not None:
                self._logline(f"{reflex.name} blocked by combat-permissive guard: {verdict}")
                return verdict
        elif reflex.name in ALWAYS_REFUSED:
            # Guard disabled is an escape hatch for the PERMITTED set only — the always-refused
            # set is refused even with the guard off.
            return combat_permissive_verdict(reflex.name, snap) or ""

        binding = self._binds.get(reflex.action)
        try:
            if reflex.kind == "hold":
                self._executor.hold(binding, reflex.hold_seconds)
            else:
                self._executor.press(binding)
        except ExecutorError as e:
            self._logline(f"{reflex.name} injection failed: {e}")
            return f"Couldn't send that key: {e}"
        except Exception as e:  # noqa: BLE001 — never crash the loop on an injection fault
            self._logline(f"{reflex.name} injection error: {e}")
            return f"Combat-reflex injection failed: {e}"
        self._logline(f"fired {reflex.name} -> {binding.key}")
        return f"{reflex.done_phrase} (sent {binding.key})."

    def _abort(self) -> str:
        """Hard abort: release every held key on the shared executor. Never raises — an abort
        must always complete."""
        try:
            release = getattr(self._executor, "release_all", None)
            if release is not None:
                release()
        except Exception as e:  # noqa: BLE001 — an abort must always complete
            self._logline(f"release_all error during abort: {e}")
        self._logline("aborted — released all keys")
        return "Aborted — released all keys."

    # -- helpers ----------------------------------------------------------------------
    def _allowed_reflexes(self) -> list[ReflexAction]:
        """Reflexes that are BOTH allowlisted (config) and wired (REFLEX_ACTIONS) — the run-time
        lookup + advertisement set. Empty by default (opt-in)."""
        return [self._reflexes[n] for n in self._cfg.allowlist if n in self._reflexes]

    def _binding_problem(self, reflex: ReflexAction) -> str | None:
        """A Commander-facing reason the reflex can't run because its ED action isn't bound to a
        keyboard key, or None when usable. Fail-soft: an unbound reflex degrades to a spoken
        'bind it in-game' rather than a silent no-op."""
        b = self._binds.get(reflex.action)
        if b is None:
            return (f"'{reflex.action}' isn't in your Elite Dangerous bindings — bind your "
                    f"{reflex.name} control to a key in-game so I can fire it.")
        if not b.usable:
            return b.unusable_reason
        return None

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


# Re-export the Tier-1 SAFE sentinel so callers importing the reflex module can reason about the
# shared danger classification without also importing keybind_capability.
__all__ = [
    "ReflexAction", "ReflexCapability", "ReflexConfig", "combat_permissive_verdict",
    "COMBAT_PERMISSIVE", "ALWAYS_REFUSED", "REFLEX_ACTIONS", "SAFE",
]
