"""Opt-in live journal/status tail (DESIGN §5, §9).

Marked `integration` + `local`: it reads the REAL Elite Dangerous journal directory on
this machine, so it's excluded from the default `pytest` run. It costs nothing (pure local
file I/O, no network/API) but needs the files to exist. Run deliberately:

    pytest -m "integration and local" tests/test_ed_live.py -s

It starts the real watchers for a couple of seconds and asserts they tail without error,
printing whatever context/events show up (rich only if ED is actually running).
"""
from __future__ import annotations

import time

import pytest

from covas.ed import EDContext, JournalWatcher, StatusWatcher, resolve_journal_dir, status_path
from covas.events import EventBus

pytestmark = [pytest.mark.integration, pytest.mark.local]


def test_live_tail_runs_without_error():
    jdir = resolve_journal_dir({})
    if not jdir.exists():
        pytest.skip(f"No Elite Dangerous journal directory at {jdir}")

    bus = EventBus()
    q = bus.subscribe()
    ctx = EDContext()
    errors: list[Exception] = []

    watchers = [
        JournalWatcher(jdir, bus, ctx, poll_interval=0.2, on_error=errors.append),
        StatusWatcher(status_path(jdir), bus, ctx, poll_interval=0.5,
                      on_error=errors.append),
    ]
    for w in watchers:
        w.start()
    try:
        time.sleep(2.0)                      # let them prime + tail briefly
    finally:
        for w in watchers:
            w.stop()
        for w in watchers:
            w.join(timeout=2.0)

    assert not errors, f"watcher errors: {errors}"

    events = []
    while not q.empty():
        e = q.get_nowait()
        if e.get("type") == "ed_event":
            events.append(e)

    print(f"\nJournal dir: {jdir}")
    print(f"Context: {ctx.summary()}")
    print(f"ed_events seen in 2s: {[e['event'] for e in events]}")
