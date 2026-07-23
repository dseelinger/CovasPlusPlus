"""Unit tests for the persona speech arbiter (issue #146). Offline + free.

Cover the policy (priority ordering, supersede/contravene/higher-priority preempt vs equal/lower
queue), freshness (TTL drop), barge-in flush, bounded-depth overflow, and the fail-soft error
capture — with a FAKE speaker and an injected clock, never any real TTS/audio. A couple of
app-level checks confirm `_speak` still routes a single reply through the arbiter and that the
PTT interrupt flushes it.
"""
from __future__ import annotations

import threading
import time

from covas.persona_speech import (
    DEFAULT_QUEUE_DEPTH,
    SAFETY_SUBJECTS,
    PersonaSpeechArbiter,
    Priority,
)

# ---- helpers -----------------------------------------------------------------------------

def _wait_until(pred, timeout: float = 2.0) -> bool:
    """Poll `pred` until true or timeout — keeps the thread-based tests deterministic."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


class FakeSpeaker:
    """A recording speaker. With `hold=True` each line BLOCKS (as a real TTS would while playing)
    until its cancel Event fires OR `release()` is called — so a test can hold a line "in progress"
    and observe a preempt cut it mid-word. Thread-safe records: `started` (pop order), `cancelled`
    (cut mid-line), `finished` (played to completion)."""

    def __init__(self, *, hold: bool = False) -> None:
        self.hold = hold
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.finished: list[str] = []
        self._release = threading.Event()
        self._lock = threading.Lock()

    def speak(self, text: str, cancel: threading.Event) -> None:
        with self._lock:
            self.started.append(text)
        if self.hold:
            while not cancel.is_set() and not self._release.is_set():
                time.sleep(0.002)
        with self._lock:
            (self.cancelled if cancel.is_set() else self.finished).append(text)

    def release(self) -> None:
        self._release.set()


class _Clock:
    """A manually-advanced monotonic clock for deterministic TTL tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ---- pure preempt policy (no thread) -----------------------------------------------------

def _line(priority, subject="", preempt=False):
    from covas.persona_speech import Line
    return Line(text="x", priority=int(priority), subject=subject, preempt=preempt)


def test_should_preempt_matrix():
    arb = PersonaSpeechArbiter(lambda *_: None)
    cur = _line(Priority.AMBIENT, subject="mood")
    # same subject supersedes even at equal priority
    assert arb._should_preempt(_line(Priority.AMBIENT, subject="mood"), cur) is True
    # higher priority preempts an unrelated line
    assert arb._should_preempt(_line(Priority.REPLY), cur) is True
    # explicit preempt flag forces a cut
    assert arb._should_preempt(_line(Priority.AMBIENT, preempt=True), cur) is True
    # a safety subject preempts regardless of priority
    danger = next(iter(SAFETY_SUBJECTS))
    assert arb._should_preempt(_line(Priority.AMBIENT, subject=danger), cur) is True
    # unrelated equal priority QUEUES (does not preempt)
    assert arb._should_preempt(_line(Priority.AMBIENT, subject="other"), cur) is False
    # unrelated lower priority QUEUES
    hi = _line(Priority.REPLY)
    assert arb._should_preempt(_line(Priority.AMBIENT), hi) is False


# ---- ordering ----------------------------------------------------------------------------

def test_lines_speak_in_priority_then_fifo_order():
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak)
    try:
        arb.enqueue("block", priority=Priority.REPLY)          # claims the speaker, holds
        assert _wait_until(lambda: "block" in spk.started)
        # Queue behind it (none preempts: all <= REPLY and unrelated).
        arb.enqueue("amb1", priority=Priority.AMBIENT)
        arb.enqueue("call", priority=Priority.CALLOUT)
        arb.enqueue("amb2", priority=Priority.AMBIENT)
        spk.release()
        assert _wait_until(lambda: len(spk.started) == 4)
        # After the reply: CALLOUT (20) before the two AMBIENT (10), which hold FIFO by enqueue.
        assert spk.started == ["block", "call", "amb1", "amb2"]
    finally:
        arb.stop()


# ---- preempt: cut the current line short -------------------------------------------------

def test_higher_priority_preempts_current_mid_line():
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak)
    try:
        musing = arb.enqueue("musing", priority=Priority.AMBIENT)
        assert _wait_until(lambda: "musing" in spk.started)
        arb.enqueue("answer", priority=Priority.REPLY)         # higher -> preempt
        assert _wait_until(lambda: "musing" in spk.cancelled)  # cut mid-word
        assert musing.cancel.is_set()
        assert _wait_until(lambda: "answer" in spk.started)
    finally:
        spk.release()
        arb.stop()


def test_same_subject_supersede_preempts_at_equal_priority():
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak)
    try:
        arb.enqueue("next star is Sol", priority=Priority.CALLOUT, subject="route")
        assert _wait_until(lambda: spk.started == ["next star is Sol"])
        # A fresher SAME-subject callout supersedes the one being read (equal priority!).
        arb.enqueue("reroute: next is Wolf 359", priority=Priority.CALLOUT, subject="route")
        assert _wait_until(lambda: "next star is Sol" in spk.cancelled)
        assert _wait_until(lambda: "reroute: next is Wolf 359" in spk.started)
    finally:
        spk.release()
        arb.stop()


def test_preempt_flag_contravenes_current_at_equal_priority():
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak)
    try:
        arb.enqueue("all clear out here", priority=Priority.AMBIENT)
        assert _wait_until(lambda: "all clear out here" in spk.started)
        # New game state makes the musing wrong -> producer forces a preempt (contravene).
        arb.enqueue("belay that — contact ahead", priority=Priority.AMBIENT, preempt=True)
        assert _wait_until(lambda: "all clear out here" in spk.cancelled)
        assert _wait_until(lambda: "belay that — contact ahead" in spk.started)
    finally:
        spk.release()
        arb.stop()


def test_unrelated_equal_priority_queues_not_preempts():
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak)
    try:
        arb.enqueue("callout one", priority=Priority.CALLOUT)
        assert _wait_until(lambda: "callout one" in spk.started)
        arb.enqueue("callout two", priority=Priority.CALLOUT)  # equal, unrelated -> QUEUE
        time.sleep(0.05)
        assert "callout one" not in spk.cancelled              # not chopped off
        assert arb.pending() == 1                              # waiting its turn
    finally:
        spk.release()
        arb.stop()


# ---- freshness / TTL ---------------------------------------------------------------------

def test_stale_ambient_line_is_dropped_not_spoken_late():
    clock = _Clock()
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak, clock=clock.now)
    try:
        arb.enqueue("block", priority=Priority.REPLY)          # holds the speaker
        assert _wait_until(lambda: "block" in spk.started)
        stale = arb.enqueue("nice system", priority=Priority.AMBIENT, ttl=5.0)
        clock.advance(10.0)                                    # it sat in the queue too long
        spk.release()                                          # block finishes -> pop the ambient
        assert _wait_until(lambda: stale.done.is_set())
        assert stale.dropped is True
        assert "nice system" not in spk.started                # never spoken late
    finally:
        arb.stop()


def test_fresh_ambient_within_ttl_still_speaks():
    clock = _Clock()
    spk = FakeSpeaker()
    arb = PersonaSpeechArbiter(spk.speak, clock=clock.now)
    try:
        line = arb.enqueue("still fresh", priority=Priority.AMBIENT, ttl=5.0)
        clock.advance(2.0)
        assert _wait_until(lambda: line.done.is_set())
        assert line.dropped is False and "still fresh" in spk.started
    finally:
        arb.stop()


# ---- barge-in flush ----------------------------------------------------------------------

def test_flush_cancels_current_and_drops_queue():
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak)
    try:
        arb.enqueue("speaking now", priority=Priority.REPLY)
        assert _wait_until(lambda: "speaking now" in spk.started)
        q1 = arb.enqueue("stale ambient 1", priority=Priority.AMBIENT)
        q2 = arb.enqueue("stale ambient 2", priority=Priority.AMBIENT)
        assert arb.pending() == 2
        arb.flush()                                            # PTT/user turn
        assert _wait_until(lambda: "speaking now" in spk.cancelled)
        assert q1.dropped and q2.dropped and arb.pending() == 0
        spk.release()
        time.sleep(0.05)
        # Nothing stale plays after the Commander spoke.
        assert "stale ambient 1" not in spk.started
        assert "stale ambient 2" not in spk.started
    finally:
        arb.stop()


# ---- bounded depth -----------------------------------------------------------------------

def test_overflow_drops_lowest_priority_and_logs():
    logs: list[str] = []
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak, max_depth=3, log=logs.append)
    try:
        arb.enqueue("block", priority=Priority.REPLY)          # in-progress, not counted in depth
        assert _wait_until(lambda: "block" in spk.started)
        a0 = arb.enqueue("amb0", priority=Priority.AMBIENT)
        arb.enqueue("amb1", priority=Priority.AMBIENT)
        arb.enqueue("amb2", priority=Priority.AMBIENT)
        assert arb.pending() == 3
        # One more overflows -> the OLDEST lowest-priority queued line is evicted.
        arb.enqueue("amb3", priority=Priority.AMBIENT)
        assert arb.pending() == 3                              # still capped
        assert a0.dropped is True
        assert any("queue full" in m for m in logs)
    finally:
        spk.release()
        arb.stop()


def test_overflow_evicts_ambient_not_a_higher_priority_newcomer():
    spk = FakeSpeaker(hold=True)
    arb = PersonaSpeechArbiter(spk.speak, max_depth=2)
    try:
        arb.enqueue("block", priority=Priority.REPLY)
        assert _wait_until(lambda: "block" in spk.started)
        amb_a = arb.enqueue("amb_a", priority=Priority.AMBIENT)
        amb_b = arb.enqueue("amb_b", priority=Priority.AMBIENT)
        assert arb.pending() == 2
        callout = arb.enqueue("callout", priority=Priority.CALLOUT)  # higher than the ambients
        assert arb.pending() == 2
        assert callout.dropped is False                        # the newcomer survives
        assert amb_a.dropped is True                           # the oldest ambient is evicted
        assert amb_b.dropped is False
    finally:
        spk.release()
        arb.stop()


# ---- fail-soft ---------------------------------------------------------------------------

def test_speak_error_is_captured_not_raised_on_the_thread():
    def boom(_text, _cancel):
        raise RuntimeError("dead TTS")

    logs: list[str] = []
    arb = PersonaSpeechArbiter(boom, log=logs.append)
    try:
        line = arb.enqueue("will fail", priority=Priority.REPLY)
        assert _wait_until(lambda: line.done.is_set())
        assert isinstance(line.error, RuntimeError)            # captured for a blocking caller
        try:
            line.raise_if_error()
        except RuntimeError:
            pass
        else:
            raise AssertionError("raise_if_error should re-raise the captured error")
        assert any("persona speak failed" in m for m in logs)
        # The speaker thread survived a bad line — a following line still speaks.
        spk_records: list[str] = []
        arb._default_speak = lambda t, _c: spk_records.append(t)
        arb.enqueue("recovered", priority=Priority.REPLY)
        assert _wait_until(lambda: spk_records == ["recovered"])
    finally:
        arb.stop()


def test_default_queue_depth_constant_matches_arbiter_default():
    arb = PersonaSpeechArbiter(lambda *_: None)
    assert arb._max_depth == DEFAULT_QUEUE_DEPTH


# ---- app wiring: no regression to a normal reply, and PTT flushes the arbiter ------------

def _app(tmp_path):
    from covas.app import App
    from tests.fakes import FakeLLM, FakeSTT, FakeTTS
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    cfg = {
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "keys": {"push_to_talk": "right ctrl"},
    }
    return App(cfg, stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())


def test_app_speak_routes_a_single_reply_through_the_arbiter(tmp_path):
    """A normal reply still speaks exactly once, in the persona voice, via the arbiter — the
    `_speak` call blocks until spoken, so `tts.spoken` is populated when it returns."""
    app = _app(tmp_path)
    try:
        app._speak("Scoop some fuel first, Commander.", threading.Event())
        assert app.tts.spoken == ["Scoop some fuel first, Commander."]
    finally:
        app.persona_arbiter.stop()


def test_app_speak_reraises_a_real_tts_failure(tmp_path):
    """The diagnosable-failure contract survives the arbiter hop: a configured-but-broken TTS
    still makes `_speak` raise so the caller degrades to text (issue #146 preserves #90/#108)."""
    app = _app(tmp_path)

    class _Boom:
        def speak(self, text, cancel):
            raise FileNotFoundError("no voice")

        def synth_pcm(self, text, voice_id=None):
            return b"", 16000

    app.tts = _Boom()
    try:
        app._speak("hi", threading.Event())
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("_speak should re-raise a real TTS failure through the arbiter")
    finally:
        app.persona_arbiter.stop()


def test_app_interrupt_flushes_the_persona_arbiter(tmp_path):
    """A PTT/user turn (`_interrupt`) must flush stale ambient queued on the arbiter so nothing
    plays after the Commander speaks."""
    app = _app(tmp_path)
    spk = FakeSpeaker(hold=True)
    app.persona_arbiter._default_speak = spk.speak
    try:
        app.persona_arbiter.enqueue("holding", priority=Priority.REPLY)
        assert _wait_until(lambda: "holding" in spk.started)
        q = app.persona_arbiter.enqueue("stale musing", priority=Priority.AMBIENT)
        assert app.persona_arbiter.pending() == 1
        app._interrupt()                                       # the barge-in path
        assert _wait_until(lambda: q.dropped)
        assert app.persona_arbiter.pending() == 0
    finally:
        spk.release()
        app.persona_arbiter.stop()
