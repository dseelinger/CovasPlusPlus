"""The closed vocabularies custom-macro authoring validates against (issue #50).

These two tables ARE the structural anti-hallucination for triggers and status conditions: the
compiler (`compile.py`) rejects any trigger or status gate not named here, so a macro can only
ever bind to an event the app actually folds and gate on a flag it actually reads. (Actions are
validated separately, against the live keybind action registry + the Commander's allowlist.)

Everything here is DATA derived from what the app already does:
  * TRIGGERS map a Commander-facing trigger id to the set of bus `ed_event` names that should
    fire it. Both the journal watcher (`ed/journal.py`) and the Status watcher (`ed/status.py`)
    publish `{"type": "ed_event", "event": <name>, ...}`; a trigger may listen for either spelling
    of the same real-world moment (e.g. supercruise exit shows up as the journal's
    `SupercruiseExit` AND the Status transition `SupercruiseExited`).
  * STATUS_CONDITIONS name the boolean keys of the shared `EDContext` snapshot that a
    `require_status` / `await_status` step may check — exactly the keys the sequence runner reads
    between steps (`keybinds.sequence._status_matches`).

Deliberately EXCLUDED from TRIGGERS: danger/interdiction moments. A macro made of Tier-1 ship
actions runs behind the combat guard, which REFUSES while in danger — so a "when interdicted…"
trigger would be structurally dead (always refused). Combat reflexes are the separate Tier-2
path for that (issue #36); we don't offer a trigger the guard would always veto.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Trigger:
    """A folded event a macro may auto-run on. `id` is the stable key stored in the spec and
    advertised to the LLM; `events` are the bus `ed_event` names that fire it; `when` is the
    Commander-facing phrase used in confirmation prompts and help ("when you dock")."""
    id: str
    events: frozenset[str]
    when: str


# Trigger id -> Trigger. Kept small and honest: only moments BOTH watchers already publish and
# that make sense to act on outside combat. Adding one is a data edit here (+ nothing else), which
# is why authoring can trust it as the closed set.
_TRIGGERS: tuple[Trigger, ...] = (
    Trigger("supercruise_exit", frozenset({"SupercruiseExit", "SupercruiseExited"}),
            "you drop out of supercruise"),
    Trigger("supercruise_entry", frozenset({"SupercruiseEntry", "SupercruiseEntered"}),
            "you enter supercruise"),
    Trigger("docked", frozenset({"Docked"}), "you dock"),
    Trigger("undocked", frozenset({"Undocked"}), "you undock"),
    Trigger("docking_granted", frozenset({"DockingGranted"}), "docking is granted"),
    Trigger("arrival", frozenset({"FSDJump"}), "you arrive in a new system"),
    Trigger("landing_gear_down", frozenset({"LandingGearDeployed"}),
            "your landing gear deploys"),
    Trigger("low_fuel", frozenset({"LowFuel"}), "your fuel drops below 25%"),
    Trigger("overheating", frozenset({"Overheating"}), "your ship starts overheating"),
)

TRIGGERS: dict[str, Trigger] = {t.id: t for t in _TRIGGERS}

# Reverse index: bus event name -> the trigger ids that listen for it. Built once so the
# capability's hot on_event path is a single dict lookup, not a scan.
_EVENT_TO_TRIGGERS: dict[str, tuple[str, ...]] = {}
for _t in _TRIGGERS:
    for _ev in _t.events:
        _EVENT_TO_TRIGGERS.setdefault(_ev, ())
        _EVENT_TO_TRIGGERS[_ev] += (_t.id,)


@dataclass(frozen=True)
class StatusCondition:
    """A boolean `EDContext` snapshot key a status-gate step may check. `key` is what the
    sequence runner reads; `label` is the Commander-facing phrase ("the landing gear is down")."""
    key: str
    label: str


# Status key -> StatusCondition. These are the boolean keys `apply_status` folds from
# Status.json Flags into the snapshot the runner reads between steps.
_STATUS_CONDITIONS: tuple[StatusCondition, ...] = (
    StatusCondition("docked", "you're docked"),
    StatusCondition("landing_gear", "the landing gear is down"),
    StatusCondition("supercruise", "you're in supercruise"),
    StatusCondition("hardpoints", "hardpoints are deployed"),
    StatusCondition("low_fuel", "fuel is below 25%"),
    StatusCondition("analysis_mode", "you're in analysis mode"),
    StatusCondition("in_danger", "you're in danger"),
    StatusCondition("being_interdicted", "you're being interdicted"),
)

STATUS_CONDITIONS: dict[str, StatusCondition] = {c.key: c for c in _STATUS_CONDITIONS}


def trigger_ids() -> tuple[str, ...]:
    """Every valid trigger id, in declared order — the closed set authoring may name."""
    return tuple(TRIGGERS.keys())


def status_keys() -> tuple[str, ...]:
    """Every valid status-condition key, in declared order — the closed set a gate may check."""
    return tuple(STATUS_CONDITIONS.keys())


def triggers_for_event(event_name: str) -> tuple[str, ...]:
    """The trigger ids that fire on a given bus `ed_event` name (empty for an unhandled event).
    The capability's on_event uses this to route a folded event to the macros bound to it."""
    return _EVENT_TO_TRIGGERS.get(event_name, ())
