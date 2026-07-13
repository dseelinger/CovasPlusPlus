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
        # Key resolution: ANTHROPIC_API_KEY env var first (dev), else the key file the
        # first-run wizard writes under data_dir. Pass it explicitly when found; otherwise let
        # the SDK read the env itself (and raise its own clear error if truly absent).
        from ..firstrun import anthropic_key
        key = anthropic_key(cfg)
        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

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
        yield from llm.stream_reply(
            self.client, self.cfg, messages, cancel, on_event,
            tool_handler=tool_handler, tools=tools,
            model=model, max_tokens=max_tokens,
        )
