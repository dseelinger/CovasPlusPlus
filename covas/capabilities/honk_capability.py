"""Auto-honk — fire the Discovery Scanner on arrival in a new system (N5, DESIGN §6).

The "honk" is a full-system Discovery Scan: hold the Discovery Scanner's fire button for a
few seconds after jumping in. This capability automates it — ON by default but SAFE: it
stays inert until the scanner's fire group is mapped, gated by the keybind safety layer.

It's an AMBIENT capability (like route callouts): it advertises no LLM tools, it just
subscribes to the bus and reacts to the journal's `FSDJump` event (arrival in a new system
via hyperspace). On arrival, if enabled and safe, it drives the scanner deterministically:

  * **Configured** (the scanner's fire group index + trigger are set) — read the CURRENT
    fire group from Status.json, cycle to the scanner's group via the
    `CycleFireGroupNext`/`CycleFireGroupPrevious` keybinds (deterministic — we know both
    indices, so we step exactly the right number of times, no guessing), HOLD the configured
    primary/secondary fire key for the honk duration, then cycle back to the original group.
  * **Not configured** — SAFE DEFAULT: do nothing (we can't prove the current group is the
    scanner and not weapons). Set `allow_unmapped_fire` to opt into the old "hope for the
    best" fallback (hold primary fire, works when the scanner is already the selected group).

Safety (reuses the keybind layer — DESIGN §6):
  * **Supercruise-only** — only honks in supercruise; in normal space it does nothing (holding
    fire there can send you into the Surface Scanner instead of honking).
  * **Combat/interdiction guard** — refuses during danger/interdiction, and when ED status
    is unavailable (can't prove it's safe).
  * **Hard abort** — the shared `KeyExecutor.release_all()` (wired to `abort_keybinds`) lifts
    the held fire key, and the executor clamps hold duration so a key can't stick.
  * Every honk (and every skip, with its reason) is logged.

Everything is injected (binds, executor, status snapshot, the thread spawner) so the whole
sequence is unit-testable offline with a recording fake executor.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from ..keybinds.binds import KeyBinding
from ..keybinds.executor import ExecutorError
from .keybind_capability import SAFE, _GUARD_MESSAGES, combat_state

# ED binding action tokens the honk sequence drives. The model never sees these — this is a
# deterministic executor macro, not an LLM tool.
CYCLE_NEXT = "CycleFireGroupNext"
CYCLE_PREV = "CycleFireGroupPrevious"
PRIMARY_FIRE = "PrimaryFire"
SECONDARY_FIRE = "SecondaryFire"


@dataclass(frozen=True)
class HonkConfig:
    """Immutable snapshot of `[honk]`. Off by default; the capability isn't registered unless
    `enabled`.

    `fire_group` is the Discovery Scanner's fire group index (0-based, as shown in the right
    HUD panel). A negative value means "not configured": auto-honk then stays INERT (it won't
    blind-fire) unless `allow_unmapped_fire` opts into the hold-primary-fire fallback.
    `trigger` picks which fire button the scanner sits on."""
    enabled: bool = False
    fire_group: int = -1
    trigger: str = "primary"        # "primary" | "secondary"
    hold_seconds: float = 6.0
    combat_guard: bool = True
    allow_unmapped_fire: bool = False   # opt back into the blind hold-primary-fire fallback

    @classmethod
    def from_cfg(cls, cfg: dict) -> "HonkConfig":
        h = cfg.get("honk", {}) or {}
        d = cls()
        trigger = str(h.get("trigger", d.trigger) or "").strip().lower()
        trigger = "secondary" if trigger.startswith("sec") else "primary"
        try:
            fire_group = int(h.get("fire_group", d.fire_group))
        except (TypeError, ValueError):
            fire_group = d.fire_group
        try:
            hold_seconds = max(0.0, float(h.get("hold_seconds", d.hold_seconds)))
        except (TypeError, ValueError):
            hold_seconds = d.hold_seconds
        return cls(
            enabled=bool(h.get("enabled", False)),
            fire_group=fire_group,
            trigger=trigger,
            hold_seconds=hold_seconds,
            combat_guard=bool(h.get("combat_guard", True)),
            allow_unmapped_fire=bool(h.get("allow_unmapped_fire", False)),
        )

    @property
    def configured(self) -> bool:
        """Whether a specific scanner fire group is set (else we use the fallback)."""
        return self.fire_group >= 0

    @property
    def fire_action(self) -> str:
        """The ED fire-button action token for the configured trigger."""
        return SECONDARY_FIRE if self.trigger == "secondary" else PRIMARY_FIRE


def cycle_plan(current: int, target: int) -> tuple[str, int]:
    """The fire-group cycle needed to go from `current` to `target`: `(action_token, count)`.

    Deterministic — we know both indices, so we step exactly `|target - current|` times in the
    right direction and never need the (unknown) total fire-group count or any wrapping. A
    positive delta cycles Next, negative cycles Previous, zero is a no-op (`("", 0)`)."""
    delta = target - current
    if delta > 0:
        return CYCLE_NEXT, delta
    if delta < 0:
        return CYCLE_PREV, -delta
    return "", 0


class HonkCapability:
    """Fires the Discovery Scanner on arrival, behind the keybind safety layer.

    Injected seams (so the default test run is offline and deterministic):
      * `binds`  — {action_token: KeyBinding} parsed from the active .binds file (may be {}).
      * `executor` — a KeyExecutor (or a fake recorder in tests) — SHARED with the keybind
        capability when both are on, so a hard abort lifts the held fire key too.
      * `status_snapshot` — Callable[[], dict|None] returning the live EDContext snapshot for
        the combat guard AND the current fire group, or None when ED monitoring isn't running.
      * `spawn` — runs the (blocking, ~hold_seconds) honk sequence off the event-pump thread;
        defaults to a daemon thread. Tests inject a synchronous runner.
    """

    def __init__(
        self,
        config: HonkConfig,
        *,
        binds: dict[str, KeyBinding],
        executor: object,
        status_snapshot: Optional[Callable[[], Optional[dict]]] = None,
        spawn: Optional[Callable[[Callable[[], None]], None]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._cfg = config
        self._binds = binds or {}
        self._executor = executor
        self._status = status_snapshot
        self._spawn = spawn or _default_spawn
        self._log = log
        self._lock = threading.Lock()   # non-blocking: skip a second honk while one is holding

    # -- capability interface ---------------------------------------------------------
    # Ambient/event-driven, like route callouts: no LLM tools, no help metadata.
    def tools(self) -> list[dict]:
        return []

    def on_event(self, event: dict) -> None:
        """Bus hook (event-pump thread). Fire the honk on arrival in a new system. Must never
        raise — a bad event must not take the pump down — and must not block the pump for the
        hold duration, so the actual sequence runs on the injected spawner."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            if event.get("event") != "FSDJump":
                return
            self._spawn(self._do_honk)
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass

    # -- the honk sequence ------------------------------------------------------------
    def _do_honk(self) -> None:
        """Run one honk. Guarded so overlapping arrivals can't stack a second sequence on top
        of a key that's still held. Never raises."""
        if not self._lock.acquire(blocking=False):
            self._logline("skipped: a honk is already in progress")
            return
        try:
            self._honk_sequence()
        except ExecutorError as e:
            self._logline(f"skipped: key injection failed — {e}")
        except Exception as e:  # noqa: BLE001 — a honk must never crash the app
            self._logline(f"error: {e}")
        finally:
            self._lock.release()

    def _honk_sequence(self) -> None:
        # 1. Safety guard (combat/interdiction, or unknown status when the guard is on).
        guard = self._guard()
        if guard is not None:
            self._logline(f"blocked: {guard}")
            return

        # 1b. Only honk in SUPERCRUISE. If you've dropped to normal space, holding fire can
        #     trigger the wrong thing (e.g. sends you into the Surface Scanner). Needs live ED
        #     status to confirm — present whenever the arrival event that triggers us fired.
        snap = self._status() if self._status is not None else None
        if not snap:
            self._logline("skipped: ED status unavailable — can't confirm we're in supercruise.")
            return
        if not snap.get("supercruise"):
            self._logline("skipped: not in supercruise — auto-honk only fires the Discovery "
                          "Scanner in supercruise (so it can't trigger the Surface Scanner).")
            return

        # 2. Unconfigured (no scanner fire group set) can't prove the CURRENT group holds the
        #    scanner and not weapons, so — by this capability's own safety rule — we don't fire.
        #    Auto-honk ships ON but stays INERT until the scanner is mapped ([honk].fire_group);
        #    it never blind-fires. Opt into the old fallback with [honk].allow_unmapped_fire.
        if not self._cfg.configured and not self._cfg.allow_unmapped_fire:
            self._logline("skipped: auto-honk is on but no Discovery Scanner fire group is set "
                          "([honk].fire_group = -1) — set it (or enable "
                          "[honk].allow_unmapped_fire) so we fire the scanner, not weapons.")
            return

        # 3. The fire button must be bound to a key we can press.
        fire_binding = self._binds.get(self._cfg.fire_action)
        if fire_binding is None or not fire_binding.usable:
            self._logline(f"skipped: {self._cfg.fire_action} has no keyboard binding — "
                          f"bind the Discovery Scanner's fire button to a key in-game.")
            return

        # 4. If a scanner fire group is configured, cycle to it first (deterministic).
        reverse: Optional[tuple[str, int]] = None
        if self._cfg.configured:
            plan = self._plan_cycle()
            if plan is None:
                return                     # couldn't determine/reach the group -> already logged
            forward_token, count, reverse = plan
            if count:
                forward = self._binds[forward_token]   # presence checked in _plan_cycle
                for _ in range(count):
                    self._executor.press(forward)

        # 5. Hold the fire button for the honk duration to complete the scan.
        self._executor.hold(fire_binding, self._cfg.hold_seconds)

        # 6. Cycle back to the original fire group so we leave the ship as we found it.
        if reverse is not None:
            back_token, back_count = reverse
            back = self._binds.get(back_token)
            if back is not None and back.usable:
                for _ in range(back_count):
                    self._executor.press(back)
            else:
                self._logline(f"honked, but couldn't restore the fire group "
                              f"({back_token} not bound).")

        where = f"group {self._cfg.fire_group}" if self._cfg.configured else "current group"
        self._logline(f"honked — held {self._cfg.fire_action} for "
                      f"{self._cfg.hold_seconds:g}s ({where}).")

    def _plan_cycle(self) -> Optional[tuple[str, int, Optional[tuple[str, int]]]]:
        """Work out the cycle to reach the configured scanner group from the current one.
        Returns (forward_token, count, reverse_or_None), or None (with a logged reason) when we
        can't safely do it — in which case we must NOT fire, since holding fire in the wrong
        group could fire weapons."""
        current = self._current_fire_group()
        if current is None:
            self._logline("skipped: current fire group unknown (needs ED monitoring) — "
                          "not firing, to avoid holding fire in the wrong group.")
            return None
        forward_token, count = cycle_plan(current, self._cfg.fire_group)
        if count == 0:
            return "", 0, None             # already on the scanner group
        cyc = self._binds.get(forward_token)
        if cyc is None or not cyc.usable:
            self._logline(f"skipped: {forward_token} has no keyboard binding — can't reach the "
                          f"scanner's fire group. Bind fire-group cycling to a key in-game.")
            return None
        back_token = CYCLE_PREV if forward_token == CYCLE_NEXT else CYCLE_NEXT
        return forward_token, count, (back_token, count)

    # -- guards / state ---------------------------------------------------------------
    def _guard(self) -> Optional[str]:
        """Combat/interdiction guard, mirroring the keybind capability. Returns a refusal
        message when it's not safe to act, or None when clear. Skipped if `combat_guard` is
        off (the fallback path can then honk without ED monitoring)."""
        if not self._cfg.combat_guard:
            return None
        snap = self._status() if self._status is not None else None
        state = combat_state(snap)
        return None if state == SAFE else _GUARD_MESSAGES[state]

    def _current_fire_group(self) -> Optional[int]:
        """The currently-selected fire group index from the live ED status, or None when it's
        unavailable (no monitoring, or not in a ship with hardpoints yet)."""
        snap = self._status() if self._status is not None else None
        if not snap:
            return None
        fg = snap.get("fire_group")
        return fg if isinstance(fg, int) and not isinstance(fg, bool) else None

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _default_spawn(fn: Callable[[], None]) -> None:
    """Run the honk sequence on a daemon thread so the ~hold_seconds hold never blocks the
    event pump (which would delay every other bus consumer)."""
    threading.Thread(target=fn, name="auto-honk", daemon=True).start()
