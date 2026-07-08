"""Cloud LLM provider — wraps the existing Anthropic streaming path so it
satisfies LLMProvider. No behavior change; this is just the seam."""
from __future__ import annotations

import threading
from typing import Iterator, Optional

import anthropic

from .. import llm
from .base import OnEvent, ToolHandler


class AnthropicLLM:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def stream_reply(
        self,
        messages: list[dict],
        cancel: threading.Event,
        on_event: OnEvent,
        tool_handler: Optional[ToolHandler] = None,
        tools: Optional[list[dict]] = None,
    ) -> Iterator[tuple[str, str]]:
        yield from llm.stream_reply(
            self.client, self.cfg, messages, cancel, on_event,
            tool_handler=tool_handler, tools=tools,
        )
