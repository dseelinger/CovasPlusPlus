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


class _RecCues:
    """A stand-in for CuePlayer that records the loop lifecycle calls (issue #5)."""

    def __init__(self):
        self.events: list[tuple] = []

    def start_loop(self, name):
        self.events.append(("start", name))

    def stop_loop(self):
        self.events.append(("stop",))

    def play(self, name, wait=False):
        self.events.append(("play", name))

    def stop(self):
        self.events.append(("stop_all",))

    @property
    def starts(self):
        return [e for e in self.events if e[0] == "start"]

    @property
    def stops(self):
        return [e for e in self.events if e[0] == "stop"]


def _armed_app(tmp_path):
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    app.cues = _RecCues()
    return app


def test_thinking_bed_starts_on_working_state_when_armed(tmp_path):
    app = _armed_app(tmp_path)
    app._bed_armed = True
    app.set_state("Transcribing")
    assert app.cues.starts == [("start", "thinking")]   # armed + working -> bed on


def test_thinking_bed_not_started_when_unarmed(tmp_path):
    """A proactive-style Thinking (no PTT turn) never starts the bed."""
    app = _armed_app(tmp_path)
    app._bed_armed = False
    app.set_state("Thinking")
    assert app.cues.starts == []


def test_thinking_bed_stops_and_disarms_on_speaking(tmp_path):
    app = _armed_app(tmp_path)
    app._bed_armed = True
    app.set_state("Thinking")                           # started
    app.set_state("Speaking")                           # reply begins -> stop + disarm
    assert app.cues.stops and app._bed_armed is False


def test_thinking_bed_stops_on_cancel_to_idle(tmp_path):
    app = _armed_app(tmp_path)
    app._bed_armed = True
    app.set_state("Thinking")
    app.set_state("Idle", "cancelled")                  # cancel/failure path
    assert app.cues.stops and app._bed_armed is False


def test_thinking_bed_disabled_by_config(tmp_path):
    app = _armed_app(tmp_path)
    app.cfg["audio"]["thinking_bed"] = False            # toggle off -> just the one-shot tick
    app._bed_armed = True
    app.set_state("Thinking")
    assert app.cues.starts == []


def test_thinking_bed_full_turn_lifecycle(tmp_path):
    """A full PTT turn: armed -> bed starts during work -> stops by the time we're Idle again."""
    app = _make_app(tmp_path, stt=FakeSTT(text="hi"),
                    llm=FakeLLM(text="Hello, Commander."), tts=FakeTTS())
    app.cues = _RecCues()
    app._bed_armed = True                               # on_ptt_up arms this before the worker
    app._process(object(), threading.Event())
    kinds = [e[0] for e in app.cues.events]
    assert "start" in kinds and "stop" in kinds         # bed ran, then was torn down
    assert app._bed_armed is False and app.state == "Idle"


def test_quit_signal_wiring(tmp_path):
    """Ctrl+Alt+Q -> request_quit() sets the event; wait_for_quit() unblocks on it and
    shutdown() cleans up without raising. The web UI relies on this bridge because Flask
    blocks the main thread instead of calling run() (see run_covas_ui.py)."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    assert not app._quit.is_set()
    app.request_quit()
    assert app._quit.is_set()
    app.wait_for_quit()          # returns immediately once set (would hang if unwired)
    app.shutdown()               # no watchers running -> closes the log, no error


class _RaisingTTS:
    """A TTS whose speak() always raises — stands in for the missing-key FileNotFoundError."""

    def speak(self, text, cancel):  # noqa: ANN001
        raise FileNotFoundError("ElevenLabsAPIKey.txt")


def test_text_only_mode_skips_tts_quietly(tmp_path):
    """In text-only mode (no ElevenLabs key), _speak must NOT invoke the TTS or raise — the
    reply is already shown as text. Guards against the loud per-turn TTS FAILED regression."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=_RaisingTTS())
    app.text_only = True                       # simulate the keyless-ElevenLabs decision
    app._speak("Clear as a bell, Commander.", threading.Event())  # must not raise


def test_speak_still_raises_when_not_text_only(tmp_path):
    """The loud diagnosable path stays intact for a CONFIGURED-but-broken TTS."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=_RaisingTTS())
    assert app.text_only is False              # injected provider -> not text-only
    try:
        app._speak("hi", threading.Event())
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("_speak should re-raise a real TTS failure")


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
                     tool_handler=None, tools=None,
                     model=None, max_tokens=None) -> Iterator[tuple[str, str]]:
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


def test_router_choice_reaches_the_llm(tmp_path):
    """With routing ON, the per-turn model/max_tokens the router picks are the ones
    App hands to the provider (DESIGN §4). A wake phrase escalates to Sonnet."""
    cfg = _cfg(tmp_path)
    cfg["router"] = {"enabled": True, "default_model": "claude-haiku-4-5",
                     "escalate_model": "claude-sonnet-5"}
    llm = FakeLLM(text="ok")
    app = App(cfg, stt=FakeSTT(text="think hard about the jump plan"),
              llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event())
    assert llm.model_seen == "claude-sonnet-5"
    assert llm.max_tokens_seen == 1024


def test_control_phrase_is_stripped_from_what_the_model_sees(tmp_path):
    """A spoken 'use opus' routes the turn to Opus but is scrubbed from the message the
    model receives, so it answers the real question instead of pushing back on a model
    switch it can't make. The raw utterance is still what gets logged as Commander."""
    cfg = _cfg(tmp_path)
    cfg["router"] = {"enabled": True, "default_model": "claude-haiku-4-5",
                     "premium_model": "claude-opus-4-8"}
    llm = FakeLLM(text="ok")
    app = App(cfg, stt=FakeSTT(text="Use opus for this. What's the best weapon?"),
              llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event())
    assert llm.model_seen == "claude-opus-4-8"                  # routed on the raw text
    assert app.history[0] == {"role": "user", "content": "What's the best weapon?"}


def test_router_off_pins_the_fixed_anthropic_model(tmp_path):
    """No [router] section -> routing off -> every turn uses [anthropic].model."""
    llm = FakeLLM(text="ok")
    app = App(_cfg(tmp_path), stt=FakeSTT(text="think hard"), llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event())
    assert llm.model_seen == "claude-haiku-4-5"  # the fixed [anthropic].model in _cfg


def test_ed_monitoring_off_by_default(tmp_path):
    """No [elite] section -> no watchers, no ED-context capability, no where_am_i tool."""
    app = App(_cfg(tmp_path), stt=FakeSTT(text="hi"), llm=FakeLLM(text="ok"), tts=FakeTTS())
    assert app.ed_ctx is None and app._ed_watchers == []
    assert "where_am_i" not in {t["name"] for t in app.registry.tools()}


def _elite_cfg(tmp_path) -> dict:
    cfg = _cfg(tmp_path)
    cfg["elite"] = {"enabled": True, "journal_dir": str(tmp_path),
                    "journal_poll_interval": 0.05, "status_poll_interval": 0.05}
    return cfg


def test_ed_monitoring_wires_up_when_enabled(tmp_path):
    """[elite].enabled -> App builds the shared context, registers the ED-context
    capability (its read tools reach the LLM), and starts the two watcher threads
    against the configured journal dir. Watchers are daemons; stop() cleans them up."""
    app = App(_elite_cfg(tmp_path), stt=FakeSTT(text="hi"),
              llm=FakeLLM(text="ok"), tts=FakeTTS())
    try:
        assert app.ed_ctx is not None
        assert len(app._ed_watchers) == 2
        assert all(w.is_alive() for w in app._ed_watchers)
        assert {"where_am_i", "ship_status", "recent_events"} <= \
            {t["name"] for t in app.registry.tools()}
    finally:
        app._stop_ed_monitoring()


class _MsgCapturingLLM:
    """Records the content of the last user message App hands the LLM."""

    def __init__(self) -> None:
        self.last_user: Optional[str] = None

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None,
                     model=None, max_tokens=None) -> Iterator[tuple[str, str]]:
        self.last_user = messages[-1]["content"]
        yield ("text", "ok")


def test_ed_context_injected_on_matching_turn(tmp_path):
    """A turn referencing game state gets the live telemetry block prepended to what the
    LLM sees — while stored history keeps the clean question (no stale context lingering)."""
    llm = _MsgCapturingLLM()
    app = App(_elite_cfg(tmp_path), stt=FakeSTT(text="where am I docked?"),
              llm=llm, tts=FakeTTS())
    try:
        app.ed_ctx.update(system="Sol", docked=True, station="Abraham Lincoln")
        app._process(object(), threading.Event())
    finally:
        app._stop_ed_monitoring()
    assert "Live game telemetry" in llm.last_user and "Abraham Lincoln" in llm.last_user
    assert app.history[0] == {"role": "user", "content": "where am I docked?"}


def test_ed_context_not_injected_on_plain_turn(tmp_path):
    """An off-topic turn is left completely alone — no telemetry, no extra tokens."""
    llm = _MsgCapturingLLM()
    app = App(_elite_cfg(tmp_path), stt=FakeSTT(text="tell me a joke, COVAS"),
              llm=llm, tts=FakeTTS())
    try:
        app.ed_ctx.update(system="Sol")
        app._process(object(), threading.Event())
    finally:
        app._stop_ed_monitoring()
    assert llm.last_user == "tell me a joke, COVAS"


def test_ed_context_wake_word_stripped_and_log_injected(tmp_path):
    """The 'context' wake word forces a lookup, injects the recent-events log, and is
    scrubbed from both the model's input and stored history."""
    llm = _MsgCapturingLLM()
    app = App(_elite_cfg(tmp_path), stt=FakeSTT(text="context what have I been up to?"),
              llm=llm, tts=FakeTTS())
    try:
        app.ed_ctx.record("FSDJump", "Jumped to Sol", "2026-07-08T12:05:00Z")
        app._process(object(), threading.Event())
    finally:
        app._stop_ed_monitoring()
    assert "Jumped to Sol" in llm.last_user                 # recent-events log injected
    assert app.history[0]["content"] == "what have I been up to?"   # wake word scrubbed
