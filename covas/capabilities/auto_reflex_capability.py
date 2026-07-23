"""Tier-2 AMBIENT auto-reflexes — fire defensive reflexes off Status/journal thresholds (DESIGN §6).

This is the AUTOMATIC (no-voice) trigger layer for the Tier-2 combat reflexes shipped in #36. It
adds NOTHING to the safety model — it fires the SAME `REFLEX_ACTIONS` through the SAME
`fire_reflex_action` helper (bind check -> combat-permissive guard -> executor), so an automatic
reflex is exactly as safe as a spoken one. What's new is the *trigger*: instead of an LLM tool
call, a threshold over the live ED state fires the reflex the instant it's crossed. Event-driven
off `Status.json` flag transitions / journal events is the only sub-100ms option and is the SAME
shape as the shipped auto-honk (`HonkCapability`): subscribe to the bus, decide, fire, respect a
governor.

Two reflexes ship, both DEFAULT OFF and per-reflex opt-in (`[reflex.auto.<name>].enabled`):

  * **heat_sink** — deploy a heat sink when the ship overheats. Trigger: ED's `Overheating`
    status flag (ED reports overheating at > 100% heat); the per-reflex `threshold` is the heat
    percent to react at (a value > 100 effectively disables it, since ED never signals hotter).
  * **chaff** — fire chaff when a hostile has a lock: the `EnteredDanger` / `Interdicted` /
    `UnderAttack` triggers, gated on Status showing danger/interdiction. A pure boolean trigger,
    so it carries no numeric threshold.

Governor (reuses the proactive policy's shape — `ProactivePolicy`): a per-reflex `cooldown` plus a
global `min_interval` mean a sustained overheat or a long fight can't machine-gun heat sinks/chaff.

The combat-permissive guard is re-enforced inside `fire_reflex_action` at fire time — so even a
trigger that fired is refused if Status can't positively confirm danger, and the ALWAYS_REFUSED
set is never reachable from here (only `COMBAT_PERMISSIVE` reflexes are wired). The shared hard
abort (`KeyExecutor.release_all()`, wired to `abort_reflex`/`abort_keybinds`) still lifts any key.

Everything is injected (binds, executor, status snapshot, clock) so the whole path is unit-testable
offline with a recording fake executor, a fake Status feed, and a fake clock — no real presses,
no real sleeps, no journal.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..keybinds.binds import KeyBinding
from .keybind_capability import COMBAT, INTERDICTION, combat_state
from .reflex_capability import REFLEX_ACTIONS, fire_reflex_action

# Global governor default: no two auto-reflexes of ANY kind fire within this many seconds, so a
# burst of overlapping transitions (enter danger -> interdicted -> overheat) can't stack presses.
DEFAULT_MIN_INTERVAL = 3.0
# ED signals overheating at > 100% heat via the Overheating flag; this is the default heat-sink
# threshold. Kept as a percent so the setting reads naturally even though ED's signal is boolean.
DEFAULT_HEAT_THRESHOLD = 100.0


# ---- trigger conditions (pure) ------------------------------------------------------------


def _overheating(snap: dict | None, threshold: float) -> bool:
    """Heat-sink condition: fire when ED reports the ship overheating. ED exposes overheating as a
    boolean flag it sets at > 100% heat (there's no finer heat telemetry in Status.json), so the
    numeric `threshold` gates that flag: at the default 100 the flag fires it; a threshold above
    100 can never be met (ED never reports hotter), which is how a Commander turns the reaction
    off by threshold rather than by the enable flag."""
    return bool(snap) and bool(snap.get("overheating")) and threshold <= DEFAULT_HEAT_THRESHOLD


def _being_targeted(snap: dict | None, _threshold: float) -> bool:
    """Chaff condition: fire when a hostile has a lock — Status shows danger or an active
    interdiction. Reuses the Tier-1 danger classification so this reads danger identically to the
    guard that will re-check it. A pure boolean trigger; the threshold is unused."""
    return combat_state(snap) in (COMBAT, INTERDICTION)


# ---- built-in reflex triggers (static) ----------------------------------------------------


@dataclass(frozen=True)
class ReflexTrigger:
    """Static definition of one automatic reflex: which reflex ACTION it fires (`name` keys into
    the shared `REFLEX_ACTIONS`), which bus `ed_event`s WAKE it to re-check, the snapshot
    `condition` that decides whether to fire now, and the governor defaults. Immutable; config
    supplies the per-reflex enable/threshold/cooldown at runtime."""
    name: str
    wake_events: frozenset[str]
    condition: Callable[[Optional[dict], float], bool]
    default_threshold: float
    default_cooldown: float
    summary: str

    def __post_init__(self) -> None:
        # Fail loud (DESIGN §2.5): an auto-reflex must map to a wired, combat-permissive action —
        # a typo here would silently never fire.
        if self.name not in REFLEX_ACTIONS:
            raise KeyError(f"auto-reflex {self.name!r} has no REFLEX_ACTIONS entry")


# The wired automatic reflexes. Each name is a `REFLEX_ACTIONS` key (and therefore a
# COMBAT_PERMISSIVE member), so the same guard governs it. Both default OFF in config.
AUTO_REFLEXES: dict[str, ReflexTrigger] = {
    "heat_sink": ReflexTrigger(
        name="heat_sink",
        # ED's status watcher publishes "Overheating" when the heat flag flips on (> 100%).
        wake_events=frozenset({"Overheating"}),
        condition=_overheating,
        default_threshold=DEFAULT_HEAT_THRESHOLD,
        default_cooldown=10.0,
        summary="deploy a heat sink when the ship overheats",
    ),
    "chaff": ReflexTrigger(
        name="chaff",
        # Danger onset (Status IsInDanger transition), an interdiction (Status flag OR the journal
        # Interdicted event), or a direct UnderAttack journal event all wake the chaff check.
        wake_events=frozenset({"EnteredDanger", "Interdicted", "UnderAttack"}),
        condition=_being_targeted,
        default_threshold=0.0,
        default_cooldown=20.0,
        summary="fire chaff when a hostile locks on or you're interdicted",
    ),
}

# The union of every wake event — a cheap membership test before we walk the reflexes.
_ALL_WAKE_EVENTS: frozenset[str] = frozenset().union(
    *(t.wake_events for t in AUTO_REFLEXES.values()))


# ---- config -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReflexSetting:
    """Per-reflex config: opt-in enable, its trigger `threshold`, and its own cooldown governor.
    DEFAULT OFF — a reflex never auto-fires unless the Commander enables it by name."""
    enabled: bool = False
    threshold: float = 0.0
    cooldown: float = 0.0


@dataclass(frozen=True)
class AutoReflexConfig:
    """Immutable snapshot of `[reflex.auto]`. OFF by default at BOTH levels: the `enabled` master
    switch AND every per-reflex `enabled`. With it off the app behaves exactly as if the automatic
    layer didn't exist. `combat_guard` is carried from the parent `[reflex]` so the automatic path
    obeys the same guard toggle as the verbal one."""
    enabled: bool = False
    combat_guard: bool = True
    min_interval: float = DEFAULT_MIN_INTERVAL
    reflexes: dict[str, ReflexSetting] = field(default_factory=dict)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "AutoReflexConfig":
        r = cfg.get("reflex", {}) or {}
        a = r.get("auto", {}) or {}
        d = cls()
        reflexes: dict[str, ReflexSetting] = {}
        for name, trig in AUTO_REFLEXES.items():
            sub = a.get(name, {}) or {}
            reflexes[name] = ReflexSetting(
                enabled=bool(sub.get("enabled", False)),
                threshold=_as_float(sub.get("threshold"), trig.default_threshold),
                cooldown=_as_float(sub.get("cooldown"), trig.default_cooldown),
            )
        return cls(
            enabled=bool(a.get("enabled", False)),
            # combat_guard defaults to the parent [reflex].combat_guard (leave ON).
            combat_guard=bool(r.get("combat_guard", True)),
            min_interval=_as_float(a.get("min_interval"), d.min_interval),
            reflexes=reflexes,
        )

    def setting(self, name: str) -> ReflexSetting:
        """The resolved per-reflex config, or a disabled default for an unknown name."""
        return self.reflexes.get(name, ReflexSetting())


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ---- governor (reuses the proactive policy's shape) ---------------------------------------


class AutoReflexPolicy:
    """The 'may this reflex fire now?' gate: per-reflex enable + per-reflex cooldown + a global
    min-interval, driven by an injectable monotonic clock (so tests advance time deterministically).
    Same design as `ProactivePolicy` — pure decision + a little last-fire state. `should_fire`
    never mutates; call `mark_fired` only once a press actually landed, so a reflex the guard
    refused doesn't burn its cooldown."""

    def __init__(self, cfg: AutoReflexConfig) -> None:
        self.cfg = cfg
        self._last: dict[str, float] = {}       # reflex name -> last fire time
        self._last_any: float = float("-inf")

    def should_fire(self, name: str, now: float) -> tuple[bool, str]:
        c = self.cfg
        if not c.enabled:
            return False, "auto-reflexes disabled"
        setting = c.setting(name)
        if not setting.enabled:
            return False, f"{name} auto-reflex off"
        if now - self._last_any < c.min_interval:
            return False, f"global governor ({c.min_interval:.0f}s)"
        last = self._last.get(name)
        if last is not None and now - last < setting.cooldown:
            return False, f"{name} cooldown ({setting.cooldown:.0f}s)"
        return True, f"{name} auto-reflex"

    def mark_fired(self, name: str, now: float) -> None:
        """Record a fire at `now`, arming both the per-reflex cooldown and the global interval."""
        self._last[name] = now
        self._last_any = now


# ---- capability ---------------------------------------------------------------------------


class AutoReflexCapability:
    """Ambient (no-voice) auto-reflex reactor. Subscribes to the bus via `on_event` (dispatched by
    the app's event pump), and on a waking `ed_event` fires any enabled reflex whose threshold the
    live Status snapshot meets — through the SHARED `fire_reflex_action` guard/executor path.

    Injected seams (so the default test run is offline and deterministic):
      * `binds` — {action_token: KeyBinding} parsed from the active .binds file (may be {}).
      * `executor` — a KeyExecutor (or a fake recorder in tests). SHARED with the keybind / honk /
        verbal-reflex capabilities, so ONE hard abort (`release_all()`) lifts every held key.
      * `status_snapshot` — Callable[[], dict|None] returning the live EDContext snapshot for the
        trigger conditions + the combat-permissive guard, or None when ED monitoring isn't running.
      * `clock` — monotonic clock for the cooldown governor (injected for hermetic tests).

    No LLM tools: this is a fast, silent path (the issue's "no voice" requirement). It exposes only
    the bus `on_event` hook; the verbal `ReflexCapability` still owns the spoken tools + help.
    """
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "keybinds"

    def __init__(
        self,
        *,
        binds: dict[str, KeyBinding],
        executor: object,
        config: AutoReflexConfig,
        triggers: dict[str, ReflexTrigger] | None = None,
        status_snapshot: Optional[Callable[[], Optional[dict]]] = None,
        policy: Optional[AutoReflexPolicy] = None,
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._binds = binds or {}
        self._executor = executor
        self._cfg = config
        self._triggers = triggers or dict(AUTO_REFLEXES)
        self._status = status_snapshot
        self._policy = policy or AutoReflexPolicy(config)
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()   # serialize concurrent bus events (one fire at a time)

    # -- registry surface -------------------------------------------------------------
    def tools(self) -> list[dict]:
        """No LLM tools — this is the silent, no-voice reactor path (the verbal ReflexCapability
        owns the spoken reflex tools + help). But the capability IS registered so the event pump
        delivers it bus events, and `CapabilityRegistry.tools()/tools_for_level()` iterate EVERY
        registered capability's `tools()`; without this method a registered auto-reflex would
        AttributeError on the next turn. Return an empty list so it registers cleanly and stays
        invisible to the LLM."""
        return []

    # -- bus hook ---------------------------------------------------------------------
    def on_event(self, event: dict) -> None:
        """Bus hook (event-pump thread). On a waking `ed_event`, re-check + fire the matching
        reflex(es). Must never raise — it runs on the shared event-pump thread, and a watcher
        event must not take that thread down."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            name = event.get("event")
            if not name or name not in _ALL_WAKE_EVENTS:
                return
            self._react(str(name))
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass

    # -- decide + fire ----------------------------------------------------------------
    def _react(self, event_name: str) -> None:
        """Fire every enabled reflex woken by `event_name` whose threshold the live snapshot meets.
        Serialized so two near-simultaneous events can't fire on top of each other."""
        with self._lock:
            snap = self._status() if self._status is not None else None
            now = self._clock()
            for trig in self._triggers.values():
                if event_name not in trig.wake_events:
                    continue
                setting = self._cfg.setting(trig.name)
                # Threshold check first (cheap, pure) — skip the governor if the condition's not met.
                if not trig.condition(snap, setting.threshold):
                    continue
                ok, reason = self._policy.should_fire(trig.name, now)
                if not ok:
                    self._logline(f"{trig.name} suppressed: {reason}")
                    continue
                reflex = REFLEX_ACTIONS.get(trig.name)
                if reflex is None:      # defensive — AUTO_REFLEXES validates this at import
                    continue
                fired, msg = fire_reflex_action(
                    reflex, binds=self._binds, executor=self._executor, snap=snap,
                    combat_guard=self._cfg.combat_guard, log=self._log)
                if fired:
                    self._policy.mark_fired(trig.name, now)
                    self._logline(f"auto-{trig.name} on {event_name}: {msg}")
                else:
                    # The shared guard refused at fire time (e.g. Status flipped to SAFE) — do NOT
                    # arm the cooldown, so a legitimately-danger re-trigger can still fire.
                    self._logline(f"auto-{trig.name} on {event_name} refused: {msg}")

    # -- readiness (startup reporting) ------------------------------------------------
    def enabled_reflexes(self) -> list[ReflexTrigger]:
        """The auto-reflexes the Commander has opted into (master + per-reflex enable), for the
        startup readiness log. Empty by default."""
        if not self._cfg.enabled:
            return []
        return [t for t in self._triggers.values() if self._cfg.setting(t.name).enabled]

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


__all__ = [
    "AutoReflexCapability", "AutoReflexConfig", "AutoReflexPolicy", "ReflexSetting",
    "ReflexTrigger", "AUTO_REFLEXES",
]
