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

Adding a new LLM provider (issue #11 — the plumbing is provider-agnostic). A provider
NORMALIZES its native API onto this one contract so `app.py` never special-cases it:
  * **Tiering is a parameter, not a provider.** `model`/`max_tokens` are the cost router's
    per-turn choice; the router picks a canonical tier (cheap/standard/premium) and the
    provider's own `[<provider>].tiers` map turns that into a concrete model id (see
    `router._provider_tiers`). One `stream_reply`, any tier.
  * **Tool calling** — translate the shared `tools` JSON-Schema list into the provider's
    native tool format, run calls via `tool_handler(name, args) -> str`, and feed results back.
  * **Streaming text/thinking** — yield ("text", …) for spoken output; route any reasoning to
    on_event("thinking", …) so it's kept OUT of the spoken text (never yield it as "text").
  * **Usage/$ accounting** — after the call, emit on_event("usage", dict) with the provider-
    agnostic shape: {model, input_tokens, output_tokens, cache_creation_input_tokens,
    cache_read_input_tokens, cost_usd}. Reuse `llm.estimate_cost(model, usage, pricing)` for the
    dollar figure so every provider costs out of the same `[pricing]` table.
  * **Cancellation** — check the `cancel` Event between chunks and stop promptly (barge-in).
  * **Fail soft** — a provider/tool error must not crash the loop; the app already guards, but
    don't swallow so much that a misconfig is undiagnosable.

In-game policy (issue #11): every LLM here is a CLOUD provider (Anthropic today; OpenAI/Gemini too)
and is fine on the in-game path — the router tiers it for cost. Cost is handled by that cloud
tiering, NOT by a local LLM: a useful local model fights Elite Dangerous for the GPU, so COVAS++
runs no local LLM at all (issue #128). The only local ML is CPU-side (Piper TTS, Whisper STT), which
doesn't contend with the game's rendering.
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

    def synth_pcm(self, text: str, voice_id: Optional[str] = None) -> tuple[bytes, int]:
        """Return (raw 16-bit mono PCM bytes, sample_rate). Used for cached status lines and
        for rendering a line to a chosen BUS/VOICE via the mixer (C1). `voice_id=None` uses
        the provider's configured voice; providers with a single voice ignore it."""


@runtime_checkable
class LLMProvider(Protocol):
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
        """Stream a reply for the given conversation. `tools` are the client-side
        tool schemas the LLM may call (from the capability registry); `tool_handler`
        runs them. `model`/`max_tokens` are the cost router's per-turn choice
        (DESIGN §4) — the tier is a *parameter*, not a provider-per-model; None means
        use the provider's configured default. See module docstring."""
