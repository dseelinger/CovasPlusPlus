"""Shared hard-abort coordinator for keybind sequences and custom macros (issue #154).

The hard abort is the loop-level half of the safety layer: when the Commander says
"abort", a running status-checked sequence must STOP between steps (the executor's
`release_all()` is the key-level half that lifts held keys). One abort must stop EVERY
in-flight run — a voice-invoked keybind sequence AND any triggered custom macro — because
they share the same key executor; a half-stopped run could re-press a key the abort just
released.

The bug this replaces (#154): a single `threading.Event` was overloaded as BOTH the global
stop signal (`set()` on abort, polled by the runner) AND a per-run reset (`clear()` at the
start of every run, so a stale abort couldn't kill a fresh run). Because the Event was
deliberately SHARED between the two capabilities and triggered macros run on their own daemon
threads, a benign macro starting concurrently with a running sequence would `clear()` the abort
that had just been `set()` for that sequence. The sequence's next poll then read False and
re-pressed its remaining keys AFTER `release_all()` had already lifted them — the hard-abort
guarantee was silently defeated.

Fix: don't overload one flag. Each run takes its OWN abort token (a monotonic generation id).
A hard abort marks EVERY currently-live token aborted; starting a new run mints a FRESH token
(which begins un-aborted, preserving the old "a new run is not killed by a stale abort" intent)
instead of clearing shared state. A new run therefore can never erase an abort meant for a
concurrently-running one. All mint/set/poll transitions are serialized under one lock, so the
set-vs-clear race is gone.

The user-facing contract, preserved exactly:
  * saying "abort" stops ALL in-flight keybind sequences AND triggered macros, and
  * a newly-starting run is never killed by an abort meant for an earlier, already-finished run,
    nor does starting it erase an abort meant for a still-running one.

Everything is plain in-memory state guarded by a lock, so the whole thing is unit-tested
offline with no threads required (though it is thread-safe by construction).
"""
from __future__ import annotations

import threading


class AbortController:
    """Coordinates the shared hard-abort across concurrent keybind sequences and macros.

    Usage from a capability run:
        token = controller.begin()
        try:
            run_sequence(..., abort=lambda: controller.is_aborted(token))
        finally:
            controller.end(token)

    And from the hard-abort tool handler:
        controller.abort()   # marks every live run aborted; then release_all() on the executor
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0
        self._live: set[int] = set()       # tokens for runs currently in flight
        self._aborted: set[int] = set()    # live tokens the current abort covers
        self._abort_count = 0              # observability: number of abort() calls

    def begin(self) -> int:
        """Register a new run and return its abort token. The token starts NOT aborted even if a
        prior abort is outstanding — a genuinely new run is fresh (this is the safe half of the
        old per-run `clear()`, without touching any other run's state)."""
        with self._lock:
            token = self._next
            self._next += 1
            self._live.add(token)
            self._aborted.discard(token)
            return token

    def end(self, token: int) -> None:
        """Retire a finished run's token. Idempotent; safe to call from a `finally`."""
        with self._lock:
            self._live.discard(token)
            self._aborted.discard(token)

    def is_aborted(self, token: int) -> bool:
        """True once a hard abort has been raised for this still-live run. Polled by the sequence
        runner before every step and while waiting."""
        with self._lock:
            return token in self._aborted

    def abort(self) -> None:
        """Hard abort: mark every currently-live run aborted so each stops at its next poll. Runs
        that begin AFTER this call get fresh, un-aborted tokens (an abort doesn't latch onto
        future runs — matching the pre-#154 behaviour, minus the cross-run clear race)."""
        with self._lock:
            self._abort_count += 1
            self._aborted |= self._live

    @property
    def abort_count(self) -> int:
        """How many times `abort()` has been called — for logging/observability and tests."""
        with self._lock:
            return self._abort_count
