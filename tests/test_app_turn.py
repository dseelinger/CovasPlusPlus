"""Unit test: drive App through a full voice turn with injected fakes (DESIGN §9).

No network, no audio hardware, no API. The provider seam lets us pass FakeSTT/
FakeLLM/FakeTTS straight into App(...), so a whole capture -> transcribe -> LLM ->
speak turn runs offline and free. Proves the composition-root wiring: the injected
providers are the ones actually used, and the capability registry's tools reach the
LLM call.
"""
from __future__ import annotations

import threading
from typing import Iterator, Optional

from covas.app import App
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path) -> dict:
    """Minimal config exercising the __init__ + _process paths. input_device="" so
    the Recorder never queries the audio subsystem; empty sound_cues so CuePlayer
    loads/plays nothing."""
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n- [ ] Jump to Sol\n", encoding="utf-8")
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


def _make_app(tmp_path, *, llm, tts, stt) -> App:
    return App(_cfg(tmp_path), llm=llm, tts=tts, stt=stt)


def test_full_turn_transcribes_streams_and_speaks(tmp_path):
    app = _make_app(
        tmp_path,
        stt=FakeSTT(text="what's next, COVAS?"),
        llm=FakeLLM(text="Scoop some fuel first, Commander."),
        tts=FakeTTS(),
    )
    # The injected fakes are the ones App actually uses.
    assert isinstance(app.stt, FakeSTT)
    assert isinstance(app.llm, FakeLLM)
    assert isinstance(app.tts, FakeTTS)

    app._process(object(), threading.Event())  # audio payload is ignored by FakeSTT

    assert app.tts.spoken == ["Scoop some fuel first, Commander."]
    assert app.history == [
        {"role": "user", "content": "what's next, COVAS?"},
        {"role": "assistant", "content": "Scoop some fuel first, Commander."},
    ]
    assert app.state == "Idle"


def test_cancel_before_speaking_skips_tts(tmp_path):
    app = _make_app(tmp_path, stt=FakeSTT(text="hello"),
                    llm=FakeLLM(text="hi"), tts=FakeTTS())
    cancel = threading.Event()
    cancel.set()  # already cancelled -> _process returns before doing anything
    app._process(object(), cancel)
    assert app.tts.spoken == []


class _CapturingLLM:
    """Records the tools + tool_handler App hands to the LLM, then yields text."""

    def __init__(self) -> None:
        self.tools_seen: Optional[list[dict]] = None
        self.handler = None

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None) -> Iterator[tuple[str, str]]:
        self.tools_seen = tools
        self.handler = tool_handler
        yield ("text", "ok")


def test_turn_offers_capability_tools_to_llm(tmp_path):
    cap = _CapturingLLM()
    app = _make_app(tmp_path, stt=FakeSTT(text="mark fuel done"),
                    llm=cap, tts=FakeTTS())
    app._process(object(), threading.Event())

    names = {t["name"] for t in (cap.tools_seen or [])}
    assert {"get_next_objectives", "find_objectives", "set_objective",
            "add_objective", "modify_objective", "delete_objective"} <= names
    # The handler App passed is the registry's dispatcher — invoking it runs a real
    # checklist tool end to end (bound methods aren't identity-comparable, so we
    # assert on behavior).
    assert callable(cap.handler)
    assert "Scoop fuel" in cap.handler("get_next_objectives", {})
