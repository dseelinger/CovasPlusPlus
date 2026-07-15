"""A small turn-gated confirmation gate (DESIGN §6 safety layer).

Consequential ship actions ARM but do not fire: the Commander must confirm on a SEPARATE spoken
command. The gate that enforces "separate command" is a tiny state machine — a pending item, the
Commander-utterance turn it was armed on, and a wall-clock arm time — checked against the LIVE
turn counter and clock so the model can't arm-and-confirm inside one turn.

The keybind capability grew this logic inline for the one-action prototype; the custom-macro
capability (issue #50) needs exactly the same policy for its authored macros. Rather than a
second copy that could drift, the shared mechanism lives here as `ConfirmGate`. It is pure of
game/IO concerns (it stores an opaque payload and reports verdicts); the capability owns the
guards, execution, and speech. Everything is injected so it's unit-tested with a fake clock.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")

# Verdicts `confirm()` can return alongside the payload (or None). A caller turns these into
# spoken lines; keeping them as constants means the two capabilities word them consistently.
CONFIRM_NONE_PENDING = "none_pending"     # nothing is armed
CONFIRM_SAME_TURN = "same_turn"           # confirmation arrived in the SAME utterance as the arm
CONFIRM_EXPIRED = "expired"               # the arm aged out of the confirm window
CONFIRM_OK = "ok"                         # a genuine, separate, in-window confirmation


@dataclass(frozen=True)
class ConfirmVerdict(Generic[T]):
    """Outcome of a `confirm()` call. `status` is one of the CONFIRM_* constants; `payload` is the
    armed value only when `status == CONFIRM_OK` (consumed — the gate is cleared)."""
    status: str
    payload: Optional[T] = None


class ConfirmGate(Generic[T]):
    """Holds at most one armed payload behind the turn+window gate. Thread-safe: the event pump
    (a trigger arming) and the worker thread (a voice confirm) can touch it concurrently.

      * `arm(payload)`   — stash a payload as pending, stamped with the current turn + clock.
      * `new_turn()`     — advance the utterance counter (the app calls this per Commander turn).
      * `confirm()`      — verdict: OK (and hand back the payload, clearing the gate) only when a
                           LATER turn's confirmation lands inside the window; else a reason.
      * `clear()`        — drop any pending payload (a hard abort).
      * `pending`        — the armed payload without consuming it (for a status readout).
    """

    def __init__(self, *, confirm_window: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._window = float(confirm_window)
        self._clock = clock
        self._lock = threading.Lock()
        self._payload: Optional[T] = None
        self._armed_turn = 0
        self._armed_at = 0.0
        self._turn = 0

    def new_turn(self) -> None:
        with self._lock:
            self._turn += 1

    def arm(self, payload: T) -> None:
        with self._lock:
            self._payload = payload
            self._armed_turn = self._turn
            self._armed_at = self._clock()

    @property
    def pending(self) -> Optional[T]:
        with self._lock:
            return self._payload

    def clear(self) -> None:
        with self._lock:
            self._payload = None

    def confirm(self) -> ConfirmVerdict[T]:
        """Consume a pending payload iff this is a genuine separate-turn, in-window confirmation.
        Returns a verdict; on OK the payload is returned and the gate is cleared."""
        with self._lock:
            if self._payload is None:
                return ConfirmVerdict(CONFIRM_NONE_PENDING)
            if self._turn <= self._armed_turn:
                return ConfirmVerdict(CONFIRM_SAME_TURN)
            if self._clock() - self._armed_at > self._window:
                self._payload = None
                return ConfirmVerdict(CONFIRM_EXPIRED)
            payload, self._payload = self._payload, None
            return ConfirmVerdict(CONFIRM_OK, payload)
