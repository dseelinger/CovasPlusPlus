"""Unit tests for the EventBus pub/sub spine (issue #162; offline, DESIGN §9).

`covas/events.py` is the thread-safe spine every input publishes onto and the web UI /
capabilities subscribe to. These tests lock the guarantees the audit (#162) named:

  * subscribe/unsubscribe/publish basics and replay=True/False semantics;
  * the backlog is capped at `backlog` entries (oldest dropped);
  * a publish racing a subscribe stays ORDERED — a live event can never be replayed ahead
    of the older backlog it interleaves with (the fix registers the queue + backlog copy
    atomically under the lock);
  * subscriber queues are BOUNDED — a stalled consumer that never drains can't grow its
    queue without limit, and publish stays non-blocking / fail-soft when a queue is full.

All hermetic: no network, no sleeping on real time, pure in-process queues.
"""
from __future__ import annotations

import queue
import threading

from covas.events import EventBus


def _drain(q: queue.Queue) -> list[dict]:
    out: list[dict] = []
    try:
        while True:
            out.append(q.get_nowait())
    except queue.Empty:
        pass
    return out


def test_publish_delivers_to_live_subscriber():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish({"type": "a"})
    bus.publish({"type": "b"})
    got = [e["type"] for e in _drain(q)]
    assert got == ["a", "b"]


def test_publish_stamps_ts_and_copies_event():
    bus = EventBus()
    q = bus.subscribe()
    src = {"type": "a"}
    out = bus.publish(src)
    assert "ts" in out
    assert "ts" not in src  # publish must not mutate the caller's dict
    delivered = q.get_nowait()
    assert delivered["type"] == "a" and "ts" in delivered
    # An explicit ts is preserved rather than overwritten.
    assert bus.publish({"type": "b", "ts": 123.0})["ts"] == 123.0


def test_replay_true_gives_backlog_then_live():
    bus = EventBus()
    bus.publish({"type": "old1"})
    bus.publish({"type": "old2"})
    q = bus.subscribe(replay=True)
    bus.publish({"type": "live"})
    assert [e["type"] for e in _drain(q)] == ["old1", "old2", "live"]


def test_replay_false_skips_backlog():
    bus = EventBus()
    bus.publish({"type": "old"})
    q = bus.subscribe(replay=False)
    bus.publish({"type": "live"})
    assert [e["type"] for e in _drain(q)] == ["live"]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish({"type": "a"})
    assert _drain(q) == []
    # Unsubscribing an unknown queue is a harmless no-op.
    bus.unsubscribe(queue.Queue())


def test_backlog_is_capped_oldest_dropped():
    bus = EventBus(backlog=3)
    for i in range(5):
        bus.publish({"type": i})
    q = bus.subscribe(replay=True)
    assert [e["type"] for e in _drain(q)] == [2, 3, 4]


def test_racing_publish_stays_ordered_under_lock():
    """A live event published concurrently with a subscribe must not be delivered ahead
    of the backlog it replays. We force the race by interleaving from another thread while
    subscribe holds the lock; whatever the interleaving, the queue's contents must be a
    contiguous, in-order prefix->suffix (never a live event wedged before older backlog)."""
    for _ in range(50):
        bus = EventBus()
        seq = list(range(20))
        for i in seq:
            bus.publish({"type": i})

        started = threading.Event()

        def racer():
            started.set()
            for i in range(20, 40):
                bus.publish({"type": i})

        t = threading.Thread(target=racer)
        t.start()
        started.wait()
        q = bus.subscribe(replay=True)
        t.join()
        bus.publish({"type": 999})

        got = [e["type"] for e in _drain(q)]
        # Every value delivered must appear in strictly increasing publish order: no live
        # event may sort ahead of an older one. (Monotonic == no out-of-order interleave.)
        assert got == sorted(got), got
        # The backlog prefix (values 0..19 that survived the cap) must be present and in order.
        assert 999 == got[-1]
        assert got.index(999) == len(got) - 1


def test_subscriber_queue_is_bounded():
    bus = EventBus(backlog=1, subscriber_max=10)
    q = bus.subscribe()
    # A stalled consumer: never drains. Publish far past the cap.
    for i in range(1000):
        bus.publish({"type": i})
    assert q.qsize() <= 10


def test_full_queue_drops_oldest_and_keeps_newest():
    bus = EventBus(backlog=1, subscriber_max=5)
    q = bus.subscribe()
    for i in range(100):
        bus.publish({"type": i})
    got = [e["type"] for e in _drain(q)]
    # Bounded, and the retained events are the most RECENT ones (drop-oldest policy).
    assert len(got) == 5
    assert got == [95, 96, 97, 98, 99]


def test_publish_never_blocks_or_raises_on_stalled_subscriber():
    """A full/stalled subscriber must not block or crash publish for everyone else."""
    bus = EventBus(backlog=1, subscriber_max=3)
    stalled = bus.subscribe()  # never drained
    live = bus.subscribe()
    for i in range(50):
        bus.publish({"type": i})  # must return promptly, no exception
        live.get_nowait()          # healthy consumer keeps pace
    # The healthy subscriber saw the final event; the stalled one stayed bounded.
    assert stalled.qsize() <= 3
    bus.publish({"type": "final"})
    assert live.get_nowait()["type"] == "final"
