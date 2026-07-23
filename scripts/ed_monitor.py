"""Manual live viewer for Elite Dangerous monitoring (DESIGN §5).

Starts the journal + status watchers against the real (or configured) journal directory
and prints ed_events + the rolling context as they change — a quick eyeball check that
tailing works on your machine. Pure local file I/O; no LLM, no TTS, no network.

    .venv\\Scripts\\python.exe scripts\\ed_monitor.py

Ctrl+C to stop. This does NOT run the voice loop and never speaks — it's a debug tool.
"""
from __future__ import annotations

import time

from covas.config import load_config
from covas.ed import EDContext, JournalWatcher, StatusWatcher, resolve_journal_dir, status_path
from covas.events import EventBus


def main() -> None:
    cfg = load_config()
    jdir = resolve_journal_dir(cfg)
    print(f"Watching: {jdir}")
    if not jdir.exists():
        print("  (directory does not exist — is Elite Dangerous installed / has it run?)")

    bus = EventBus()
    q = bus.subscribe()
    ctx = EDContext()

    def _err(e: Exception) -> None:
        print(f"  [watcher error] {e}")

    watchers = [
        JournalWatcher(jdir, bus, ctx, poll_interval=0.3, on_error=_err),
        StatusWatcher(status_path(jdir), bus, ctx, poll_interval=0.5, on_error=_err),
    ]
    for w in watchers:
        w.start()

    print("Listening for ED events (Ctrl+C to stop)...\n")
    last_summary = None
    try:
        while True:
            drained = False
            while not q.empty():
                e = q.get_nowait()
                if e.get("type") == "ed_event":
                    print(f"  event: {e['event']}")
                    drained = True
            summary = ctx.summary()
            if drained and summary and summary != last_summary:
                print(f"    -> {summary}")
                last_summary = summary
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        for w in watchers:
            w.stop()
        print("\nStopped. o7")


if __name__ == "__main__":
    main()
