"""Provider interfaces (structural typing via Protocol).

Keep these tiny — 1-2 methods each. The moment an interface grows
provider-specific parameters, the abstraction stops paying for itself.

Event stream contract shared by all LLM providers: stream_reply() yields
    ("text", chunk)      -> spoken/printed reply text
and calls on_event(kind, data) for side channels:
    on_event("thinking", delta)   -> reasoning summary (optional)
    on_event("search",   query)   -> a web search fired (cloud only)
    on_event("tool",     name)    -> a client tool was called
    on_event("usage",    dict)    -> per-call token counts + $ estimate (cloud only)
`data` is a str for every kind except "usage", which passes a dict — hence the
`object` payload type below. This lets app.py consume any provider identically.
"""
from __future__ import annotations

import threading
from typing import Callable, Iterator, Optional, Protocol, runtime_checkable

import numpy as np

# `data` is a str for text/thinking/search/tool and a dict for "usage".
OnEvent = Callable[[str, object], None]
ToolHandler = Callable[[str, dict], str]


@runtime_checkable
class STTProvider(Protocol):
    def transcribe(self, audio: np.ndarray) -> str:
        """Turn mono float32 audio into text (empty string if nothing heard)."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    def speak(self, text: str, cancel: threading.Event) -> None:
        """Synthesize and play `text`, stopping promptly if `cancel` is set."""

    def synth_pcm(self, text: str) -> tuple[bytes, int]:
        """Return (raw 16-bit mono PCM bytes, sample_rate). Used for caching."""


@runtime_checkable
class LLMProvider(Protocol):
    def stream_reply(
        self,
        messages: list[dict],
        cancel: threading.Event,
        on_event: OnEvent,
        tool_handler: Optional[ToolHandler] = None,
    ) -> Iterator[tuple[str, str]]:
        """Stream a reply for the given conversation. See module docstring."""
