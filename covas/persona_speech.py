"""One speech arbiter for the Ship's-AI (persona) voice (issue #146).

Before this, TWO uncoordinated producers spoke the COVAS persona voice on the SAME clean
COVAS bus: the app/conversation path (replies, proactive callouts, route callouts — already
serialized *among themselves* by a lock + Idle-gate) and the audio-layer's PERSONA ambient
cue player (`_persona_chatter`, event-pump-driven, gated by NOTHING the app knew about). With
no shared serialization the two could start together and the mixer would *mix* them — the
companion talking over itself. That breaks the fiction (Immerse) and sounds broken.

This module is the single serialization point: every persona-voice line is ENQUEUED here and
one speaker thread speaks them one at a time. The policy is priority + freshness + preempt, NOT
naive FIFO:

  * **Priority** — a user-directed REPLY outranks a proactive/route CALLOUT, which outranks an
    ambient PERSONA cue. Higher priority is spoken first; a queued lower line waits.
  * **Preempt (cut the current line short mid-word)** — a newly-enqueued line PREEMPTS the one
    already speaking when it *supersedes* it (a fresher line on the SAME subject key — e.g. an
    updated `route:next-star` callout replacing the older one still being read), when it's a
    *safety* line (a subject in :data:`SAFETY_SUBJECTS`, e.g. a #147 hazard warning subsuming an
    ambient musing), when the producer sets ``preempt=True``, or when it's simply *higher
    priority* (a reply shouldn't wait behind an ambient musing). Preempting sets the in-progress
    line's ``cancel`` Event — the SAME mechanism PTT barge-in uses — so real TTS stops mid-word.
    An unrelated *equal/lower* line just QUEUES (ordinary lines don't chop each other off).
  * **Freshness / TTL** — an ambient cue that waited in the queue longer than its ``ttl`` is
    DROPPED, not spoken late (a "nice system" musing 12 s after you left is noise).
  * **Barge-in** — :meth:`PersonaSpeechArbiter.flush` cancels the current line AND drops the
    whole queue; the app wires it into the PTT/user-turn path so no stale ambient plays after
    the Commander has spoken.
  * **Bounded depth** — the queue is capped; an overflowing burst drops the lowest-priority
    line (logged, never silently) so an event storm can't back up minutes of speech.

The arbiter is deliberately pure-ish and standalone: it knows nothing about the mixer or a TTS
provider. Each line carries a ``speak(cancel)`` thunk (the app supplies the real persona TTS /
crew-splitting reply path; the audio layer supplies the persona-on-bus path; tests supply a
fake), and the clock is injected so TTL is deterministic in tests. A speak error is captured on
the line (and re-raised to a *blocking* enqueuer via :meth:`Line.raise_if_error`) so the app's
existing "a dead TTS degrades to text, never crashes" contract is preserved — the speaker
thread itself never dies on a bad line.
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional, Tuple


class Priority(IntEnum):
    """Persona-voice line priorities (higher speaks first / preempts lower).

    AMBIENT — the audio layer's "our-perspective" PERSONA musings (issue #57);
    CALLOUT — proactive LLM callouts + deterministic route callouts (DESIGN §5);
    REPLY   — a user-directed answer (and its degraded/misconfig/interim siblings): the
              Commander asked, so it outranks everything and is never preempted by a cue.
    """

    AMBIENT = 10
    CALLOUT = 20
    REPLY = 30


# Subjects whose arrival ALWAYS preempts whatever is speaking, regardless of priority — a
# safety line the Commander must hear now (e.g. a #147 "next jump's a neutron star" hazard
# warning arriving mid ambient musing). Producers tag such a line with subject="danger".
SAFETY_SUBJECTS = frozenset({"danger", "hazard"})

# Default freshness window for an ambient cue and default bounded queue depth. Both are
# overridable at construction (the app reads them from config); kept here so the module has
# sane standalone defaults for tests and direct use.
DEFAULT_AMBIENT_TTL_S = 8.0
DEFAULT_QUEUE_DEPTH = 8


@dataclass(eq=False)
class Line:
    """One enqueued persona-voice line + its lifecycle state.

    ``speak`` is the thunk the speaker thread runs — ``speak(cancel)`` must block until the line
    has played or ``cancel`` fires (so the arbiter can serialize and cut mid-word). When it is
    None the arbiter falls back to the injected ``default_speak(text, cancel)``. ``cancel`` is
    shared with the producer when supplied (so the app's existing barge-in, which sets the
    turn's cancel Event, cuts an arbiter line too); otherwise the arbiter makes a fresh one.
    """

    text: str
    priority: int
    subject: str = ""
    preempt: bool = False
    ttl: Optional[float] = None
    speak: Optional[Callable[[threading.Event], None]] = None
    cancel: threading.Event = field(default_factory=threading.Event)
    # --- internal bookkeeping (set by the arbiter) ---
    enqueued_at: float = 0.0
    seq: int = 0
    done: threading.Event = field(default_factory=threading.Event)
    dropped: bool = False
    spoken: bool = False
    error: Optional[BaseException] = None

    def expired(self, now: float) -> bool:
        """True once a TTL-bearing line has waited longer than its freshness window."""
        return self.ttl is not None and (now - self.enqueued_at) > float(self.ttl)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the line is spoken, cancelled, or dropped. Returns done-ness."""
        return self.done.wait(timeout)

    def raise_if_error(self) -> None:
        """Re-raise a speak error captured on the speaker thread — so a *blocking* enqueuer
        (the app's reply path) keeps the old ``_speak`` contract: a real TTS failure propagates
        to the caller's degrade-to-text guard instead of being swallowed on the speaker thread."""
        if self.error is not None:
            raise self.error


# Heap entries: (-priority, seq, line) so highest priority pops first, FIFO within a priority.
_Entry = Tuple[int, int, Line]


class PersonaSpeechArbiter:
    """Single queue + single speaker thread for the persona voice (issue #146).

    Inject ``default_speak(text, cancel)`` for lines that carry only text (and for tests: a fake
    speaker), ``clock`` for deterministic TTL, and ``log`` for the no-silent-drops habit. The
    speaker thread is started lazily on the first :meth:`enqueue` (or explicitly via
    :meth:`start`) and stopped by :meth:`stop`; it is a daemon so it never blocks process exit.
    """

    def __init__(
        self,
        default_speak: Optional[Callable[[str, threading.Event], None]] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        log: Optional[Callable[[str], None]] = None,
        max_depth: int = DEFAULT_QUEUE_DEPTH,
    ) -> None:
        self._default_speak = default_speak
        self._clock = clock
        self._log = log or (lambda _m: None)
        self._max_depth = max(1, int(max_depth))
        self._cond = threading.Condition()
        self._heap: List[_Entry] = []
        self._counter = itertools.count()
        self._current: Optional[Line] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- configuration (live) --------------------------------------------
    def set_max_depth(self, depth: int) -> None:
        """Update the bounded queue depth live (a Settings change), min 1."""
        with self._cond:
            self._max_depth = max(1, int(depth))

    # ---- producer API -----------------------------------------------------
    def enqueue(
        self,
        text: str,
        *,
        priority: int,
        subject: str = "",
        preempt: bool = False,
        ttl: Optional[float] = None,
        speak: Optional[Callable[[threading.Event], None]] = None,
        cancel: Optional[threading.Event] = None,
    ) -> Line:
        """Enqueue a persona-voice line and return its :class:`Line` handle.

        ``priority`` is a :class:`Priority` (or int); ``subject`` is a topic key (``route:next-star``,
        ``status``, ``danger`` …) — a fresher line on the same subject supersedes queued/ speaking
        siblings. ``preempt`` forces a cut of the current line; ``ttl`` drops the line if it waits
        too long; ``speak`` overrides the default speaker for THIS line; ``cancel`` shares the
        producer's barge-in Event. A blocking caller then does ``line.wait(); line.raise_if_error()``.
        """
        line = Line(
            text=text, priority=int(priority), subject=subject, preempt=bool(preempt),
            ttl=ttl, speak=speak, cancel=cancel if cancel is not None else threading.Event(),
        )
        with self._cond:
            line.enqueued_at = self._clock()
            line.seq = next(self._counter)
            # 1. Supersede: a fresher SAME-subject line makes queued siblings obsolete — drop them
            #    now so only the newest survives (the one speaking is handled by the preempt step).
            if subject:
                for _, _, q in self._heap:
                    if not q.dropped and q.subject == subject:
                        self._drop_locked(q, f"superseded by fresher {subject!r} line")
            # 2. Enqueue.
            heapq.heappush(self._heap, (-line.priority, line.seq, line))
            # 3. Bounded depth: on overflow drop the lowest-priority live line (possibly this one).
            self._enforce_depth_locked()
            # 4. Preempt the in-progress line if this one outranks/ supersedes/ is safety-critical.
            if self._current is not None and not line.dropped \
                    and self._should_preempt(line, self._current):
                self._current.cancel.set()
                self._log(
                    f"persona: preempting in-progress {self._current.priority}/"
                    f"{self._current.subject!r} for {line.priority}/{subject!r}")
            self._cond.notify()
        self._ensure_thread()
        return line

    def flush(self) -> None:
        """Barge-in: cancel the current line AND drop every queued line, so no stale ambient
        plays after the Commander has spoken. Wired into the app's PTT/user-turn interrupt."""
        with self._cond:
            if self._current is not None:
                self._current.cancel.set()
            for _, _, line in self._heap:
                if not line.dropped:
                    self._drop_locked(line, "flushed on barge-in")
            self._cond.notify()

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        """Start the speaker thread (idempotent). Normally lazy via the first enqueue."""
        self._ensure_thread()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal the speaker thread to exit and join it (bounded). Safe to call more than once."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    # ---- introspection (tests / diagnostics) ------------------------------
    @property
    def current(self) -> Optional[Line]:
        return self._current

    def pending(self) -> int:
        """Count of live (not-yet-dropped) queued lines."""
        with self._cond:
            return sum(1 for _, _, q in self._heap if not q.dropped)

    # ---- policy (pure, directly unit-testable) ----------------------------
    def _should_preempt(self, new: Line, current: Line) -> bool:
        """Whether ``new`` must cut ``current`` short instead of queuing behind it."""
        if new.preempt:
            return True
        if new.subject and new.subject == current.subject:
            return True  # supersede: a fresher line on the same subject
        if new.subject in SAFETY_SUBJECTS:
            return True  # safety line subsumes whatever is playing
        return new.priority > current.priority  # unrelated higher priority still jumps the queue

    def _select_next(self) -> Optional[Line]:
        """Pop the highest-priority live, non-expired line (dropping expired/ dead ones as it
        goes), or None when the queue holds nothing speakable. Directly testable with the
        injected clock — the speaker thread calls it under the condition lock."""
        with self._cond:
            return self._pop_ready_locked()

    # ---- internals --------------------------------------------------------
    def _drop_locked(self, line: Line, reason: str) -> None:
        line.dropped = True
        line.done.set()
        self._log(f"persona: dropped {line.priority}/{line.subject!r} — {reason}")

    def _enforce_depth_locked(self) -> None:
        live = [q for _, _, q in self._heap if not q.dropped]
        if len(live) <= self._max_depth:
            return
        # Drop the lowest-priority line; oldest (smallest seq) breaks a tie, keeping fresher ones.
        victim = min(live, key=lambda q: (q.priority, q.seq))
        self._drop_locked(victim, f"queue full (>{self._max_depth}), lowest priority evicted")

    def _pop_ready_locked(self) -> Optional[Line]:
        now = self._clock()
        while self._heap:
            _, _, line = heapq.heappop(self._heap)
            if line.dropped:
                continue
            if line.expired(now):
                self._drop_locked(line, f"stale (ttl {line.ttl}s exceeded)")
                continue
            return line
        return None

    def _ensure_thread(self) -> None:
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="persona-speech", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                line = self._pop_ready_locked()
                while line is None and not self._stop.is_set():
                    self._cond.wait(timeout=0.2)
                    line = self._pop_ready_locked()
                if self._stop.is_set():
                    if line is not None:
                        self._drop_locked(line, "arbiter stopping")
                    return
                self._current = line
            self._speak_one(line)
            with self._cond:
                self._current = None
            line.done.set()

    def _speak_one(self, line: Line) -> None:
        """Run one line's speak thunk, capturing (never propagating) any error — a bad line is
        dropped + logged and the speaker survives. The captured error is re-raised only to a
        blocking enqueuer via :meth:`Line.raise_if_error`."""
        try:
            if line.cancel.is_set():
                return  # barged in / preempted before we started — skip cleanly
            if line.speak is not None:
                line.speak(line.cancel)
            elif self._default_speak is not None:
                self._default_speak(line.text, line.cancel)
            line.spoken = True
        except BaseException as e:  # noqa: BLE001 — a bad line must never kill the speaker thread
            line.error = e
            self._log(f"persona speak failed ({type(e).__name__}): {e}")
