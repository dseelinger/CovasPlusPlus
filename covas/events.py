"""A tiny thread-safe pub/sub bus so the voice loop can stream events to the UI."""
from __future__ import annotations
import queue
import threading
import time


class EventBus:
    def __init__(self, backlog: int = 300) -> None:
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._backlog: list[dict] = []
        self._max = backlog

    def subscribe(self, replay: bool = True) -> queue.Queue:
        """Register a new subscriber queue. By default the recent backlog is replayed
        into it so a late-joining client (the web UI) sees history. Pass replay=False
        for a live-only consumer (e.g. the proactive event pump) that must not react to
        events published before it subscribed."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.add(q)
            backlog = list(self._backlog) if replay else []
        for e in backlog:          # replay recent history to a new client
            q.put(e)
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
            except Exception:  # noqa: BLE001
                pass
        return event
