"""Unit tests for EDContext threading discipline (DESIGN §5, §9), offline.

Focus: the journal-thread registry FOLDS mutate in-memory under the lock but persist to disk
OUTSIDE it (#161), so a slow/locked disk can never stall a concurrent voice-loop snapshot() /
summary() read. Driven with injected FAKE registries whose persist() blocks on demand — no real
disk, no thread races beyond the two we spin up deliberately.
"""
from __future__ import annotations

import threading
import time

from covas.ed import EDContext


class _BlockingLedger:
    """A fake visit ledger whose persist() blocks until released, so a test can freeze a writer
    mid-persist and prove a reader still gets the lock. Mirrors the real ledger's two-phase seam:
    record_arrival_deferred() mutates+renders (fast, under the caller's lock); persist() writes."""

    def __init__(self) -> None:
        self.persist_started = threading.Event()
        self.release = threading.Event()
        self.persisted = False

    def record_arrival_deferred(self, event):
        # Fast, in-memory: returns (changed, body-to-write). Runs under EDContext._lock.
        return True, "serialized-body"

    def persist(self, body):
        # The slow disk write — must run OUTSIDE EDContext._lock (that's the whole point of #161).
        self.persist_started.set()
        self.release.wait(timeout=5.0)
        self.persisted = True


def test_snapshot_not_blocked_while_a_registry_persist_is_slow():
    ctx = EDContext()
    ledger = _BlockingLedger()
    ctx.set_visit_ledger(ledger)

    writer_done = threading.Event()

    def writer():
        # Folds under the lock, then blocks inside persist() (outside the lock).
        ctx.record_arrival({"event": "FSDJump", "StarSystem": "Sol"})
        writer_done.set()

    t = threading.Thread(target=writer, name="slow-writer", daemon=True)
    t.start()

    # Wait until the writer is stuck inside the (blocked) persist — i.e. it has already released
    # EDContext._lock. Pre-#161 the disk write ran UNDER the lock, so this reader would hang here.
    assert ledger.persist_started.wait(timeout=2.0), "writer never reached persist()"

    start = time.perf_counter()
    snap = ctx.snapshot()
    elapsed = time.perf_counter() - start

    assert isinstance(snap, dict)
    assert elapsed < 1.0, "snapshot() was blocked on the in-flight disk write"
    assert not writer_done.is_set(), "writer should still be blocked in persist(), proving the read overtook it"

    ledger.release.set()                       # let the writer finish
    assert writer_done.wait(timeout=2.0)
    assert ledger.persisted is True


def test_record_arrival_persists_folded_body_outside_the_lock():
    # The fold result the deferred method produces is what gets persisted (ordering/correctness).
    ctx = EDContext()

    class _CapturingLedger:
        def __init__(self):
            self.written = []

        def record_arrival_deferred(self, event):
            return True, f"body::{event.get('StarSystem')}"

        def persist(self, body):
            self.written.append(body)

    led = _CapturingLedger()
    ctx.set_visit_ledger(led)
    assert ctx.record_arrival({"event": "FSDJump", "StarSystem": "Sol"}) is True
    assert led.written == ["body::Sol"]


def test_record_arrival_no_change_does_not_persist():
    ctx = EDContext()

    class _NoChangeLedger:
        def __init__(self):
            self.persist_calls = 0

        def record_arrival_deferred(self, event):
            return False, None

        def persist(self, body):
            self.persist_calls += 1

    led = _NoChangeLedger()
    ctx.set_visit_ledger(led)
    assert ctx.record_arrival({"event": "ReceiveText"}) is False
    assert led.persist_calls == 0
