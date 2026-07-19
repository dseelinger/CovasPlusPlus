"""Unit tests: concurrency & lifecycle edge cases in covas/app.py (issue #156).

Offline and free (DESIGN §9) — injected fakes, no network/audio/API. These pin the four
fixes from the audit:

  1. dispatch_text claims the turn INSIDE the proactive lock, so a proactive callout can't
     slip its idle-claim into a gap and start a second, concurrent worker for the same turn.
  2. reset_setting swaps the config in place WITHOUT an empty-dict window, so the keyboard-hook
     thread reading self.cfg["keys"] never hits a KeyError.
  3. _start_event_pump is idempotent under a concurrent first-enable — one pump thread, one bus
     subscription, no double-dispatch.
  4. on_cancel takes the proactive lock like every other interrupt path, so a cancel racing a
     proactive-callout claim isn't lost.

Ordering is driven deterministically (barriers / blocking providers / lock-state spies), not by
sleeps, so there's no timing flakiness.
"""
from __future__ import annotations

import threading
from typing import Iterator

from covas import app as app_mod
from covas import tiering
from covas.app import App
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    return {
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "whisper": {"model": "small", "device": "cpu", "compute_type": "int8"},
        "keys": {"push_to_talk": "right ctrl", "tap_cancel_ms": 400},
    }


def _make_app(tmp_path, **kw) -> App:
    kw.setdefault("stt", FakeSTT())
    kw.setdefault("llm", FakeLLM())
    kw.setdefault("tts", FakeTTS())
    return App(_cfg(tmp_path), **kw)


# --- fix 1: dispatch_text claims the turn under the proactive lock --------------------

def test_dispatch_text_claims_turn_under_the_proactive_lock(tmp_path):
    """The turn-claim (_dispatch_text, which sets self.worker) must run while the proactive lock
    is HELD — mirroring on_ptt_down. Before the fix the lock was released after _interrupt and the
    claim ran unlocked, leaving a gap a proactive callout could claim."""
    app = _make_app(tmp_path)
    held: dict = {}
    orig = app._dispatch_text

    def spy(text):
        held["locked_during_claim"] = app._proactive_lock.locked()
        return orig(text)

    app._dispatch_text = spy  # type: ignore[method-assign]
    app.dispatch_text("plot a course")
    assert app.worker is not None
    app.worker.join(timeout=5)

    assert held.get("locked_during_claim") is True


class _BlockingLLM:
    """stream_reply blocks until released, so the worker stays alive across the assertion window —
    lets us prove a proactive claim is refused while a typed turn is genuinely in flight."""

    def __init__(self, release: threading.Event) -> None:
        self._release = release

    def stream_reply(self, messages, cancel, on_event, tool_handler=None, tools=None,
                     model=None, max_tokens=None) -> Iterator[tuple[str, str]]:
        self._release.wait(5)
        yield ("text", "ok")


def test_proactive_callout_refused_while_typed_turn_in_flight(tmp_path):
    """With a typed turn's worker alive, _speak_proactive must return False and NOT replace the
    worker — the typed turn keeps the floor, no double LLM call."""
    release = threading.Event()
    app = _make_app(tmp_path, llm=_BlockingLLM(release))
    app.tier_level = tiering.LEVELS["Full"]  # ensure proactive is permitted (exercise the guard)

    app.dispatch_text("what's next")
    try:
        assert app.worker is not None and app.worker.is_alive()
        first_worker = app.worker
        first_cancel = app.active_cancel

        started = app._speak_proactive("FSDJump", {"event": "FSDJump"})

        assert started is False                 # callout refused — a turn already holds the floor
        assert app.worker is first_worker       # no second worker was spun up
        assert app.active_cancel is first_cancel
    finally:
        release.set()
        if app.worker is not None:
            app.worker.join(timeout=5)


# --- fix 2: reset_setting swaps cfg with no empty-dict window -------------------------

def test_reset_setting_keeps_cfg_keys_readable_throughout(tmp_path, monkeypatch):
    """A concurrent hook-thread read of self.cfg["keys"] (as on_ptt_up does, unguarded) must never
    KeyError while reset_setting re-derives the config. The old cfg.clear()+update() exposed an
    empty-dict window; the in-place update+prune closes it."""
    app = _make_app(tmp_path)
    monkeypatch.setattr(app_mod, "save_overrides", lambda o: None)
    monkeypatch.setattr(app, "_after_settings_change", lambda before: None)

    def fake_load():
        # A fresh dict identity each call (like load_config) — "keys" always present, as config.toml
        # guarantees. A "transient" section that isn't in the base proves the stale-key prune path.
        return {
            "anthropic": {"model": "claude-haiku-4-5"},
            "audio": {"input_device": ""},
            "whisper": {"model": "small", "device": "cpu", "compute_type": "int8"},
            "keys": {"push_to_talk": "right ctrl", "tap_cancel_ms": 400},
        }

    # Seed a stale top-level section that fake_load() omits, so each reset must drop it (exercising
    # the delete-stale branch) — and the reader must survive that too.
    app.cfg["transient_section"] = {"x": 1}
    monkeypatch.setattr(app_mod, "load_config", fake_load)

    errors: list[Exception] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                _ = app.cfg["keys"]["push_to_talk"]   # the exact read on_ptt_up does on the hook thread
            except Exception as e:  # noqa: BLE001 — record any read failure (KeyError is the bug)
                errors.append(e)

    r = threading.Thread(target=reader, daemon=True)
    r.start()
    try:
        for _ in range(3000):
            app.reset_setting(["nonexistent", "override"])
    finally:
        stop.set()
        r.join(timeout=5)

    assert errors == [], f"cfg read failed during reset_setting: {errors[:3]}"
    assert "transient_section" not in app.cfg     # stale key pruned
    assert app.cfg["keys"]["push_to_talk"] == "right ctrl"


# --- fix 3: _start_event_pump is idempotent under concurrent first-enable -------------

def test_start_event_pump_idempotent_under_concurrent_enable(tmp_path):
    """Many callers (bootstrap, HUD/route/macro enable) can race the first enable. The guard makes
    the check-then-act atomic: exactly ONE subscription and ONE pump thread, never two (which would
    double-dispatch every event)."""
    app = _make_app(tmp_path)

    subs: list[int] = []
    subs_lock = threading.Lock()
    real_subscribe = app.bus.subscribe

    def counting_subscribe(*a, **k):
        with subs_lock:
            subs.append(1)
        return real_subscribe(*a, **k)

    app.bus.subscribe = counting_subscribe  # type: ignore[method-assign]

    n = 16
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()            # all threads pile into _start_event_pump at once
        app._start_event_pump()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    try:
        assert len(subs) == 1                       # subscribed exactly once
        assert app._pump is not None and app._pump.is_alive()
    finally:
        app._stop_event_pump()

    # A second call after start is still a no-op (the plain idempotency the callers rely on).
    app._start_event_pump()
    assert len(subs) == 1


# --- fix 4: on_cancel takes the proactive lock like every other interrupt path --------

def test_on_cancel_takes_the_proactive_lock(tmp_path):
    """on_cancel must hold the proactive lock across interrupt + the Idle flip, so a proactive
    claim mid-flight (past its Idle check, worker not yet started) can't have this cancel land in
    the gap and be lost."""
    app = _make_app(tmp_path)
    held: dict = {}
    orig = app._interrupt

    def spy():
        held["locked_during_interrupt"] = app._proactive_lock.locked()
        return orig()

    app._interrupt = spy  # type: ignore[method-assign]
    app.on_cancel()

    assert held.get("locked_during_interrupt") is True
    assert app.state == "Idle"
