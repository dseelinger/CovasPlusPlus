"""A tiny thread-safe pub/sub bus so the voice loop can stream events to the UI."""
from __future__ import annotations

import queue
import threading
import time


class EventBus:
    def __init__(self, backlog: int = 300, subscriber_max: int = 1000) -> None:
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._backlog: list[dict] = []
        self._max = backlog
        # Each subscriber gets a *bounded* queue so a stalled consumer (e.g. a `/ws`
        # client whose socket is under TCP backpressure — it blocks in ws.send() and
        # stops draining) can't grow its queue without limit and exhaust memory. The
        # cap sits comfortably above the backlog so a full backlog replay is never
        # truncated (see subscribe()); the headroom above that absorbs a burst of live
        # events while the consumer catches up.
        self._qmax = max(subscriber_max, 2 * backlog)

    def subscribe(self, replay: bool = True) -> queue.Queue:
        """Register a new subscriber queue. By default the recent backlog is replayed
        into it so a late-joining client (the web UI) sees history. Pass replay=False
        for a live-only consumer (e.g. the proactive event pump) that must not react to
        events published before it subscribed.

        The backlog snapshot is copied into the queue and the queue is registered as a
        subscriber **atomically under the lock**, so a live event published concurrently
        can never be delivered ahead of the older backlog it replays: publish() also
        holds the same lock to append to the backlog and snapshot subscribers, so the
        two serialize — either the racing event lands in the backlog before we copy it
        (replayed in order), or the queue is already registered before that event is
        delivered (it arrives after the replayed backlog). Either way, ordered."""
        q: queue.Queue = queue.Queue(maxsize=self._qmax)
        with self._lock:
            if replay:
                # Safe from Full: _qmax >= 2 * _max >= len(_backlog).
                for e in self._backlog:
                    q.put_nowait(e)
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict) -> dict:
        event = dict(event)
        event.setdefault("ts", time.time())
        with self._lock:
            self._backlog.append(event)
            if len(self._backlog) > self._max:
                self._backlog = self._backlog[-self._max:]
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                # A stalled subscriber has hit its cap. Drop its *oldest* buffered event
                # to make room for this one: on the live-status stream the freshest event
                # matters most, so we keep the newest and shed the stale tail rather than
                # block the whole publish loop. If the queue is being drained concurrently
                # the re-put may still race to Full; we then drop this event — publish
                # stays non-blocking and fail-soft either way.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass
            except Exception:  # noqa: BLE001 — a dead/odd subscriber must never break publish
                pass
        return event
