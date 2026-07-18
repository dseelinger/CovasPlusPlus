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

from covas import firstrun
from covas.providers import gemini_llm as gem
from covas.providers._retry import ProviderError, is_config_error


# ---- helpers ---------------------------------------------------------------
def _llm(monkeypatch=None, *, key="test-key", web_search=False, **cfg):
    # Keys are file-only (DPAPI) now — patch the firstrun resolver instead of exporting an env var.
    if monkeypatch is not None and key is not None:
        monkeypatch.setattr(firstrun, "gemini_key", lambda cfg: key)
    return gem.GeminiLLM({
        "gemini": {"model": "gemini-flash-lite-latest", **cfg},
        "web_search": {"enabled": web_search},
        "personality": {"enabled": False},
        "pricing": {"gemini-flash-lite-latest": {"input": 0.10, "output": 0.40}},
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
    monkeypatch.setattr(firstrun, "gemini_key", lambda cfg: "k")
    out = list(gem.GeminiLLM({"gemini": {}, "web_search": {"enabled": False},
                              "personality": {"enabled": False}}).stream_reply(
        [{"role": "user", "content": "hi"}], cancel, lambda *a: None))
    assert out == [("text", "first ")]


def test_no_key_raises(monkeypatch):
    """A missing key is a MISCONFIGURATION (issue #108), not a bare RuntimeError: it carries a
    401-shaped ProviderError so the app's misconfig voice branch classifies it."""
    monkeypatch.setattr(firstrun, "gemini_key", lambda cfg: None)
    with pytest.raises(ProviderError) as ei:
        list(gem.GeminiLLM({"gemini": {}, "web_search": {"enabled": False},
                            "personality": {"enabled": False}}).stream_reply(
            [{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None))
    assert ei.value.status == 401 and is_config_error(ei.value)


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
    chunks = list(gem._stream_generate("http://x/v1beta", "gemini-flash-lite-latest", "SECRET", {},
                                       threading.Event()))
    assert len(chunks) == 2
    assert ":streamGenerateContent?alt=sse" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "SECRET"   # key in header, not URL
    assert "SECRET" not in captured["url"]


def test_stream_generate_raises_on_non_200(monkeypatch):
    """A fail-fast (non-retryable) non-200 raises a structured ProviderError with the status intact
    (issue #108), not a bare RuntimeError, so the app's misconfig voice branch can classify it."""
    class _Resp:
        status_code = 403
        text = "forbidden"
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self): return iter(())

    monkeypatch.setattr(gem.requests, "post", lambda *a, **k: _Resp())
    with pytest.raises(ProviderError) as ei:
        list(gem._stream_generate("http://x/v1beta", "m", "k", {}, threading.Event()))
    assert ei.value.status == 403 and ei.value.provider == "Gemini" and is_config_error(ei.value)


def test_stream_generate_404_message_names_model_and_config(monkeypatch):
    # A bad/stale model id (issue #91) must surface a clear, actionable message, not a raw 404.
    class _Resp:
        status_code = 404
        text = '{"error":{"message":"model: gemini-3.1-flash-lite"}}'
        headers: dict = {}
        def close(self): pass

    monkeypatch.setattr(gem.requests, "post", lambda *a, **k: _Resp())
    with pytest.raises(ProviderError) as ei:
        list(gem._stream_generate("http://x/v1beta", "gemini-bogus", "k", {}, threading.Event()))
    msg = str(ei.value)
    assert "gemini-bogus" in msg and "[gemini].model" in msg and "/models" in msg
    assert ei.value.status == 404 and is_config_error(ei.value)


# ---- model-list parsing + fail-soft guard (issue #91) ----------------------
def test_parse_models_list_strips_prefix_and_dedupes():
    payload = {"models": [
        {"name": "models/gemini-2.5-flash-lite"},
        {"name": "models/gemini-2.5-flash"},
        {"name": "gemini-2.5-pro"},          # already bare
        {"name": "models/gemini-2.5-flash"},  # duplicate
        {"nope": "no name"},                  # skipped
        "not-a-dict",                          # skipped
    ]}
    assert gem.parse_models_list(payload) == [
        "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"]
    assert gem.parse_models_list({}) == []


def test_list_gemini_models_uses_header_and_parses(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        def json(self): return {"models": [{"name": "models/gemini-2.5-flash"}]}
        def close(self): pass

    def fake_get(url, **k):
        captured["url"] = url
        captured["headers"] = k.get("headers")
        return _Resp()

    monkeypatch.setattr(gem.requests, "get", fake_get)
    ids = gem.list_gemini_models("http://x/v1beta", "SECRET")
    assert ids == ["gemini-2.5-flash"]
    assert captured["url"].endswith("/models")
    assert captured["headers"]["x-goog-api-key"] == "SECRET"   # key in header, not URL
    assert "SECRET" not in captured["url"]


def test_provider_list_models_is_failsoft(monkeypatch):
    # A catalog fetch error (offline, bad key, non-200) must degrade to [] — never raise.
    p = _llm(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(gem, "list_gemini_models", boom)
    assert p.list_models() == []


def test_provider_list_models_no_key_is_failsoft(monkeypatch):
    monkeypatch.setattr(firstrun, "gemini_key", lambda cfg: None)
    p = gem.GeminiLLM({"gemini": {}, "web_search": {"enabled": False},
                       "personality": {"enabled": False}})
    assert p.list_models() == []   # no key -> [] not a raise


# ---- opt-in integration (real Gemini API; needs a key) ---------------------
@pytest.mark.integration
@pytest.mark.paid
def test_live_gemini_replies():
    """One real streamGenerateContent call. Needs GEMINI_API_KEY (or GOOGLE_API_KEY); skipped
    otherwise so the paid suite stays deliberate. Uses the cheap Flash model."""
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        pytest.skip("set GEMINI_API_KEY to run the live Gemini test")
    cfg = {"gemini": {"model": os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")},
           "web_search": {"enabled": False}, "personality": {"enabled": False}, "pricing": {}}
    p = gem.GeminiLLM(cfg)
    text = "".join(piece for kind, piece in p.stream_reply(
        [{"role": "user", "content": "Say 'docking granted' and nothing else."}],
        threading.Event(), lambda *a: None) if kind == "text")
    assert text.strip()


@pytest.mark.integration
@pytest.mark.paid
def test_live_gemini_tier_ids_resolve():
    """Guard against the #91 class of bug for ALIAS ids: the shipped [gemini.tiers] are `-latest`
    aliases (gemini-flash-lite-latest / …) that resolve server-side and do NOT appear verbatim in the
    concrete `GET /models` list — so a strict membership check would false-fail. Instead assert a real
    turn SUCCEEDS on each configured tier id (+ the default model): that's the true "this id is
    accepted / not a 404" check. Needs a key; paid (one short turn per unique id)."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        pytest.skip("set GEMINI_API_KEY to run the live Gemini tier-alias guard")
    from covas.config import load_config
    cfg = load_config()
    g = cfg.get("gemini", {}) or {}
    ids = {str(g.get("model", "")).strip()}
    ids |= {str(v).strip() for v in (g.get("tiers", {}) or {}).values()}
    ids = {i for i in ids if i}
    for mid in sorted(ids):
        p = gem.GeminiLLM({"gemini": {"model": mid}, "web_search": {"enabled": False},
                           "personality": {"enabled": False}, "pricing": {}})
        text = "".join(piece for kind, piece in p.stream_reply(
            [{"role": "user", "content": "Reply with the single word: ok."}],
            threading.Event(), lambda *a: None) if kind == "text")
        assert text.strip(), f"configured Gemini id {mid!r} produced no reply (stale/invalid?)"
