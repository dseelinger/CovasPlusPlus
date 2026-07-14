"""Tests for the Gemini LLM provider (issue #13).

Default (unit) tests are OFFLINE and FREE: the network lives in `_stream_generate`, which the tests
monkeypatch to yield canned SSE chunk dicts — so the function-call loop, Google-Search grounding
side-channel, thought->thinking routing, usage accounting, and cancellation are all exercised
without a key or a socket. The `@pytest.mark.integration` `paid` test at the bottom hits the real API.
"""
from __future__ import annotations

import os
import threading

import pytest

from covas.providers import gemini_llm as gem


# ---- helpers ---------------------------------------------------------------
def _llm(monkeypatch=None, *, key="test-key", web_search=False, **cfg):
    if monkeypatch is not None and key is not None:
        monkeypatch.setenv("GEMINI_API_KEY", key)
    return gem.GeminiLLM({
        "gemini": {"model": "gemini-2.5-flash", **cfg},
        "web_search": {"enabled": web_search},
        "personality": {"enabled": False},
        "pricing": {"gemini-2.5-flash": {"input": 0.30, "output": 2.50}},
    })


def _text(t, *, thought=False):
    part = {"text": t}
    if thought:
        part["thought"] = True
    return {"candidates": [{"content": {"parts": [part]}}]}


def _fcall(name, args):
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args}}]}}]}


def _grounding(*queries):
    return {"candidates": [{"content": {"parts": []},
                            "groundingMetadata": {"webSearchQueries": list(queries)}}]}


def _usage(p, c):
    return {"usageMetadata": {"promptTokenCount": p, "candidatesTokenCount": c}}


def _run(provider, monkeypatch, rounds, *, tool_handler=None, tools=None, cancel=None):
    seq = iter(rounds)
    calls = {"n": 0}

    def fake_stream(base_url, model, key, body, cancel_ev, **k):
        calls["n"] += 1
        calls["last_body"] = body
        for chunk in next(seq):
            if cancel_ev.is_set():
                return
            yield chunk

    monkeypatch.setattr(gem, "_stream_generate", fake_stream)
    events: list[tuple] = []
    text = "".join(
        piece for kind, piece in provider.stream_reply(
            [{"role": "user", "content": "hi"}], cancel or threading.Event(),
            lambda k, d: events.append((k, d)),
            tool_handler=tool_handler, tools=tools)
        if kind == "text")
    return text, events, calls


# ---- contents + tool translation -------------------------------------------
def test_contents_maps_roles_and_text():
    p = gem.GeminiLLM({"gemini": {}, "web_search": {"enabled": False}, "personality": {"enabled": False}})
    contents = p._contents([{"role": "user", "content": "hello"},
                            {"role": "assistant", "content": "hi there"}])
    assert contents == [{"role": "user", "parts": [{"text": "hello"}]},
                        {"role": "model", "parts": [{"text": "hi there"}]}]   # assistant -> model


def test_build_tools_function_declarations_and_grounding():
    tools = [{"name": "get_next", "description": "d",
              "input_schema": {"type": "object", "properties": {}}}]
    out = gem._build_tools(tools, grounding=True)
    assert out[0] == {"functionDeclarations": [
        {"name": "get_next", "description": "d", "parameters": {"type": "object", "properties": {}}}]}
    assert {"googleSearch": {}} in out                       # grounding tool added
    # grounding off -> only function declarations; nameless tools dropped
    assert gem._build_tools([{"description": "x"}], grounding=False) == []


# ---- streaming text + usage + system ---------------------------------------
def test_streams_text_and_emits_usage_and_system(monkeypatch):
    p = _llm(monkeypatch)
    p._system = "You are COVAS."
    text, events, calls = _run(p, monkeypatch, [[_text("Hello, "), _text("Commander."), _usage(10, 5)]])
    assert text == "Hello, Commander." and calls["n"] == 1
    assert calls["last_body"]["systemInstruction"] == {"parts": [{"text": "You are COVAS."}]}
    usage = [d for k, d in events if k == "usage"][0]
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 5 and usage["cost_usd"] > 0.0


def test_thought_part_routed_to_thinking(monkeypatch):
    text, events, _ = _run(_llm(monkeypatch), monkeypatch,
                           [[_text("planning", thought=True), _text("the answer")]])
    assert text == "the answer"
    assert ("thinking", "planning") in events


def test_grounding_queries_surface_as_search_events(monkeypatch):
    text, events, _ = _run(_llm(monkeypatch, web_search=True), monkeypatch,
                           [[_grounding("elite dangerous news", "thargoid war"), _text("Here you go")]])
    assert text == "Here you go"
    assert ("search", "elite dangerous news") in events and ("search", "thargoid war") in events


def test_grounding_tool_added_only_when_web_search_enabled(monkeypatch):
    _, _, calls = _run(_llm(monkeypatch, web_search=True), monkeypatch, [[_text("hi")]],
                       tools=[{"name": "t"}])
    assert {"googleSearch": {}} in calls["last_body"]["tools"]
    _, _, calls2 = _run(_llm(monkeypatch, web_search=False), monkeypatch, [[_text("hi")]],
                        tools=[{"name": "t"}])
    assert all("googleSearch" not in t for t in calls2["last_body"]["tools"])


# ---- function-call loop ----------------------------------------------------
def test_function_call_loop_dispatches_and_feeds_response(monkeypatch):
    round1 = [_fcall("set_objective", {"name": "scoop fuel"}), _usage(20, 8)]
    round2 = [_text("Done — marked it."), _usage(30, 6)]
    seen = {}

    def handler(name, args):
        seen["name"], seen["args"] = name, args
        return "objective set"

    text, events, calls = _run(_llm(monkeypatch), monkeypatch, [round1, round2],
                               tool_handler=handler, tools=[{"name": "set_objective"}])
    assert calls["n"] == 2 and text == "Done — marked it."
    assert seen == {"name": "set_objective", "args": {"name": "scoop fuel"}}   # args arrive as a dict
    assert ("tool", "set_objective") in events
    # the tool result was fed back as a functionResponse user turn
    last = calls["last_body"]["contents"]
    assert last[-1]["role"] == "user"
    assert last[-1]["parts"][0]["functionResponse"]["response"] == {"result": "objective set"}
    assert last[-2]["role"] == "model" and "functionCall" in last[-2]["parts"][0]


def test_two_function_calls_dispatched(monkeypatch):
    round1 = [_fcall("get_next", {}), _fcall("find_objectives", {"query": "fuel"})]
    round2 = [_text("ok")]
    calls_seen = []
    _run(_llm(monkeypatch), monkeypatch, [round1, round2],
         tool_handler=lambda n, a: calls_seen.append(n) or "r",
         tools=[{"name": "get_next"}, {"name": "find_objectives"}])
    assert calls_seen == ["get_next", "find_objectives"]


def test_tool_error_is_soft(monkeypatch):
    round1 = [_fcall("boom", {})]
    round2 = [_text("recovered")]

    def boom(name, args):
        raise RuntimeError("kaboom")

    text, _, calls = _run(_llm(monkeypatch), monkeypatch, [round1, round2],
                          tool_handler=boom, tools=[{"name": "boom"}])
    assert text == "recovered" and calls["n"] == 2


def test_no_tools_no_loop(monkeypatch):
    text, _, calls = _run(_llm(monkeypatch), monkeypatch, [[_text("just talking")]])
    assert text == "just talking" and calls["n"] == 1


# ---- cancellation + no key --------------------------------------------------
def test_cancel_stops_streaming(monkeypatch):
    cancel = threading.Event()

    def fake_stream(base_url, model, key, body, cancel_ev, **k):
        yield _text("first ")
        cancel.set()
        yield _text("second")

    monkeypatch.setattr(gem, "_stream_generate", fake_stream)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out = list(gem.GeminiLLM({"gemini": {}, "web_search": {"enabled": False},
                              "personality": {"enabled": False}}).stream_reply(
        [{"role": "user", "content": "hi"}], cancel, lambda *a: None))
    assert out == [("text", "first ")]


def test_no_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        list(gem.GeminiLLM({"gemini": {}, "web_search": {"enabled": False},
                            "personality": {"enabled": False}}).stream_reply(
            [{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None))


def test_google_api_key_env_fallback(monkeypatch):
    from covas.firstrun import gemini_key
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "goog")
    assert gemini_key({"gemini": {}}) == "goog"


# ---- request shaping + SSE parsing -----------------------------------------
def test_request_body_carries_model_cap_and_tools(monkeypatch):
    _, _, calls = _run(_llm(monkeypatch), monkeypatch, [[_text("hi")]],
                       tools=[{"name": "get_next", "input_schema": {"type": "object"}}])
    body = calls["last_body"]
    assert body["generationConfig"]["maxOutputTokens"] >= 1
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "get_next"


def test_stream_generate_parses_sse_and_uses_header(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self):
            yield b'data: {"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}'
            yield b''
            yield b'data: {"candidates":[{"content":{"parts":[{"text":"there"}]}}]}'

    def fake_post(url, **k):
        captured["url"] = url
        captured["headers"] = k.get("headers")
        return _Resp()

    monkeypatch.setattr(gem.requests, "post", fake_post)
    chunks = list(gem._stream_generate("http://x/v1beta", "gemini-2.5-flash", "SECRET", {},
                                       threading.Event()))
    assert len(chunks) == 2
    assert ":streamGenerateContent?alt=sse" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "SECRET"   # key in header, not URL
    assert "SECRET" not in captured["url"]


def test_stream_generate_raises_on_non_200(monkeypatch):
    class _Resp:
        status_code = 403
        text = "forbidden"
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self): return iter(())

    monkeypatch.setattr(gem.requests, "post", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError):
        list(gem._stream_generate("http://x/v1beta", "m", "k", {}, threading.Event()))


# ---- opt-in integration (real Gemini API; needs a key) ---------------------
@pytest.mark.integration
@pytest.mark.paid
def test_live_gemini_replies():
    """One real streamGenerateContent call. Needs GEMINI_API_KEY (or GOOGLE_API_KEY); skipped
    otherwise so the paid suite stays deliberate. Uses the cheap Flash model."""
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        pytest.skip("set GEMINI_API_KEY to run the live Gemini test")
    cfg = {"gemini": {"model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")},
           "web_search": {"enabled": False}, "personality": {"enabled": False}, "pricing": {}}
    p = gem.GeminiLLM(cfg)
    text = "".join(piece for kind, piece in p.stream_reply(
        [{"role": "user", "content": "Say 'docking granted' and nothing else."}],
        threading.Event(), lambda *a: None) if kind == "text")
    assert text.strip()
