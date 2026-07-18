"""Unit test: drive App through a full voice turn with injected fakes (DESIGN §9).

No network, no audio hardware, no API. The provider seam lets us pass FakeSTT/
FakeLLM/FakeTTS straight into App(...), so a whole capture -> transcribe -> LLM ->
speak turn runs offline and free. Proves the composition-root wiring: the injected
providers are the ones actually used, and the capability registry's tools reach the
LLM call.
"""
from __future__ import annotations

import threading
import time
from typing import Iterator, Optional

from covas.app import App
from covas.providers._retry import ProviderError
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


# --- memory recall (#61): cache-safe injection into the per-turn user message ----------

class _MsgListCapturingLLM:
    """Records the FULL messages list App hands the LLM, so a test can prove the injection
    is isolated to the per-turn tail (the cacheable prefix — system + prior history — is
    untouched)."""

    def __init__(self) -> None:
        self.messages: Optional[list] = None

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None,
                     model=None, max_tokens=None) -> Iterator[tuple[str, str]]:
        self.messages = [dict(m) for m in messages]     # snapshot (App may mutate history after)
        yield ("text", "ok")


def _memory_cfg(tmp_path) -> dict:
    cfg = _cfg(tmp_path)
    cfg["memory"] = {"enabled": True, "dir": str(tmp_path / "mem"), "cap": 500}
    return cfg


def _seed(app, text: str, *, type: str = "preference", tags=()) -> None:
    """Store a durable fact directly via the capture sink (the real store the app recalls from)."""
    app.memory.capture.remember(text, type=type, tags=list(tags))


def test_memory_recall_injected_on_matching_turn(tmp_path):
    """A turn that reaches into the past gets the relevant fact prepended to what the LLM sees."""
    llm = _MsgCapturingLLM()
    app = App(_memory_cfg(tmp_path), stt=FakeSTT(text="do you remember my main ship?"),
              llm=llm, tts=FakeTTS())
    _seed(app, "Main ship is a Krait Mk II", tags=["ship"])
    app._process(object(), threading.Event())
    assert "Krait Mk II" in llm.last_user
    assert "Remembered about the Commander" in llm.last_user


def test_memory_recall_not_injected_on_plain_turn(tmp_path):
    """An off-topic turn is left completely alone — no memory block, no extra tokens."""
    llm = _MsgCapturingLLM()
    app = App(_memory_cfg(tmp_path), stt=FakeSTT(text="tell me a joke, COVAS"),
              llm=llm, tts=FakeTTS())
    _seed(app, "Main ship is a Krait Mk II", tags=["ship"])
    app._process(object(), threading.Event())
    assert llm.last_user == "tell me a joke, COVAS"


def test_memory_recall_miss_injects_nothing(tmp_path):
    """A recall-referencing turn with no matching fact injects nothing (fail-soft miss)."""
    llm = _MsgCapturingLLM()
    app = App(_memory_cfg(tmp_path), stt=FakeSTT(text="do you remember my favourite music?"),
              llm=llm, tts=FakeTTS())
    _seed(app, "Main ship is a Krait Mk II", tags=["ship"])
    app._process(object(), threading.Event())
    assert llm.last_user == "do you remember my favourite music?"


def test_memory_recall_is_cache_safe(tmp_path):
    """CACHE-SAFETY (DoD #61): the recall block rides the per-turn USER message ONLY, never the
    cacheable prefix. Proven across TWO recall turns:
      * turn 1's block appears only in the per-turn TAIL the model saw;
      * stored `history` keeps the CLEAN user text (no block) — so nothing lingers;
      * on turn 2, everything the model sees BEFORE its own tail (turn-1's user + assistant, the
        cacheable prefix) is CLEAN — the block from turn 1 never entered the prompt-cache prefix.
    The system prompt is built separately in llm.py and this path never touches it."""
    llm = _MsgListCapturingLLM()
    app = App(_memory_cfg(tmp_path), stt=FakeSTT(text="do you remember my main ship?"),
              llm=llm, tts=FakeTTS())
    _seed(app, "Main ship is a Krait Mk II", tags=["ship"])

    # -- turn 1 --
    app._process(object(), threading.Event())
    turn1 = llm.messages
    assert "Krait Mk II" in turn1[-1]["content"]                 # block in the per-turn tail
    assert app.history[0] == {"role": "user", "content": "do you remember my main ship?"}
    assert "Krait Mk II" not in app.history[0]["content"]        # clean text stored, nothing lingers

    # -- turn 2 --
    app.stt = FakeSTT(text="and do you remember my callsign?")
    app._process(object(), threading.Event())
    turn2 = llm.messages
    # Everything before turn 2's own (augmented) tail is the cacheable prefix — it must be clean.
    assert all("Remembered about the Commander" not in m["content"] for m in turn2[:-1])
    assert turn2[:-1] == app.history[:-2]                        # prefix == clean stored history


def test_memory_recall_off_when_disabled(tmp_path):
    """No [memory] section -> no recall capability, and a recall phrase injects nothing."""
    llm = _MsgCapturingLLM()
    app = App(_cfg(tmp_path), stt=FakeSTT(text="do you remember my main ship?"),
              llm=llm, tts=FakeTTS())
    assert app.memory is None
    app._process(object(), threading.Event())
    assert llm.last_user == "do you remember my main ship?"


# --- wake-word gate (#64): gates the CONTINUOUS path only; PTT bypasses it -------------

def _wake_cfg(tmp_path, phrase: str = "COVAS") -> dict:
    cfg = _cfg(tmp_path)
    cfg["listen"] = {"wake_word": phrase}
    return cfg


def test_continuous_utterance_without_wake_word_is_dropped(tmp_path):
    """A hands-free (wake_gated) capture that lacks the wake word never reaches the LLM — no
    turn, no history, back to Idle. This is the whole point of the gate."""
    llm = FakeLLM(text="ok")
    app = App(_wake_cfg(tmp_path), stt=FakeSTT(text="what's my fuel?"),
              llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event(), wake_gated=True)
    assert llm.model_seen is None            # LLM never called
    assert app.history == []                 # nothing stored
    assert app.tts.spoken == []              # nothing spoken
    assert app.state == "Idle"


def test_continuous_utterance_with_wake_word_runs_and_strips_it(tmp_path):
    """When the capture carries the wake word, the turn runs and the wake word is stripped
    from what the model sees / what's stored."""
    llm = FakeLLM(text="Half a tank, Commander.")
    app = App(_wake_cfg(tmp_path), stt=FakeSTT(text="COVAS, what's my fuel?"),
              llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event(), wake_gated=True)
    assert llm.model_seen is not None        # LLM was called
    assert app.history[0] == {"role": "user", "content": "what's my fuel?"}
    assert app.tts.spoken == ["Half a tank, Commander."]


def test_ptt_utterance_bypasses_the_wake_gate(tmp_path):
    """A deliberate PTT turn (the _process default, wake_gated=False) runs even without the
    wake word — PTT is never gated, so hands-on always works regardless of continuous config."""
    llm = FakeLLM(text="ok")
    app = App(_wake_cfg(tmp_path), stt=FakeSTT(text="what's my fuel?"),
              llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event())   # PTT path: wake_gated defaults False
    assert llm.model_seen is not None            # ran despite no wake word
    assert app.history[0] == {"role": "user", "content": "what's my fuel?"}


def test_continuous_wake_word_only_capture_is_dropped(tmp_path):
    """A capture that is JUST the wake word has no command — drop it (armed but empty)."""
    llm = FakeLLM(text="ok")
    app = App(_wake_cfg(tmp_path), stt=FakeSTT(text="COVAS."),
              llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event(), wake_gated=True)
    assert llm.model_seen is None
    assert app.history == []
    assert app.state == "Idle"


def test_continuous_unaffected_when_wake_word_disabled(tmp_path):
    """Empty wake_word (the default) -> the gate is off and continuous mode behaves exactly as
    issue #63 shipped: any capture runs, text unchanged."""
    llm = FakeLLM(text="ok")
    cfg = _cfg(tmp_path)
    cfg["listen"] = {"wake_word": ""}
    app = App(cfg, stt=FakeSTT(text="what's my fuel?"), llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event(), wake_gated=True)
    assert llm.model_seen is not None
    assert app.history[0] == {"role": "user", "content": "what's my fuel?"}


def test_vad_utterance_dispatches_wake_gated(tmp_path):
    """The continuous callback marks its dispatch wake_gated=True (the wiring that makes the
    gate apply to hands-free captures); a helper records the flag passed to _dispatch_utterance."""
    app = App(_wake_cfg(tmp_path), stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    seen = {}

    def _capture(audio, *, wake_gated=False):
        seen["wake_gated"] = wake_gated

    app._dispatch_utterance = _capture  # type: ignore[method-assign]
    app.ptt_held = False
    app._on_vad_utterance(object())
    assert seen == {"wake_gated": True}


# --- history integrity (feature-01 remediation) ---------------------------------------
# A turn that CANCELS, ERRORS, or comes back EMPTY must leave NO orphaned user turn in
# self.history. An orphan (a user message with no assistant reply) poisons the next call:
# Anthropic 400s ("messages … must have non-empty content") and the model answers the STALE
# question. We commit the user+assistant PAIR only after a successful reply.

class _RaisingLLM:
    """stream_reply raises mid-stream — stands in for the Anthropic 400 we saw in testing."""

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None, model=None, max_tokens=None):
        raise RuntimeError("400 - user messages must have non-empty content")
        yield ("text", "")  # unreachable — keeps this a generator, like the real provider


class _CancelMidStreamLLM:
    """Sets the cancel event as the reply begins — a barge-in / tap-cancel mid-turn."""

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None, model=None, max_tokens=None):
        cancel.set()
        yield ("text", "half a sen")


def test_error_midturn_leaves_history_clean(tmp_path):
    app = _make_app(tmp_path, stt=FakeSTT(text="what's the latest ED update?"),
                    llm=_RaisingLLM(), tts=FakeTTS())
    app._process(object(), threading.Event())
    assert app.history == []            # no orphaned user turn survives the error
    assert app.tts.spoken == []
    assert app.state == "Idle"


def test_cancel_midturn_leaves_history_clean(tmp_path):
    app = _make_app(tmp_path, stt=FakeSTT(text="tell me a long story"),
                    llm=_CancelMidStreamLLM(), tts=FakeTTS())
    app._process(object(), threading.Event())
    assert app.history == []            # barge-in/cancel drops the turn cleanly
    assert app.tts.spoken == []


def test_empty_reply_leaves_history_clean(tmp_path):
    app = _make_app(tmp_path, stt=FakeSTT(text="hello?"),
                    llm=FakeLLM(text="   "), tts=FakeTTS())
    app._process(object(), threading.Event())
    assert app.history == []
    assert app.tts.spoken == []


def test_whitespace_transcription_drops_turn_without_calling_llm(tmp_path):
    """A near-silence / whitespace-only capture (e.g. a barge-in that caught almost nothing)
    must NOT reach the LLM or history — sending an empty/whitespace user turn 400s the API
    ("messages must have non-empty content") and poisons later turns."""
    llm = FakeLLM(text="ok")
    app = _make_app(tmp_path, stt=FakeSTT(text="   \n  "), llm=llm, tts=FakeTTS())
    app._process(object(), threading.Event())
    assert llm.model_seen is None       # LLM never called on an empty capture
    assert app.history == []            # nothing stored
    assert app.tts.spoken == []
    assert app.state == "Idle"


def test_history_not_poisoned_across_error_then_success(tmp_path):
    """The stale-question BLEED regression: after a turn errors, the next turn answers ITS OWN
    question — the errored turn left no orphan for the model to respond to."""
    app = _make_app(tmp_path, stt=FakeSTT(text="what's the latest ED update?"),
                    llm=_RaisingLLM(), tts=FakeTTS())
    app._process(object(), threading.Event())              # turn 1 errors
    assert app.history == []

    app.llm = FakeLLM(text="On foot? Different department, Commander.")
    app.stt = FakeSTT(text="give me the on-foot engineering breakdown")
    app._process(object(), threading.Event())              # turn 2 succeeds
    assert app.history == [
        {"role": "user", "content": "give me the on-foot engineering breakdown"},
        {"role": "assistant", "content": "On foot? Different department, Commander."},
    ]


# --- typed prompt from the control panel (issue #76) ----------------------------------
# A typed prompt runs a FULL normal turn (router, context, tools, history, spoken reply) —
# identical to a spoken turn, just skipping STT. The shared spine is _run_turn; dispatch_text
# is the public entry the web /api/prompt route calls.

def test_typed_prompt_runs_full_turn_end_to_end(tmp_path):
    """A typed prompt streams the LLM, speaks the reply, and commits the user+assistant pair —
    exactly like a spoken turn, without STT. Logged as Commander (the raw text is what's stored)."""
    llm = FakeLLM(text="Plotting a course now, Commander.")
    app = _make_app(tmp_path, stt=FakeSTT(), llm=llm, tts=FakeTTS())
    app._run_turn("plot a route to Sol", threading.Event())
    assert llm.model_seen is not None                       # the LLM actually ran
    assert app.tts.spoken == ["Plotting a course now, Commander."]
    assert app.history == [
        {"role": "user", "content": "plot a route to Sol"},
        {"role": "assistant", "content": "Plotting a course now, Commander."},
    ]
    assert app.state == "Idle"


def test_typed_prompt_routing_and_control_strip_parity(tmp_path):
    """Parity with the spoken path: a typed control phrase ('use opus') routes the turn AND is
    scrubbed from what the model sees / what's stored — the same _run_turn code drives both."""
    cfg = _cfg(tmp_path)
    cfg["router"] = {"enabled": True, "default_model": "claude-haiku-4-5",
                     "premium_model": "claude-opus-4-8"}
    llm = FakeLLM(text="ok")
    app = App(cfg, stt=FakeSTT(), llm=llm, tts=FakeTTS())
    app._run_turn("Use opus for this. What's the best weapon?", threading.Event())
    assert llm.model_seen == "claude-opus-4-8"
    assert app.history[0] == {"role": "user", "content": "What's the best weapon?"}


def test_dispatch_text_empty_or_whitespace_is_dropped(tmp_path):
    """Empty/whitespace typed input never spawns a turn — no worker, no history, no API call
    (an empty user turn 400s the API), mirroring the transcription guard."""
    llm = FakeLLM(text="should not run")
    app = _make_app(tmp_path, stt=FakeSTT(), llm=llm, tts=FakeTTS())
    app.dispatch_text("   \n\t ")
    assert app.worker is None                               # nothing dispatched
    assert app.history == [] and llm.model_seen is None and app.tts.spoken == []


def test_dispatch_text_runs_turn_on_worker_and_strips(tmp_path):
    """The public entry spawns the worker, trims surrounding whitespace, and runs the full turn."""
    llm = FakeLLM(text="Aye, Commander.")
    app = _make_app(tmp_path, stt=FakeSTT(), llm=llm, tts=FakeTTS())
    app.dispatch_text("  hello COVAS  ")
    assert app.worker is not None
    app.worker.join(timeout=5)
    assert app.history[0] == {"role": "user", "content": "hello COVAS"}   # stripped
    assert app.tts.spoken == ["Aye, Commander."]


# --- transient-provider degradation (issue #97) ---------------------------------------

class _DegradedLLM:
    """stream_reply raises a ProviderError(retryable=True) — a provider whose in-provider retries
    were exhausted by a sustained overload (e.g. Anthropic 529)."""

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None, model=None, max_tokens=None):
        raise ProviderError("Overloaded", provider="Anthropic", status=529,
                            retryable=True, attempts=4)
        yield ("text", "")  # unreachable — keeps this a generator like the real provider


def test_exhausted_retries_speak_named_degraded_line_no_orphan(tmp_path):
    """On exhausted retries the Commander hears a short, in-character, PROVIDER-NAMED line (not a
    raw error), the turn returns to Idle, and history is left clean (no orphaned user turn)."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_DegradedLLM(), tts=FakeTTS())
    app._run_turn("what's the latest ED news?", threading.Event())
    assert app.history == []                                # atomic commit never happened
    assert app.state == "Idle"
    assert app.tts.spoken, "a degraded provider must still say something"
    said = app.tts.spoken[0].lower()
    assert "overloaded" in said and "claude" in said       # provider named (anthropic default)


def test_non_transient_error_does_not_speak_degraded_line(tmp_path):
    """A plain (non-transient) failure falls soft WITHOUT the 'overloaded' line — that line is
    reserved for genuine provider degradation."""
    app = _make_app(tmp_path, stt=FakeSTT(text="hi"), llm=_RaisingLLM(), tts=FakeTTS())
    app._process(object(), threading.Event())
    assert app.history == [] and app.tts.spoken == []      # errored, nothing spoken
    assert app.state == "Idle"


def test_degraded_line_degrades_to_log_in_text_only(tmp_path):
    """Fail-soft: with no TTS (text-only) the degraded message must not be spoken or crash — it
    degrades to a logged line."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_DegradedLLM(), tts=FakeTTS())
    app.text_only = True
    app._run_turn("what's the latest ED news?", threading.Event())  # must not raise
    assert app.tts.spoken == []                            # nothing voiced in text-only
    assert app.state == "Idle" and app.history == []


# --- LLM misconfiguration voice (issue #108) -------------------------------------------
# A NON-transient, user-fixable failure (bad model/key/endpoint) is the one silence-is-worst case:
# the app is useless without an LLM and the fix is entirely the Commander's. Distinct from #97's
# transient/"overloaded" branch above — retrying a bad key or model just wastes time, so these fail
# fast (ProviderError(retryable=False)) and speak a different, "check your settings" line instead.

class _MisconfigLLM:
    """stream_reply raises a fail-fast ProviderError — a bad model id (404), matching what the raw
    providers now raise from their non-retryable connect branch (issue #108)."""

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None, model=None, max_tokens=None):
        raise ProviderError("Gemini LLM 404: model not found", provider="Gemini",
                            status=404, retryable=False)
        yield ("text", "")  # unreachable — keeps this a generator like the real provider


class _BadKeyLLM:
    """stream_reply raises a fail-fast 401 — a wrong/missing API key."""

    def stream_reply(self, messages, cancel, on_event,
                     tool_handler=None, tools=None, model=None, max_tokens=None):
        raise ProviderError("OpenAI LLM 401: invalid_api_key", provider="OpenAI",
                            status=401, retryable=False)
        yield ("text", "")  # unreachable


def test_config_error_speaks_named_misconfig_line_no_orphan(tmp_path):
    """On a classified misconfiguration the Commander hears a short, provider-named 'check your
    settings' line (not a raw error), the turn returns to Idle, and history is left clean."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_MisconfigLLM(), tts=FakeTTS())
    app._run_turn("what's the latest ED news?", threading.Event())
    assert app.history == []                                # atomic commit never happened
    assert app.state == "Idle"
    assert app.tts.spoken, "a misconfigured provider must still say something"
    said = app.tts.spoken[0].lower()
    # Provider name comes from the ACTIVE config (like _speak_degraded), not the error object —
    # this app's default config is the Anthropic provider ("Claude").
    assert "claude" in said and "settings" in said           # provider named, points at settings
    assert "model" in said                                   # 404 -> the model-id hint


def test_config_error_401_names_the_key_as_the_fix(tmp_path):
    """A 401/403 status names the API KEY as the likely fix, not the model."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_BadKeyLLM(), tts=FakeTTS())
    app._run_turn("what's the latest ED news?", threading.Event())
    said = app.tts.spoken[0].lower()
    assert "key" in said


def test_config_error_fires_every_failed_turn_no_rate_limit(tmp_path):
    """Per the design decision, the misconfig line is NOT rate-limited — each failed turn was a
    deliberate PTT that got no answer, so it speaks again next time too."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_MisconfigLLM(), tts=FakeTTS())
    app._run_turn("first question", threading.Event())
    app._run_turn("second question", threading.Event())
    assert len(app.tts.spoken) == 2


def test_config_error_does_not_speak_degraded_overloaded_line(tmp_path):
    """The two spoken branches are mutually exclusive: a config error must NOT say 'overloaded'."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_MisconfigLLM(), tts=FakeTTS())
    app._run_turn("what's the latest ED news?", threading.Event())
    assert "overloaded" not in app.tts.spoken[0].lower()


def test_transient_error_does_not_speak_misconfig_line(tmp_path):
    """The reverse: a transient/exhausted-retry error stays on the #97 'overloaded' branch and must
    NOT say 'settings' — the two branches never cross."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_DegradedLLM(), tts=FakeTTS())
    app._run_turn("what's the latest ED news?", threading.Event())
    assert "settings" not in app.tts.spoken[0].lower()


def test_unknown_error_does_not_speak_misconfig_line(tmp_path):
    """An unclassified exception (no status at all — a tool bug, a code crash) keeps the OLD silent
    cue+log behavior: misdiagnosing a bug as 'check your settings' would send the Commander on a
    wild goose chase."""
    app = _make_app(tmp_path, stt=FakeSTT(text="hi"), llm=_RaisingLLM(), tts=FakeTTS())
    app._process(object(), threading.Event())
    assert app.tts.spoken == []
    assert app.state == "Idle"


def test_config_error_degrades_to_log_in_text_only(tmp_path):
    """Fail-soft: with no TTS (text-only) the misconfig message must not be spoken or crash — it
    degrades to a logged line, never silent."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_MisconfigLLM(), tts=FakeTTS())
    app.text_only = True
    app._run_turn("what's the latest ED news?", threading.Event())  # must not raise
    assert app.tts.spoken == []                            # nothing voiced in text-only
    assert app.state == "Idle" and app.history == []


def test_speak_config_errors_setting_false_suppresses_the_line(tmp_path):
    """`[llm].speak_config_errors = false` opts out of the spoken line (default stays on)."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=_MisconfigLLM(), tts=FakeTTS())
    app.cfg["llm"] = {"speak_config_errors": False}
    app._run_turn("what's the latest ED news?", threading.Event())
    assert app.tts.spoken == []
    assert app.state == "Idle" and app.history == []


# --- latency watchdog (issue #97) -----------------------------------------------------

def test_latency_watchdog_speaks_interim_when_slow(tmp_path):
    """A turn that goes past the threshold without a reply speaks a plain-language 'still trying'
    line ONCE in the current voice."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    app.cfg["llm"] = {"slow_warning_seconds": 0.02}
    wd = app._arm_latency_watchdog(threading.Event())
    time.sleep(0.12)
    wd.disarm()
    assert len(app.tts.spoken) == 1                        # fired exactly once
    assert "slow" in app.tts.spoken[0].lower()


def test_latency_watchdog_disarm_prevents_speak(tmp_path):
    """Disarming (reply arrived, or turn cancelled) before the threshold suppresses the line."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    app.cfg["llm"] = {"slow_warning_seconds": 0.05}
    wd = app._arm_latency_watchdog(threading.Event())
    wd.disarm()                                            # reply came back immediately
    time.sleep(0.12)
    assert app.tts.spoken == []                            # never fired


def test_latency_watchdog_text_only_does_not_speak(tmp_path):
    """In text-only mode the interim heads-up must not touch TTS (it degrades to a log line)."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    app.text_only = True
    app.cfg["llm"] = {"slow_warning_seconds": 0.02}
    wd = app._arm_latency_watchdog(threading.Event())
    time.sleep(0.1)
    wd.disarm()
    assert app.tts.spoken == []


def test_latency_watchdog_disabled_when_threshold_zero(tmp_path):
    """slow_warning_seconds = 0 disables the watchdog entirely."""
    app = _make_app(tmp_path, stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    app.cfg["llm"] = {"slow_warning_seconds": 0}
    wd = app._arm_latency_watchdog(threading.Event())
    time.sleep(0.05)
    wd.disarm()
    assert app.tts.spoken == []



class _CueSpy:
    """Records the ORDER of cue calls so a test can assert the failure cue is emitted AFTER the
    bus-clearing Idle transition (issue: shipped failure cue was silent — see _fail_cue_to_idle)."""

    def __init__(self):
        self.calls = []

    def play(self, name, wait=False):
        self.calls.append(("play", name))

    def stop_loop(self):
        self.calls.append(("stop_loop",))

    def start_loop(self, name):
        self.calls.append(("start_loop", name))

    def stop(self):
        self.calls.append(("stop",))


def test_failure_cue_plays_after_the_idle_transition_not_before(tmp_path):
    """Regression: on no-speech the `failed` cue was submitted BEFORE set_state('Idle'), whose
    stop_loop() clear_bus()'d the shared alert bus and dropped it — so the shipped failure cue was
    silent out of the box. It must be played AFTER the transition, with no bus clear following."""
    app = _make_app(tmp_path, stt=FakeSTT(text="   "), llm=FakeLLM(text="x"), tts=FakeTTS())
    spy = _CueSpy()
    app.cues = spy
    app._process(object(), threading.Event())   # whitespace transcript -> no-speech path

    assert ("play", "failed") in spy.calls                       # the cue was played at all
    play_idx = spy.calls.index(("play", "failed"))
    clearing = {"stop_loop", "stop"}
    # Nothing clears the alert bus AFTER the cue is submitted (that's what used to drop it)...
    assert not any(c[0] in clearing for c in spy.calls[play_idx + 1:])
    # ...and the Idle transition's clear happened BEFORE it, proving the fixed order.
    assert any(c[0] in clearing for c in spy.calls[:play_idx])
