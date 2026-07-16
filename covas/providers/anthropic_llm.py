"""Cloud LLM provider — wraps the existing Anthropic streaming path so it
satisfies LLMProvider. No behavior change; this is just the seam."""
from __future__ import annotations

import threading
from typing import Iterator, Optional

import anthropic

from .. import llm
from ._retry import RetryPolicy
from .base import OnEvent, ToolHandler


class AnthropicLLM:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        # Transient-error handling (issue #97): the Anthropic SDK already retries 429/5xx/overloaded
        # (incl. 529) internally with its own backoff + Retry-After honoring. For parity with the
        # raw-`requests` providers we DRIVE that from the SAME `[llm.retry]` policy instead of
        # wrapping the stream in the shared helper — wrapping would DOUBLE-retry the request. The
        # SDK's max_retries counts retries (not total tries), so it's attempts-1.
        # STREAMING BOUNDARY: the SDK's retries cover the initial connect / before the first token
        # only. Once tokens stream, a mid-stream drop cannot be transparently retried without
        # double-speaking, so it falls soft (the turn fails and app.py's degraded path speaks the
        # outcome) — this matches the raw providers' behavior.
        policy = RetryPolicy.from_cfg(cfg)
        max_retries = max(0, policy.attempts - 1)
        # Key resolution: the DPAPI-encrypted key file the first-run wizard writes under data_dir
        # (file-only since #22 — no env-var read). Pass it explicitly when found; otherwise fall
        # through to the bare SDK client. The SDK will read its own ANTHROPIC_API_KEY as a last
        # resort, but that's moot in practice: `is_configured` gates the app on a FILE key, so an
        # env-only setup never gets this far — the wizard runs first.
        from ..firstrun import anthropic_key
        key = anthropic_key(cfg)
        self.client = (anthropic.Anthropic(api_key=key, max_retries=max_retries) if key
                       else anthropic.Anthropic(max_retries=max_retries))

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
