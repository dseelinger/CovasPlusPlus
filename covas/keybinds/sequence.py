"""Sequenced macros — status-checked timed key sequences (DESIGN §6, issue #33).

The one-action prototype (and the Tier-1 batches) press a *single* key. Real ED tasks are
**sequences**: throttle up, hold vertical thrust to clear the pad, boost out, retract the gear
once you're actually off the pad. Firing those keys blind is fragile — the "retract gear" step
should only run once Status.json says the gear is down, and we want to *verify* it came up
afterwards rather than assume the keypress landed.

This module adds a small, declarative **sequence** representation the LLM only ever SELECTS
(it never synthesises raw keys) plus a deterministic runner that sits ON TOP of the existing
`KeyExecutor` — it reuses `press` / `hold` / `release`, and reads a Status.json snapshot
*between steps* to gate and verify. Every side effect is injected (executor, status source,
sleep, clock, abort flag) so the whole thing is unit-tested offline with no real key presses
and no real waiting.

A sequence is a tuple of `Step`s. Six step kinds cover the design's "macros over single keys":

    press(action)              -> tap a bound key (KeyExecutor.press)
    hold(action, seconds)      -> hold a bound key for a duration (KeyExecutor.hold)
    release(action)            -> lift a key left down by a prior hold (KeyExecutor.release)
    wait(seconds)              -> fixed pause between steps
    require_status(key,expect) -> PRECONDITION: fail the sequence NOW if Status.json's `key`
                                  isn't `expect` (e.g. "gear is down" before a launch)
    await_status(key,expect,timeout) -> BLOCK until Status.json's `key` becomes `expect`, or
                                  fail on timeout (e.g. confirm supercruise actually engaged)

Safety is inherited from the executor + capability: a running sequence checks the injected
`abort` flag before every step (and while waiting), and on abort OR failure it calls the
executor's `release_all()` so a hold can never strand a key down.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .binds import KeyBinding
from .executor import ExecutorError

# ---- step vocabulary ----------------------------------------------------------------------

# Step kinds. Data-only strings so a macro definition (keybinds/actions/*.py) reads declaratively
# and the LLM selects a whole named macro rather than assembling these itself.
PRESS = "press"
HOLD = "hold"
RELEASE = "release"
WAIT = "wait"
REQUIRE_STATUS = "require_status"   # precondition: fail immediately if not satisfied
AWAIT_STATUS = "await_status"       # block until satisfied, or fail on timeout

STEP_KINDS = frozenset({PRESS, HOLD, RELEASE, WAIT, REQUIRE_STATUS, AWAIT_STATUS})

# Ceilings so a bad macro can't hang the worker forever. Holds are already clamped by the
# executor (MAX_HOLD_SECONDS); these bound the sequence's own waits/awaits.
MAX_WAIT_SECONDS = 30.0
DEFAULT_AWAIT_TIMEOUT = 10.0
# Longest single sleep chunk while waiting/awaiting, so the abort flag is honoured promptly
# even inside a long wait (the executor's release_all covers held keys; this covers the loop).
_SLEEP_CHUNK = 0.25


@dataclass(frozen=True)
class Step:
    """One step of a sequenced macro (see module docstring for the kinds).

    `action` is the ED binding token for press/hold/release (None otherwise). `seconds` is the
    hold duration, the wait duration, or the await timeout. `status_key` + `expect` name a key
    in the EDContext status snapshot (e.g. `landing_gear`, `docked`, `supercruise`) and the
    boolean it must equal. `describe` is an optional Commander-facing phrase used in the failure
    message so a timed-out/failed check reads clearly."""
    kind: str
    action: str | None = None
    seconds: float = 0.0
    status_key: str | None = None
    expect: bool = True
    poll: float = 0.5
    describe: str = ""


@dataclass(frozen=True)
class SequenceOutcome:
    """Result of running a sequence. `status` is "done" (all steps ran), "aborted" (the abort
    flag fired mid-run), or "failed" (a precondition/await/binding step failed). `message` is
    the Commander-facing reason for aborted/failed."""
    status: str
    message: str = ""


class SequenceError(Exception):
    """A step couldn't run: an unbound/unusable key, a precondition that isn't met, or an
    await that timed out. Carries a Commander-facing message."""


class _Aborted(Exception):
    """Internal: the injected abort flag fired mid-sequence. Turned into an 'aborted' outcome."""


# ---- runner ------------------------------------------------------------------------------


def run_sequence(
    steps: Sequence[Step],
    *,
    executor: object,
    binds: dict[str, KeyBinding],
    status: Callable[[], dict | None] | None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    abort: Callable[[], bool] = lambda: False,
) -> SequenceOutcome:
    """Run `steps` deterministically on `executor`, reading `status` between steps.

    All side effects are injected: `executor` (press/hold/release/release_all), `status`
    (the live snapshot getter, or None when ED monitoring is off), `sleep`/`clock` (so tests
    inject fake timing — no real waiting), and `abort` (a predicate polled before each step and
    while waiting; when it returns True the sequence stops). On abort OR any failure the runner
    calls `executor.release_all()` so a mid-sequence hold never strands a key down.

    Never raises — always returns a `SequenceOutcome` (the capability turns it into speech)."""
    try:
        for step in steps:
            if abort():
                raise _Aborted()
            _run_step(step, executor=executor, binds=binds, status=status,
                      sleep=sleep, clock=clock, abort=abort)
    except _Aborted:
        _safe_release(executor)
        return SequenceOutcome("aborted", "Aborted — released every held key.")
    except SequenceError as e:
        _safe_release(executor)   # never leave a key down after a failed step
        return SequenceOutcome("failed", str(e))
    except Exception as e:  # noqa: BLE001 — a macro fault must never crash the loop
        _safe_release(executor)
        return SequenceOutcome("failed", f"unexpected error: {e}")
    return SequenceOutcome("done")


def _run_step(
    step: Step,
    *,
    executor: object,
    binds: dict[str, KeyBinding],
    status: Callable[[], dict | None] | None,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    abort: Callable[[], bool],
) -> None:
    k = step.kind
    if k == PRESS:
        _inject(executor.press, _binding(step, binds))
    elif k == HOLD:
        _inject(executor.hold, _binding(step, binds), step.seconds)
    elif k == RELEASE:
        _inject(executor.release, _binding(step, binds))
    elif k == WAIT:
        _sleep_interruptible(step.seconds, sleep, abort)
    elif k == REQUIRE_STATUS:
        if not _status_matches(step, status):
            raise SequenceError(step.describe or _default_status_msg(step))
    elif k == AWAIT_STATUS:
        _await_status(step, status=status, sleep=sleep, clock=clock, abort=abort)
    else:
        raise SequenceError(f"unknown step kind: {k!r}")


def _binding(step: Step, binds: dict[str, KeyBinding]) -> KeyBinding:
    """Resolve a press/hold/release step's binding, or raise with a clear reason. Refusing is
    always safer than pressing the wrong key, so a missing/joystick-only bind is a hard fail."""
    if not step.action:
        raise SequenceError("a key step has no action token")
    b = binds.get(step.action)
    if b is None:
        raise SequenceError(f"'{step.action}' isn't in your Elite Dangerous bindings — bind it "
                            f"to a key in-game.")
    if not b.usable:
        raise SequenceError(b.unusable_reason or f"'{step.action}' has no keyboard binding.")
    return b


def _inject(fn: Callable, *args: object) -> None:
    """Call an executor primitive, normalising its ExecutorError into a SequenceError so the
    runner's single failure path (release + report) handles it."""
    try:
        fn(*args)
    except ExecutorError as e:
        raise SequenceError(f"couldn't send that key: {e}") from e


def _status_matches(step: Step, status: Callable[[], dict | None] | None) -> bool:
    """True when the live status snapshot's `status_key` equals `expect`. When status is
    unavailable (None / not a dict) we can't prove the state, so return False — fail-safe: a
    precondition refuses and an await keeps polling until it times out."""
    snap = status() if status is not None else None
    if not isinstance(snap, dict):
        return False
    return bool(snap.get(step.status_key)) == step.expect


def _await_status(
    step: Step,
    *,
    status: Callable[[], dict | None] | None,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    abort: Callable[[], bool],
) -> None:
    """Poll the status snapshot until `status_key == expect`, or raise on timeout. This is the
    'read Status.json between steps to verify state instead of firing blind' guarantee."""
    timeout = _clamp(step.seconds if step.seconds > 0 else DEFAULT_AWAIT_TIMEOUT)
    poll = max(0.05, step.poll)
    deadline = clock() + timeout
    while True:
        if abort():
            raise _Aborted()
        if _status_matches(step, status):
            return
        if clock() >= deadline:
            raise SequenceError(step.describe or _default_status_msg(step, timed_out=True))
        sleep(min(poll, _SLEEP_CHUNK if poll > _SLEEP_CHUNK else poll))


def _sleep_interruptible(seconds: float, sleep: Callable[[float], None],
                         abort: Callable[[], bool]) -> None:
    """A fixed pause, split into small chunks so the abort flag is honoured mid-wait."""
    remaining = _clamp(seconds)
    while remaining > 1e-9:
        if abort():
            raise _Aborted()
        chunk = min(_SLEEP_CHUNK, remaining)
        sleep(chunk)
        remaining -= chunk


def _safe_release(executor: object) -> None:
    """Call the executor's release_all() defensively — an abort/failure must never strand a
    held key, and cleanup itself must never raise."""
    try:
        release = getattr(executor, "release_all", None)
        if release is not None:
            release()
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass


def _default_status_msg(step: Step, *, timed_out: bool = False) -> str:
    want = "" if step.expect else "not "
    verb = "timed out waiting for" if timed_out else "need"
    return f"{verb} {step.status_key} to be {want}set"


def _clamp(seconds: float) -> float:
    return max(0.0, min(float(seconds), MAX_WAIT_SECONDS))
