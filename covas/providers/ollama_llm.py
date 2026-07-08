"""Local LLM provider — Qwen (or any model) served by Ollama.

Talks to Ollama's HTTP API (/api/chat, streaming NDJSON) with plain `requests`,
so no extra dependency. Reuses the same personality system prompt as the cloud
path via llm.build_system, and normalizes output to the shared event contract in
base.py so app.py can consume it identically to the Anthropic provider.

Thinking models (e.g. Qwen3) may emit reasoning either as a separate `thinking`
field or inline in <think>...</think> tags — both are routed to on_event
("thinking", ...) and kept OUT of the spoken text.

NOTE (POC scope): client-side tool calling (the checklist tools) is not wired
into the local path yet — local models format tool JSON less reliably, so that
needs its own validate/repair loop. Tracked as a follow-up; conversation works
today. `tool_handler` is accepted for interface parity and currently ignored.
"""
from __future__ import annotations

import json
import threading
from typing import Iterator, Optional

import requests

from ..llm import build_system
from .base import OnEvent, ToolHandler


class OllamaLLM:
    def __init__(self, cfg: dict) -> None:
        o = cfg.get("ollama", {})
        self.host = str(o.get("host", "http://localhost:11434")).rstrip("/")
        self.model = str(o.get("model", "qwen3"))
        self.options = {"temperature": float(o.get("temperature", 0.7))}
        self.think = bool(o.get("think", True))
        self._system = build_system(cfg)  # personality.txt, or None if OFF

    # -- connectivity check, used by the POC runner for a friendly error --------
    def ping(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            if names and not _model_available(self.model, names):
                return False, (f"Model '{self.model}' not found in Ollama. Available: "
                               f"{', '.join(names) or '(none)'}. Pull it with "
                               f"`ollama pull {self.model}`.")
            return True, f"Ollama up at {self.host}; model '{self.model}' ready."
        except Exception as e:  # noqa: BLE001
            return False, (f"Can't reach Ollama at {self.host} ({e}). Is `ollama serve` "
                           f"running?")

    def _messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        if self._system:
            out.append({"role": "system", "content": self._system})
        for m in messages:
            content = m["content"]
            if isinstance(content, str):  # local path uses plain-text turns only
                out.append({"role": m["role"], "content": content})
        return out

    def stream_reply(
        self,
        messages: list[dict],
        cancel: threading.Event,
        on_event: OnEvent,
        tool_handler: Optional[ToolHandler] = None,
    ) -> Iterator[tuple[str, str]]:
        payload = {
            "model": self.model,
            "messages": self._messages(messages),
            "stream": True,
            "think": self.think,
            "options": self.options,
        }
        in_think = False  # tracks inline <think> tag state
        pending = ""      # trailing fragment that may be the start of a split tag
        try:
            with requests.post(
                f"{self.host}/api/chat", json=payload, stream=True, timeout=(10, 600)
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if cancel.is_set():
                        return
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg = obj.get("message", {}) or {}

                    # Separate reasoning field (newer Ollama) -> thinking channel.
                    thinking = msg.get("thinking")
                    if thinking:
                        on_event("thinking", thinking)

                    text = msg.get("content", "")
                    if text:
                        # Fallback: strip inline <think>...</think> to the channel.
                        # `pending` carries a half-arrived tag across the chunk seam.
                        combined, pending = pending + text, ""
                        for kind, piece in _split_think(combined, in_think):
                            if kind == "state":
                                in_think = piece  # bool
                            elif kind == "pending":
                                pending = piece
                            elif kind == "thinking":
                                on_event("thinking", piece)
                            else:
                                yield ("text", piece)

                    if obj.get("done"):
                        break
            # Flush any dangling partial tag that never completed.
            if pending:
                if in_think:
                    on_event("thinking", pending)
                else:
                    yield ("text", pending)
        except requests.RequestException as e:
            raise RuntimeError(f"Ollama request failed: {e}") from e


_OPEN, _CLOSE = "<think>", "</think>"


def _model_available(model: str, names: list[str]) -> bool:
    """Whether Ollama would resolve `model` against the installed `names`, mirroring
    Ollama's real rule: an exact tag match, or a bare name resolving to '<name>:latest'.
    A loose substring/startswith check wrongly reported e.g. 'qwen3' as ready when only
    'qwen3.6:latest' was installed, then /api/chat 404'd — this is the precise version."""
    if model in names:
        return True
    if ":" not in model:  # bare name -> Ollama resolves it to the ':latest' tag only
        return f"{model}:latest" in names
    return False


def _partial_tag_len(text: str, tag: str) -> int:
    """Length of the longest suffix of `text` that is a *proper* (non-empty, non-full)
    prefix of `tag` — i.e. how much trailing text might be the start of `tag` still
    arriving. A full match is not proper (0), so a completed tag isn't held back."""
    for n in range(min(len(text), len(tag) - 1), 0, -1):
        if text[-n:] == tag[:n]:
            return n
    return 0


def _split_think(text: str, in_think: bool):
    """Yield ('text'|'thinking', str), ('state', bool), or ('pending', str) splitting on
    <think> tags. Best-effort for models that inline reasoning rather than using the
    field. A trailing fragment that could be the start of a tag split across a streamed
    chunk boundary is emitted as ('pending', frag) so the caller can prepend it to the
    next chunk instead of leaking a bare '<thi' into speech."""
    i = 0
    while i < len(text):
        if not in_think:
            start = text.find(_OPEN, i)
            if start == -1:
                rest = text[i:]
                p = _partial_tag_len(rest, _OPEN)
                if p:
                    if len(rest) > p:
                        yield ("text", rest[:-p])
                    yield ("pending", rest[-p:])
                elif rest:
                    yield ("text", rest)
                return
            if start > i:
                yield ("text", text[i:start])
            in_think = True
            yield ("state", True)
            i = start + len(_OPEN)
        else:
            end = text.find(_CLOSE, i)
            if end == -1:
                rest = text[i:]
                p = _partial_tag_len(rest, _CLOSE)
                if p:
                    if len(rest) > p:
                        yield ("thinking", rest[:-p])
                    yield ("pending", rest[-p:])
                elif rest:
                    yield ("thinking", rest)
                return
            if end > i:
                yield ("thinking", text[i:end])
            in_think = False
            yield ("state", False)
            i = end + len(_CLOSE)
