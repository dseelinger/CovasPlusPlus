"""In-memory fake providers — satisfy the base.py Protocols with zero I/O.

Two consumers share these:
  - the dev-mode mock (config.mock_enabled), wired in via the factory, so the full
    push-to-talk loop can be exercised with no API calls and no cost;
  - unit tests, which inject them in place of the real providers.

They accept an optional `cfg` so the factory can build them the same way it builds
the real providers, and take keyword args so tests can script their output.
"""
from __future__ import annotations

import threading
from typing import Iterator, Optional


class FakeSTT:
    """STTProvider: returns canned text without touching audio hardware or Whisper."""

    def __init__(self, cfg: dict | None = None, *, text: str = "test transcription") -> None:
        self._text = text

    def transcribe(self, audio) -> str:  # noqa: ANN001 — mirrors the real signature
        return self._text


class FakeTTS:
    """TTSProvider: records what it was asked to say and plays nothing."""

    def __init__(self, cfg: dict | None = None) -> None:
        self.spoken: list[str] = []
        # Records the voice each synth_pcm was asked to use (None = default) — for tests
        # asserting a comms/alert line picked a non-COVAS voice.
        self.voices_seen: list[str | None] = []

    def speak(self, text: str, cancel: threading.Event) -> None:
        self.spoken.append(text)

    def synth_pcm(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        self.voices_seen.append(voice_id)
        return b"", 16000


class FakeLLM:
    """LLMProvider: yields scripted text (and optional pre-text side events) with no
    network call. `events` is a list of (kind, data) forwarded via on_event before the
    text — handy for tests that assert on 'search'/'tool'/'usage' handling."""

    def __init__(
        self,
        cfg: dict | None = None,
        *,
        text: str = "This is a mock reply.",
        events: Optional[list[tuple[str, object]]] = None,
    ) -> None:
        self._text = text
        self._events = list(events or [])
        # Records the router's per-turn choice (model, max_tokens) for test assertions.
        self.model_seen: Optional[str] = None
        self.max_tokens_seen: Optional[int] = None

    def stream_reply(
        self,
        messages: list[dict],
        cancel: threading.Event,
        on_event,  # noqa: ANN001 — OnEvent
        tool_handler=None,  # noqa: ANN001 — ToolHandler, accepted for parity
        tools=None,  # noqa: ANN001 — client tool schemas, accepted for parity
        model=None,  # noqa: ANN001 — cost router's per-turn model, recorded below
        max_tokens=None,  # noqa: ANN001 — cost router's per-turn cap, recorded below
    ) -> Iterator[tuple[str, str]]:
        self.model_seen = model
        self.max_tokens_seen = max_tokens
        for kind, data in self._events:
            if cancel.is_set():
                return
            on_event(kind, data)
        if cancel.is_set():
            return
        yield ("text", self._text)
