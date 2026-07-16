"""OpenAI-compatible LLM provider (issue #12).

ONE implementation unlocks **OpenAI, Groq, DeepSeek, and OpenRouter** — they all speak the OpenAI
`chat/completions` API, so only `[openai].base_url` + `model` differ. Streaming over `requests`
(already a dep — no OpenAI SDK), normalized to the shared event contract in `base.py` so `app.py`
consumes it identically to the Anthropic provider.

Tool calling is the tricky part: OpenAI streams `tool_calls` as **deltas** (id/name on the first
chunk, `arguments` assembled across many), and links a result back by `tool_call_id` — different
from Anthropic's block model. `stream_reply` assembles the deltas, dispatches via the shared
`tool_handler`, and loops (append the assistant `tool_calls` + `role:"tool"` results, re-request)
until the model produces a final answer — the same client-tool loop `llm.py` runs for Anthropic.

Tiering is provided by the router foundation (#11): the per-turn `model` comes from
`[openai].tiers.{cheap,standard,premium}`. Reasoning models (DeepSeek-R1, o-series) that stream a
`reasoning_content` delta route it to `on_event("thinking", …)`, kept OUT of the spoken text. Usage
is costed via the shared `[pricing]` table (`llm.estimate_cost`). No web-search tool (the OpenAI
chat/completions API has none — that stays an Anthropic-only capability). Fail soft: a request error
raises a clear RuntimeError the app already guards, degrading the turn to text, never crashing.

Key: `[openai].api_key_file` (DPAPI-encrypted; shared with the OpenAI TTS provider, #16).
"""
from __future__ import annotations

import json
import threading
from typing import Iterator, Optional

import requests

from ..llm import build_system, estimate_cost
from ._retry import (RetryPolicy, TransientError, is_retryable_status,
                     parse_retry_after, run_with_retry)
from .base import OnEvent, ToolHandler

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"
_USER_AGENT = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion)"
# Cap the client-tool loop like the Anthropic path, so a misbehaving model can't spin forever.
_MAX_ROUNDS = 8


class OpenAILLM:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        o = cfg.get("openai", {}) or {}
        self.base_url = str(o.get("base_url", "")).strip().rstrip("/") or _DEFAULT_BASE_URL
        self.model = str(o.get("model", "")).strip() or _DEFAULT_MODEL
        self._max_tokens = int(o.get("max_tokens",
                                     (cfg.get("anthropic", {}) or {}).get("max_tokens", 1024)))
        self._system = build_system(cfg)  # personality.txt, or None if OFF

    def _key(self) -> str:
        from ..firstrun import openai_key
        key = openai_key(self._cfg)
        if not key:
            raise RuntimeError(
                "OpenAI LLM selected but no key found (add it in Settings, or to [openai].api_key_file)."
            )
        return key

    def _messages(self, messages: list[dict]) -> list[dict]:
        """Convert the app's conversation history to OpenAI chat messages (system first). History
        turns carry plain-string content; an Anthropic-style block list is flattened to its text."""
        out: list[dict] = []
        if self._system:
            out.append({"role": "system", "content": self._system})
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                out.append({"role": m["role"], "content": content})
            elif isinstance(content, list):
                txt = " ".join(b.get("text", "") for b in content
                               if isinstance(b, dict) and b.get("type") == "text").strip()
                if txt:
                    out.append({"role": m["role"], "content": txt})
        return out

    def stream_reply(
        self,
        messages: list[dict],
        cancel: threading.Event,
        on_event: OnEvent,
        tool_handler: Optional[ToolHandler] = None,
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[tuple[str, str]]:
        key = self._key()
        working = self._messages(messages)
        oa_tools = _translate_tools(tools)
        mdl = str(model or self.model)
        cap = int(max_tokens if max_tokens is not None else self._max_tokens)
        policy = RetryPolicy.from_cfg(self._cfg)  # transient-error retry (issue #97)

        for _round in range(_MAX_ROUNDS):
            body: dict = {"model": mdl, "messages": working, "stream": True,
                          "stream_options": {"include_usage": True}, "max_tokens": cap}
            if oa_tools:
                body["tools"] = oa_tools
                body["tool_choice"] = "auto"

            text_parts: list[str] = []
            tool_calls: dict[int, dict] = {}
            finish: Optional[str] = None
            usage: Optional[dict] = None

            for chunk in _stream_chat(self.base_url, key, body, cancel, policy=policy):
                if cancel.is_set():
                    return
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                ch = choices[0]
                delta = ch.get("delta") or {}
                # Reasoning models (DeepSeek-R1, o-series) stream reasoning here -> thinking channel.
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    on_event("thinking", reasoning)
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield ("text", content)
                for tc in delta.get("tool_calls") or []:
                    _accumulate_tool_call(tool_calls, tc)
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]

            if cancel.is_set():
                return
            if usage:
                on_event("usage", _usage_event(self._cfg, mdl, usage))

            # A client-tool round: assemble the calls, run them, feed results back, re-request.
            if finish == "tool_calls" and tool_handler is not None and tool_calls:
                calls = _finalize_tool_calls(tool_calls)
                working.append({
                    "role": "assistant",
                    "content": "".join(text_parts) or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                        for c in calls
                    ],
                })
                for c in calls:
                    on_event("tool", c["name"])
                    try:
                        args = json.loads(c["args"]) if c["args"].strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        out = tool_handler(c["name"], args if isinstance(args, dict) else {})
                    except Exception as e:  # noqa: BLE001 — a tool error must not crash the loop
                        out = f"Tool error: {e}"
                    working.append({"role": "tool", "tool_call_id": c["id"], "content": out})
                continue
            return


# ---- module helpers -------------------------------------------------------
def _close(r) -> None:  # noqa: ANN001 — a requests.Response (or a test double without .close)
    """Best-effort release of a non-200 response before we raise — test doubles may lack close()."""
    try:
        r.close()
    except Exception:  # noqa: BLE001
        pass


def _translate_tools(tools: Optional[list[dict]]) -> list[dict]:
    """Translate the shared Anthropic-style tool schemas ({name, description, input_schema}) into
    OpenAI function tools ({type:'function', function:{name, description, parameters}})."""
    out: list[dict] = []
    for t in tools or []:
        name = (t or {}).get("name")
        if not name:
            continue
        out.append({"type": "function", "function": {
            "name": name,
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out


def _accumulate_tool_call(acc: dict[int, dict], tc: dict) -> None:
    """Fold one streamed tool_call delta into the per-index accumulator (id/name arrive once,
    `arguments` is concatenated across many deltas)."""
    idx = tc.get("index", 0)
    slot = acc.setdefault(idx, {"id": "", "name": "", "args": ""})
    if tc.get("id"):
        slot["id"] = tc["id"]
    fn = tc.get("function") or {}
    if fn.get("name"):
        slot["name"] = fn["name"]
    if fn.get("arguments"):
        slot["args"] += fn["arguments"]


def _finalize_tool_calls(acc: dict[int, dict]) -> list[dict]:
    """Ordered list of assembled calls, each with a guaranteed id (some endpoints omit it) so the
    assistant tool_calls and the role:'tool' results reference the same tool_call_id."""
    calls: list[dict] = []
    for i in sorted(acc):
        c = acc[i]
        calls.append({"id": c["id"] or f"call_{i}", "name": c["name"], "args": c["args"]})
    return calls


def _usage_event(cfg: dict, model: str, usage: dict) -> dict:
    """Normalize an OpenAI `usage` object to the provider-agnostic usage dict + $ estimate."""
    ev = {
        "model": model,
        "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    ev["cost_usd"] = estimate_cost(model, ev, cfg.get("pricing", {}))
    return ev


def _stream_chat(base_url: str, key: str, body: dict, cancel: threading.Event,
                 *, policy: Optional[RetryPolicy] = None,
                 timeout=(10, 600)) -> Iterator[dict]:  # noqa: ANN001
    """POST `chat/completions` with stream=True and yield each parsed SSE `data:` chunk as a dict.
    Stops on `[DONE]` or `cancel`.

    Retry (issue #97) wraps the CONNECT only: a 429/5xx/529 or a connection/timeout on the initial
    request backs off (honoring Retry-After) and re-tries per `policy`; a 4xx (bad key, 404 model)
    fails fast. Once bytes stream, a mid-stream drop is NOT retried — it propagates and the turn
    falls soft. A non-retryable non-200 raises the same RuntimeError the app already guards."""
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": _USER_AGENT}
    policy = policy or RetryPolicy()

    def _connect() -> "requests.Response":
        r = requests.post(url, data=json.dumps(body), headers=headers, stream=True, timeout=timeout)
        if r.status_code != 200:
            detail = f"OpenAI LLM {r.status_code}: {r.text[:200]}"
            retryable = is_retryable_status(r.status_code)
            ra = parse_retry_after(r.headers.get("Retry-After")) if retryable else None
            _close(r)
            if retryable:
                raise TransientError(detail, status=r.status_code, retry_after=ra, provider="OpenAI")
            raise RuntimeError(detail)
        return r

    with run_with_retry(_connect, cancel, policy, provider="OpenAI") as r:
        for line in r.iter_lines():
            if cancel.is_set():
                return
            if not line:
                continue
            s = line.decode("utf-8") if isinstance(line, bytes) else line
            if not s.startswith("data:"):
                continue
            data = s[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
