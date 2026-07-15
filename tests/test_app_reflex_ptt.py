"""Unit test: the reflex second-PTT fast path through App (issue #38) — offline, free (§9).

Proves the dispatch contract without a mic, a key, an executor, or the LLM:

  * a captured combat keyword ("chaff!") fires the reflex via ReflexCapability.fire_reflex — the
    #36 guard/executor path — and NEVER calls the LLM (that's the whole point: no round-trip);
  * a captured non-combat utterance FALLS THROUGH to the normal conversation turn
    (_dispatch_utterance), so the second PTT still works as an ordinary talk key;
  * an "abort" snap routes to the shared hard abort.

A recording fake reflex + injected FakeSTT/FakeLLM/FakeTTS keep it hermetic.
"""
from __future__ import annotations

import threading

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
        "keys": {"push_to_talk": "right ctrl"},
    }


class _RecReflex:
    """Stand-in for ReflexCapability that records fire_reflex calls (the #36 dispatch seam)."""

    def __init__(self) -> None:
        self.fired: list[str] = []

    def fire_reflex(self, name: str) -> str:
        self.fired.append(name)
        return "abort" if name == "abort" else f"{name.replace('_', ' ')} away"


def _app(tmp_path, *, transcript: str):
    llm = FakeLLM(text="conversation reply")
    app = App(_cfg(tmp_path), stt=FakeSTT(text=transcript), llm=llm, tts=FakeTTS())
    return app, llm


def test_spotted_keyword_fires_reflex_and_bypasses_the_llm(tmp_path):
    app, llm = _app(tmp_path, transcript="chaff!")
    app.reflex = _RecReflex()
    fell_through = {"n": 0}
    app._dispatch_utterance = lambda audio, **k: fell_through.__setitem__("n", fell_through["n"] + 1)

    app._process_reflex(object(), threading.Event())

    assert app.reflex.fired == ["chaff"]        # fired the reflex by name
    assert fell_through["n"] == 0               # did NOT fall through to a normal turn
    assert llm.model_seen is None               # LLM never called — the whole point
    assert app.tts.spoken == ["chaff away"]     # spoke the reflex result as feedback
    assert app.state == "Idle"


def test_abort_phrase_routes_to_hard_abort(tmp_path):
    app, _ = _app(tmp_path, transcript="abort abort!")
    app.reflex = _RecReflex()
    app._process_reflex(object(), threading.Event())
    assert app.reflex.fired == ["abort"]


def test_non_combat_utterance_falls_through_to_a_normal_turn(tmp_path):
    app, llm = _app(tmp_path, transcript="what's my fuel level?")
    app.reflex = _RecReflex()
    seen = {}
    app._dispatch_utterance = lambda audio, **k: seen.setdefault("audio", audio)

    app._process_reflex(object(), threading.Event())

    assert app.reflex.fired == []               # nothing fired
    assert "audio" in seen                       # handed the SAME capture to the normal turn
    assert llm.model_seen is None               # (the fake normal turn was stubbed; no direct call)


def test_falls_through_when_reflexes_disabled(tmp_path):
    """A combat keyword still falls through to a normal turn when reflexes aren't enabled
    (app.reflex is None) — the fast path degrades gracefully rather than swallowing the turn."""
    app, _ = _app(tmp_path, transcript="chaff!")
    assert app.reflex is None                    # no [reflex].enabled in _cfg
    seen = {"n": 0}
    app._dispatch_utterance = lambda audio, **k: seen.__setitem__("n", seen["n"] + 1)
    app._process_reflex(object(), threading.Event())
    assert seen["n"] == 1


def test_empty_capture_returns_to_idle_without_firing(tmp_path):
    app, _ = _app(tmp_path, transcript="")     # no speech detected
    app.reflex = _RecReflex()
    fell_through = {"n": 0}
    app._dispatch_utterance = lambda audio, **k: fell_through.__setitem__("n", fell_through["n"] + 1)
    app._process_reflex(object(), threading.Event())
    assert app.reflex.fired == [] and fell_through["n"] == 0
    assert app.state == "Idle"


def test_cancelled_capture_does_nothing(tmp_path):
    app, _ = _app(tmp_path, transcript="chaff!")
    app.reflex = _RecReflex()
    cancel = threading.Event()
    cancel.set()
    app._process_reflex(object(), cancel)
    assert app.reflex.fired == []
