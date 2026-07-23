"""Tests for the Gemini LLM provider (issue #13).

Default (unit) tests are OFFLINE and FREE: the network lives in `_stream_generate`, which the tests
monkeypatch to yield canned SSE chunk dicts — so the function-call loop, Google-Search grounding
side-channel, thought->thinking routing, usage accounting, and cancellation are all exercised
without a key or a socket. The `@pytest.mark.integration` `paid` test at the bottom hits the real API.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from covas import firstrun
from covas.providers import gemini_llm as gem
from covas.providers._retry import ProviderError, is_config_error, is_degraded_error


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
    # System is built PER TURN via build_system now (issue #151), not a frozen attr — patch it.
    monkeypatch.setattr(gem, "build_system", lambda cfg: "You are COVAS.")
    text, events, calls = _run(p, monkeypatch, [[_text("Hello, "), _text("Commander."), _usage(10, 5)]])
    assert text == "Hello, Commander." and calls["n"] == 1
    assert calls["last_body"]["systemInstruction"] == {"parts": [{"text": "You are COVAS."}]}
    usage = [d for k, d in events if k == "usage"][0]
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 5 and usage["cost_usd"] > 0.0


def test_system_prompt_rebuilt_per_turn_on_ship_swap(monkeypatch, tmp_path):
    """Issue #151: build_system() is re-evaluated each stream_reply, so the ACTIVE ship's per-ship
    crew roster (#127) — stamped onto cfg before each turn — follows a ship SWAP on the SAME provider
    instance. The frozen-at-construction bug kept using the ship-you-built-with's roster forever.
    Drives the REAL build_system -> crew.system_instruction -> load_members chain, fully offline
    (a tmp roster file, no network)."""
    roster = tmp_path / "crew.json"
    roster.write_text(json.dumps({
        "default": [{"name": "Vela"}],
        "ships": {"1": {"hull": "sidewinder", "members": [{"name": "Nyx"}]},
                  "2": {"hull": "sidewinder", "members": [{"name": "Orin"}]}},
    }), encoding="utf-8")
    cfg = {"gemini": {"model": "gemini-flash-lite-latest"}, "web_search": {"enabled": False},
           "personality": {"enabled": False}, "crew": {"enabled": True, "file": str(roster)},
           "experimental": {"crew": {"enabled": True}}}
    monkeypatch.setattr(firstrun, "gemini_key", lambda cfg: "k")
    p = gem.GeminiLLM(cfg)

    captured: dict = {}

    def fake_stream(base_url, model, key, body, cancel_ev, **k):
        parts = (body.get("systemInstruction") or {}).get("parts") or [{}]
        captured["system"] = parts[0].get("text", "")
        yield _text("ok")

    monkeypatch.setattr(gem, "_stream_generate", fake_stream)

    # Flying ship 1 -> Nyx is the crew, Orin is not. Anchor on the "Your crew:" roster line: the
    # prompt template hardcodes an example that uses the bracket form ("[Nyx] ..."), so a bare
    # substring check would false-match the example regardless of the active roster.
    cfg["crew"]["_active_ship_id"] = "1"
    list(p.stream_reply([{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None))
    assert "Your crew: Nyx" in captured["system"] and "Your crew: Orin" not in captured["system"]

    # SWAP to ship 2 (same instance, no _reload_llm) -> the roster follows: Orin in, Nyx out.
    cfg["crew"]["_active_ship_id"] = "2"
    list(p.stream_reply([{"role": "user", "content": "hi"}], threading.Event(), lambda *a: None))
    assert "Your crew: Orin" in captured["system"] and "Your crew: Nyx" not in captured["system"]


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


# ---- transient retry (issue #97) -------------------------------------------
# The retry POLICY is unit-tested in test_provider_retry.py; these drive Gemini's REAL
# _stream_generate connect path (parity with test_openai_llm), so the provider's own wiring —
# 503 -> TransientError -> run_with_retry -> recover or degrade — is covered, not just the policy.
def _RetryResp(status, *, lines=()):
    """A fake requests.Response covering both branches _stream_generate touches: a non-200 read
    (status/text/headers/close) and a 200 SSE stream (context-manager + iter_lines)."""
    class _R:
        def __init__(self):
            self.status_code = status
            self.text = "" if status == 200 else "overloaded (stub)"
            self.headers = {}                    # no Retry-After -> uses the tiny backoff below
            self._lines = list(lines)
        def iter_lines(self):
            yield from self._lines
        def close(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _R()


def _retry_llm():
    return gem.GeminiLLM({
        "gemini": {"model": "gemini-flash-lite-latest", "base_url": "http://stub/v1beta"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "pricing": {"gemini-flash-lite-latest": {"input": 0.1, "output": 0.4}},
        # microscopic backoff so the test never actually waits a retry out
        "llm": {"retry": {"base_delay": 0.001, "max_delay": 0.001, "factor": 1.0,
                          "jitter": 0.0, "attempts": 3}},
    })


def test_transient_503_then_success_emits_retry_and_recovers(monkeypatch):
    """A 503 then a 200 SSE reply: the turn recovers AND surfaces a 'retry' event so the log shows
    the backoff (flaky_llm_stub.py retry-recover case, MANUAL_TESTS §4.3)."""
    monkeypatch.setattr(firstrun, "gemini_key", lambda cfg: "k")
    ok = [b'data: {"candidates":[{"content":{"parts":[{"text":"hello"}]}}]}', b'']
    seq = iter([_RetryResp(503), _RetryResp(200, lines=ok)])
    monkeypatch.setattr(gem.requests, "post", lambda *a, **k: next(seq))

    events: list[tuple] = []
    text = "".join(piece for kind, piece in _retry_llm().stream_reply(
        [{"role": "user", "content": "hi"}], threading.Event(),
        lambda k, d: events.append((k, d))) if kind == "text")

    assert text == "hello"                                       # recovered on the retry
    retries = [d for k, d in events if k == "retry"]
    assert len(retries) == 1                                     # the single 503 surfaced one backoff
    assert retries[0]["provider"] == "Gemini" and retries[0]["reason"] == "HTTP 503"


def test_transient_503_exhausts_and_gives_up_degraded(monkeypatch):
    """Repeated 503s exhaust the budget and surface a RETRYABLE ProviderError — the app's degraded
    'service is overloaded' signal (flaky_llm_stub.py STUB_FAIL_TIMES=999 case, MANUAL_TESTS §4.3)."""
    monkeypatch.setattr(firstrun, "gemini_key", lambda cfg: "k")
    monkeypatch.setattr(gem.requests, "post", lambda *a, **k: _RetryResp(503))

    events: list[tuple] = []
    with pytest.raises(ProviderError) as ei:
        for _ in _retry_llm().stream_reply([{"role": "user", "content": "hi"}],
                                           threading.Event(), lambda k, d: events.append((k, d))):
            pass
    assert ei.value.retryable is True and is_degraded_error(ei.value)  # degraded, NOT fail-fast
    assert ei.value.status == 503 and ei.value.provider == "Gemini"
    assert ei.value.attempts == 3                              # 1 initial + 2 retries, then gave up
    assert len([d for k, d in events if k == "retry"]) == 2    # a backoff surfaced before each retry


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
