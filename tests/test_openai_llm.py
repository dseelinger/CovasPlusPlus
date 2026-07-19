"""Tests for the OpenAI-compatible LLM provider (issue #12).

Default (unit) tests are OFFLINE and FREE: the network lives in `_stream_chat`, which the tests
monkeypatch to yield canned SSE chunk dicts — so the delta-assembled tool-call handling, the
tool-loop, usage accounting, reasoning->thinking routing, and cancellation are all exercised without
a key or a socket. The `@pytest.mark.integration` `paid` test at the bottom hits the real API.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from covas import firstrun
from covas.providers import openai_llm as oai
from covas.providers._retry import ProviderError, is_config_error


# ---- helpers ---------------------------------------------------------------
def _llm(monkeypatch=None, *, key="test-key", **cfg):
    # Keys are file-only (DPAPI) now — patch the firstrun resolver instead of exporting an env var.
    if monkeypatch is not None and key is not None:
        monkeypatch.setattr(firstrun, "openai_key", lambda cfg: key)
    return oai.OpenAILLM({
        "openai": {"model": "gpt-4o-mini", **cfg},
        "personality": {"enabled": False},
        "pricing": {"gpt-4o-mini": {"input": 0.15, "output": 0.60}},
    })


def _text_chunk(content):
    return {"choices": [{"delta": {"content": content}, "finish_reason": None}]}


def _tool_chunk(index, *, id=None, name=None, args=None, finish=None):
    tc = {"index": index}
    if id is not None:
        tc["id"] = id
    fn = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    if fn:
        tc["function"] = fn
    return {"choices": [{"delta": {"tool_calls": [tc]}, "finish_reason": finish}]}


def _usage_chunk(prompt, completion):
    return {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


def _run(provider, monkeypatch, rounds, *, tool_handler=None, tools=None, cancel=None):
    """Drive stream_reply with `rounds` = a list of chunk-lists (one per HTTP round), returning
    (yielded_text, events). _stream_chat is monkeypatched to replay the next round each call."""
    seq = iter(rounds)
    calls = {"n": 0}

    def fake_stream(base_url, key, body, cancel_ev, **k):
        calls["n"] += 1
        for chunk in next(seq):
            if cancel_ev.is_set():
                return
            yield chunk

    monkeypatch.setattr(oai, "_stream_chat", fake_stream)
    events: list[tuple] = []
    text = "".join(
        piece for kind, piece in provider.stream_reply(
            [{"role": "user", "content": "hi"}], cancel or threading.Event(),
            lambda k, d: events.append((k, d)),
            tool_handler=tool_handler, tools=tools)
        if kind == "text")
    return text, events, calls["n"]


# ---- message + tool translation --------------------------------------------
def test_messages_prepends_system_and_keeps_str_turns():
    # _messages now takes the per-turn system prompt as an arg (issue #151) instead of a frozen attr.
    p = oai.OpenAILLM({"openai": {}, "personality": {"enabled": False}})
    msgs = p._messages([{"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "hi there"}], "You are COVAS.")
    assert msgs[0] == {"role": "system", "content": "You are COVAS."}
    assert msgs[1]["role"] == "user" and msgs[2]["content"] == "hi there"


def test_messages_flattens_block_content():
    p = oai.OpenAILLM({"openai": {}, "personality": {"enabled": False}})
    msgs = p._messages([{"role": "user",
                         "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}],
                       None)
    assert msgs == [{"role": "user", "content": "a b"}]


def test_translate_tools_to_openai_functions():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    out = oai._translate_tools([{"name": "get_next", "description": "next objective",
                                 "input_schema": schema}])
    assert len(out) == 1
    fn = out[0]
    assert fn["type"] == "function"
    assert fn["function"] == {"name": "get_next", "description": "next objective",
                              "parameters": schema}
    assert oai._translate_tools([{"description": "no name"}]) == []   # nameless dropped


# ---- streaming text + usage -------------------------------------------------
def test_streams_text_and_emits_usage(monkeypatch):
    rounds = [[_text_chunk("Hello, "), _text_chunk("Commander."), _usage_chunk(10, 5)]]
    text, events, n = _run(_llm(monkeypatch), monkeypatch, rounds)
    assert text == "Hello, Commander." and n == 1
    usage = [d for k, d in events if k == "usage"][0]
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 5
    assert usage["model"] == "gpt-4o-mini" and usage["cost_usd"] > 0.0   # gpt-4o-mini is priced


def test_system_prompt_rebuilt_per_turn_on_ship_swap(monkeypatch, tmp_path):
    """Issue #151: build_system() is re-evaluated each stream_reply, so the ACTIVE ship's per-ship
    crew roster (#127) — stamped onto cfg before each turn — follows a ship SWAP on the SAME provider
    instance. The frozen-at-construction bug kept prefixing the ship-you-built-with's roster forever.
    Drives the REAL build_system -> crew.system_instruction -> load_members chain, fully offline
    (a tmp roster file, no network)."""
    roster = tmp_path / "crew.json"
    roster.write_text(json.dumps({
        "default": [{"name": "Vela"}],
        "ships": {"1": {"hull": "sidewinder", "members": [{"name": "Nyx"}]},
                  "2": {"hull": "sidewinder", "members": [{"name": "Orin"}]}},
    }), encoding="utf-8")
    cfg = {"openai": {"model": "gpt-4o-mini"}, "personality": {"enabled": False},
           "crew": {"enabled": True, "file": str(roster)},
           "experimental": {"crew": {"enabled": True}}}
    monkeypatch.setattr(firstrun, "openai_key", lambda cfg: "k")
    p = oai.OpenAILLM(cfg)

    captured: dict = {}

    def fake_stream(base_url, key, body, cancel_ev, **k):
        captured["messages"] = body["messages"]
        yield _text_chunk("ok")

    monkeypatch.setattr(oai, "_stream_chat", fake_stream)

    def _system_text() -> str:
        return next((m["content"] for m in captured["messages"] if m["role"] == "system"), "")

    # Flying ship 1 -> Nyx is the crew, Orin is not. Anchor on the "Your crew:" roster line: the
    # prompt template hardcodes an example that uses the bracket form ("[Nyx] ..."), so a bare
    # substring check would false-match the example regardless of the active roster.
    cfg["crew"]["_active_ship_id"] = "1"
    list(p.stream_reply([{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None))
    assert "Your crew: Nyx" in _system_text() and "Your crew: Orin" not in _system_text()

    # SWAP to ship 2 (same instance, no _reload_llm) -> the roster follows: Orin in, Nyx out.
    cfg["crew"]["_active_ship_id"] = "2"
    list(p.stream_reply([{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None))
    assert "Your crew: Orin" in _system_text() and "Your crew: Nyx" not in _system_text()


def test_reasoning_delta_routed_to_thinking(monkeypatch):
    chunk = {"choices": [{"delta": {"reasoning_content": "let me think"}, "finish_reason": None}]}
    text, events, _ = _run(_llm(monkeypatch), monkeypatch, [[chunk, _text_chunk("answer")]])
    assert text == "answer"
    assert ("thinking", "let me think") in events


# ---- tool-call assembly + loop ---------------------------------------------
def test_assembles_streamed_tool_call_and_loops(monkeypatch):
    # Round 1: the model streams ONE tool call as deltas (args split across chunks), then asks to
    # call it. Round 2: it produces the final answer using the tool result.
    round1 = [
        _tool_chunk(0, id="call_abc", name="set_objective"),
        _tool_chunk(0, args='{"name":'),
        _tool_chunk(0, args=' "scoop fuel"}'),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        _usage_chunk(20, 8),
    ]
    round2 = [_text_chunk("Done — marked it."), _usage_chunk(30, 6)]
    seen = {}

    def handler(name, args):
        seen["name"] = name
        seen["args"] = args
        return "objective set"

    text, events, n = _run(_llm(monkeypatch), monkeypatch, [round1, round2],
                           tool_handler=handler, tools=[{"name": "set_objective"}])
    assert n == 2                                   # looped: tool round + final round
    assert text == "Done — marked it."
    assert seen == {"name": "set_objective", "args": {"name": "scoop fuel"}}
    assert ("tool", "set_objective") in events
    assert [d for k, d in events if k == "usage"].__len__() == 2   # usage per round


def test_two_parallel_tool_calls_are_dispatched(monkeypatch):
    round1 = [
        _tool_chunk(0, id="c0", name="get_next", args="{}"),
        _tool_chunk(1, id="c1", name="find_objectives", args='{"query":"fuel"}'),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    round2 = [_text_chunk("ok")]
    calls = []
    _run(_llm(monkeypatch), monkeypatch, [round1, round2],
         tool_handler=lambda n, a: calls.append(n) or "r",
         tools=[{"name": "get_next"}, {"name": "find_objectives"}])
    assert calls == ["get_next", "find_objectives"]


def test_tool_handler_error_is_soft(monkeypatch):
    round1 = [_tool_chunk(0, id="c0", name="boom", args="{}"),
              {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}]
    round2 = [_text_chunk("recovered")]

    def boom(name, args):
        raise RuntimeError("kaboom")

    text, _, n = _run(_llm(monkeypatch), monkeypatch, [round1, round2],
                      tool_handler=boom, tools=[{"name": "boom"}])
    assert text == "recovered" and n == 2          # error fed back as a tool result, loop survived


def test_no_tools_no_loop(monkeypatch):
    text, _, n = _run(_llm(monkeypatch), monkeypatch, [[_text_chunk("just talking")]])
    assert text == "just talking" and n == 1


# ---- cancellation -----------------------------------------------------------
def test_cancel_stops_streaming(monkeypatch):
    cancel = threading.Event()

    def fake_stream(base_url, key, body, cancel_ev, **k):
        yield _text_chunk("first ")
        cancel.set()                                # user barges in mid-stream
        yield _text_chunk("second")

    monkeypatch.setattr(oai, "_stream_chat", fake_stream)
    monkeypatch.setattr(firstrun, "openai_key", lambda cfg: "k")
    out = list(oai.OpenAILLM({"openai": {}, "personality": {"enabled": False}}).stream_reply(
        [{"role": "user", "content": "hi"}], cancel, lambda *a: None))
    assert out == [("text", "first ")]              # stopped before the second chunk


# ---- transient retry (issue #97) -------------------------------------------
def test_transient_503_then_success_emits_retry_and_recovers(monkeypatch):
    """Drive the REAL _stream_chat retry loop (not the monkeypatched shortcut): a 503 then a 200
    streaming reply. The turn must recover AND surface a 'retry' event so the log shows the backoff."""
    monkeypatch.setattr(firstrun, "openai_key", lambda cfg: "k")

    class _Resp:
        def __init__(self, status, *, lines=()):
            self.status_code = status
            self.text = "" if status == 200 else "overloaded (stub)"
            self.headers = {}          # no Retry-After -> uses the (tiny) backoff below
            self._lines = list(lines)

        def iter_lines(self):
            yield from self._lines

        def close(self):
            pass

        def __enter__(self):        # a real requests.Response is a context manager
            return self

        def __exit__(self, *exc):
            return False

    ok = [b'data: {"choices":[{"delta":{"content":"hello"}}]}', b'', b'data: [DONE]']
    seq = iter([_Resp(503), _Resp(200, lines=ok)])
    monkeypatch.setattr(oai.requests, "post", lambda *a, **k: next(seq))

    llm = oai.OpenAILLM({
        "openai": {"model": "gpt-4o-mini", "base_url": "http://stub/v1"},
        "personality": {"enabled": False},
        # microscopic backoff so the test doesn't actually wait out the retry
        "llm": {"retry": {"base_delay": 0.001, "max_delay": 0.001, "factor": 1.0, "jitter": 0.0}},
    })
    events: list[tuple] = []
    text = "".join(piece for kind, piece in llm.stream_reply(
        [{"role": "user", "content": "hi"}], threading.Event(),
        lambda k, d: events.append((k, d))) if kind == "text")

    assert text == "hello"                                  # recovered on the retry
    retries = [d for k, d in events if k == "retry"]
    assert len(retries) == 1                                # the single 503 surfaced one backoff
    assert retries[0]["provider"] == "OpenAI" and retries[0]["reason"] == "HTTP 503"
    assert retries[0]["attempt"] == 1 and retries[0]["attempts"] >= 1


# ---- no key -----------------------------------------------------------------
def test_no_key_raises(monkeypatch):
    """A missing key is a MISCONFIGURATION (issue #108), not a bare RuntimeError: it carries a
    401-shaped ProviderError so the app's misconfig voice branch classifies it."""
    monkeypatch.setattr(firstrun, "openai_key", lambda cfg: None)
    with pytest.raises(ProviderError) as ei:
        list(oai.OpenAILLM({"openai": {}, "personality": {"enabled": False}}).stream_reply(
            [{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None))
    assert ei.value.status == 401 and is_config_error(ei.value)


# ---- request body shaping ---------------------------------------------------
def test_request_body_has_model_tools_and_stream(monkeypatch):
    captured = {}

    def fake_stream(base_url, key, body, cancel_ev, **k):
        captured.update(body)
        captured["_base"] = base_url
        yield _text_chunk("hi")

    monkeypatch.setattr(oai, "_stream_chat", fake_stream)
    p = _llm(monkeypatch, base_url="https://api.groq.com/openai/v1/")
    list(p.stream_reply([{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None,
                        tools=[{"name": "get_next", "input_schema": {"type": "object"}}],
                        model="llama-3.3-70b-versatile", max_tokens=256))
    assert captured["_base"] == "https://api.groq.com/openai/v1"   # trailing slash trimmed
    assert captured["model"] == "llama-3.3-70b-versatile"
    assert captured["max_tokens"] == 256 and captured["stream"] is True
    assert captured["tools"][0]["function"]["name"] == "get_next"
    assert captured["tool_choice"] == "auto"


# ---- SSE line parsing (the one bit of _stream_chat that's pure) -------------
def test_stream_chat_parses_data_lines(monkeypatch):
    class _Resp:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}'
            yield b''
            yield b'data: [DONE]'
            yield b'data: {"choices":[]}'   # never reached (after DONE)

    monkeypatch.setattr(oai.requests, "post", lambda *a, **k: _Resp())
    chunks = list(oai._stream_chat("http://x/v1", "k", {}, threading.Event()))
    assert chunks == [{"choices": [{"delta": {"content": "hi"}}]}]


def test_stream_chat_raises_on_non_200(monkeypatch):
    """A fail-fast (non-retryable) non-200 raises a structured ProviderError with the status intact
    (issue #108), not a bare RuntimeError, so the app's misconfig voice branch can classify it."""
    class _Resp:
        status_code = 401
        text = "unauthorized"
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self): return iter(())

    monkeypatch.setattr(oai.requests, "post", lambda *a, **k: _Resp())
    with pytest.raises(ProviderError) as ei:
        list(oai._stream_chat("http://x/v1", "k", {}, threading.Event()))
    assert ei.value.status == 401 and ei.value.provider == "OpenAI" and is_config_error(ei.value)


# ---- opt-in integration (real OpenAI-compatible API; needs a key) ----------
@pytest.mark.integration
@pytest.mark.paid
def test_live_openai_llm_replies():
    """One real chat/completions call. Needs OPENAI_API_KEY (+ optional OPENAI_BASE_URL / OPENAI_MODEL
    for Groq/DeepSeek/OpenRouter); skipped otherwise so the paid suite stays deliberate."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("set OPENAI_API_KEY to run the live OpenAI LLM test")
    cfg = {"openai": {"base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                      "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini")},
           "personality": {"enabled": False}, "pricing": {}}
    p = oai.OpenAILLM(cfg)
    text = "".join(piece for kind, piece in p.stream_reply(
        [{"role": "user", "content": "Say 'docking granted' and nothing else."}],
        threading.Event(), lambda *a: None) if kind == "text")
    assert text.strip()
