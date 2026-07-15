"""Gemini LLM provider — native API (issue #13).

Google's Gemini on the **native** `generateContent` API (not the OpenAI-compat shim), so we get the
richer surface the issue asks for: strong function calling, a cheap/fast **Flash** default tier, and
Google-Search **grounding** that parallels the Anthropic web_search capability. Streaming over
`requests` (already a dep — no google SDK), normalized to the shared `base.py` event contract so
`app.py` consumes it identically to Anthropic/OpenAI.

Differences from the OpenAI path this provider handles:
  * **Messages** use `contents` with roles `user`/`model` and `parts` (system goes in
    `systemInstruction`).
  * **Function calling** — Gemini streams a whole `functionCall` part (name + args as a dict, not a
    delta-assembled string); we run it via the shared `tool_handler` and send a `functionResponse`
    part back, looping (capped at 8 rounds) like the Anthropic/OpenAI client-tool loops.
  * **Grounding** — with `[web_search].enabled`, we add the `googleSearch` tool; the queries Gemini
    runs arrive in `groundingMetadata.webSearchQueries` and are surfaced via `on_event("search", …)`,
    exactly like the Anthropic web_search side-channel.
  * **Thinking** — Gemini 2.5 "thought" parts (`part.thought == true`) route to
    `on_event("thinking", …)`, kept OUT of the spoken text.

Tiering comes from the router foundation (#11): the per-turn model is `[gemini].tiers.{cheap,standard,
premium}` (Flash cheap/default, Pro for depth). Usage is costed via the shared `[pricing]` table.
Key: `[gemini].api_key_file` (DPAPI-encrypted, file-only). Cloud, so in-game is fine.
Fail soft: a request error raises a clear RuntimeError the app guards, degrading the turn to text.
"""
from __future__ import annotations

import json
import threading
from typing import Iterator, Optional

import requests

from ..llm import build_system, estimate_cost
from .base import OnEvent, ToolHandler

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_DEFAULT_MODEL = "gemini-2.5-flash"
_USER_AGENT = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion)"
_MAX_ROUNDS = 8


class GeminiLLM:
    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        g = cfg.get("gemini", {}) or {}
        self.base_url = str(g.get("base_url", "")).strip().rstrip("/") or _DEFAULT_BASE_URL
        self.model = str(g.get("model", "")).strip() or _DEFAULT_MODEL
        self._max_tokens = int(g.get("max_tokens",
                                     (cfg.get("anthropic", {}) or {}).get("max_tokens", 1024)))
        self._grounding = bool((cfg.get("web_search", {}) or {}).get("enabled", False))
        self._system = build_system(cfg)  # personality.txt, or None if OFF

    def _key(self) -> str:
        from ..firstrun import gemini_key
        key = gemini_key(self._cfg)
        if not key:
            raise RuntimeError(
                "Gemini LLM selected but no key found (add it in Settings, or to [gemini].api_key_file)."
            )
        return key

    def _contents(self, messages: list[dict]) -> list[dict]:
        """Convert the app's history to Gemini `contents` (roles user/model, text parts). Plain-
        string turns become one text part; an Anthropic-style block list flattens to its text."""
        out: list[dict] = []
        for m in messages:
            role = "model" if m.get("role") == "assistant" else "user"
            content = m.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text").strip()
            else:
                text = ""
            if text:
                out.append({"role": role, "parts": [{"text": text}]})
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
        contents = self._contents(messages)
        mdl = str(model or self.model)
        cap = int(max_tokens if max_tokens is not None else self._max_tokens)
        gem_tools = _build_tools(tools, self._grounding)

        for _round in range(_MAX_ROUNDS):
            body: dict = {"contents": contents,
                          "generationConfig": {"maxOutputTokens": cap}}
            if self._system:
                body["systemInstruction"] = {"parts": [{"text": self._system}]}
            if gem_tools:
                body["tools"] = gem_tools

            text_parts: list[str] = []
            fn_calls: list[dict] = []
            usage: Optional[dict] = None

            for chunk in _stream_generate(self.base_url, mdl, key, body, cancel):
                if cancel.is_set():
                    return
                if chunk.get("usageMetadata"):
                    usage = chunk["usageMetadata"]
                for cand in chunk.get("candidates") or []:
                    for part in ((cand.get("content") or {}).get("parts") or []):
                        if "functionCall" in part:
                            fn_calls.append(part["functionCall"])
                        elif "text" in part:
                            if part.get("thought"):
                                on_event("thinking", part["text"])
                            else:
                                text_parts.append(part["text"])
                                yield ("text", part["text"])
                    for q in ((cand.get("groundingMetadata") or {}).get("webSearchQueries") or []):
                        on_event("search", q)

            if cancel.is_set():
                return
            if usage:
                on_event("usage", _usage_event(self._cfg, mdl, usage))

            # A function-call round: echo the model turn, run the calls, feed responses back, re-ask.
            if fn_calls and tool_handler is not None:
                contents.append({"role": "model",
                                 "parts": [{"functionCall": fc} for fc in fn_calls]})
                responses = []
                for fc in fn_calls:
                    name = fc.get("name", "")
                    on_event("tool", name)
                    args = fc.get("args")
                    try:
                        out = tool_handler(name, args if isinstance(args, dict) else {})
                    except Exception as e:  # noqa: BLE001 — a tool error must not crash the loop
                        out = f"Tool error: {e}"
                    responses.append({"functionResponse": {"name": name,
                                                           "response": {"result": out}}})
                contents.append({"role": "user", "parts": responses})
                continue
            return


# ---- module helpers -------------------------------------------------------
def _build_tools(tools: Optional[list[dict]], grounding: bool) -> list[dict]:
    """Translate shared tool schemas ({name, description, input_schema}) into a Gemini
    `functionDeclarations` tool, and (when grounding is on) add the `googleSearch` tool so the model
    can ground answers on live Google Search."""
    out: list[dict] = []
    decls = []
    for t in tools or []:
        name = (t or {}).get("name")
        if not name:
            continue
        d = {"name": name, "description": t.get("description", "")}
        params = t.get("input_schema")
        if params:
            d["parameters"] = params
        decls.append(d)
    if decls:
        out.append({"functionDeclarations": decls})
    if grounding:
        out.append({"googleSearch": {}})
    return out


def _usage_event(cfg: dict, model: str, usage: dict) -> dict:
    """Normalize a Gemini `usageMetadata` object to the provider-agnostic usage dict + $ estimate."""
    ev = {
        "model": model,
        "input_tokens": int(usage.get("promptTokenCount", 0) or 0),
        "output_tokens": int(usage.get("candidatesTokenCount", 0) or 0),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": int(usage.get("cachedContentTokenCount", 0) or 0),
    }
    ev["cost_usd"] = estimate_cost(model, ev, cfg.get("pricing", {}))
    return ev


def _stream_generate(base_url: str, model: str, key: str, body: dict,
                     cancel: threading.Event, *, timeout=(10, 600)) -> Iterator[dict]:  # noqa: ANN001
    """POST `models/{model}:streamGenerateContent?alt=sse` and yield each parsed SSE `data:` chunk.
    The key rides the `x-goog-api-key` header (never the URL). Raises RuntimeError on a non-200."""
    url = f"{base_url}/models/{model}:streamGenerateContent?alt=sse"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json", "User-Agent": _USER_AGENT}
    with requests.post(url, data=json.dumps(body), headers=headers,
                       stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Gemini LLM {r.status_code}: {r.text[:200]}")
        for line in r.iter_lines():
            if cancel.is_set():
                return
            if not line:
                continue
            s = line.decode("utf-8") if isinstance(line, bytes) else line
            if not s.startswith("data:"):
                continue
            data = s[5:].strip()
            if not data:
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
