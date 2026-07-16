"""Shared transient-error retry policy for the LLM providers (issue #97).

A cloud LLM has bad minutes: Anthropic 529 "Overloaded", OpenAI/Groq/OpenRouter 429s, Gemini
503s, plus plain connection/timeout blips. A single turn shouldn't die on one of those when a
short retry would sail through. This module is the ONE place every raw-`requests` provider
agrees on what's transient, how long to back off, and how to stay cancel-aware — keeping the
provider interfaces tiny (CLAUDE.md).

Scope + boundaries:
  * Retry applies to the INITIAL connect / before the first token only. Once tokens stream, a
    mid-stream drop can't be transparently retried without double-speaking, so it falls soft
    (the turn fails and the app's degraded path speaks the outcome) — see the providers.
  * The Anthropic SDK path retries internally (`max_retries`), so it is configured for parity
    rather than wrapped here — never double-retry the same request.
  * The loop watches the turn's existing `cancel` event and aborts backoff immediately, so a
    barge-in / tap-cancel is never blocked on a long sleep.

The app consumes two things after a provider gives up: :func:`is_degraded_error` (should the
Commander hear an in-character "service is overloaded" line?) and :func:`degraded_reason` (the
precise line to log). Both are provider-agnostic.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

# HTTP statuses worth retrying: rate-limit (429), the transient 5xx family, and Anthropic's
# 529 "Overloaded". Everything else — 400/401/403/404 and other 4xx auth/validation errors —
# fails fast (retrying a bad key or a nonexistent model just wastes the Commander's time).
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 529})

T = TypeVar("T")


class TransientError(Exception):
    """Raised BY a provider's connect step to feed a known-transient failure into the retry loop
    (a retryable HTTP status, optionally carrying the server's `Retry-After`). Distinct from a
    hard :class:`RuntimeError` so :func:`run_with_retry` retries it without re-parsing status."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 retry_after: Optional[float] = None, provider: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.provider = provider


class ProviderError(RuntimeError):
    """The final outcome of a provider call the retry loop couldn't rescue. `retryable=True` means
    it was a transient failure we exhausted (overloaded/timeout) — the app speaks the degraded
    line; `retryable=False` is a fail-fast/cancelled outcome. Carries the provider name, last HTTP
    status, and attempt count so the log reason is precise."""

    def __init__(self, message: str, *, provider: str = "", status: Optional[int] = None,
                 retryable: bool = False, attempts: int = 0) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.retryable = retryable
        self.attempts = attempts


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to retry an interactive turn. Small attempt count + a HARD total-wait cap keep it
    from ever feeling hung; jitter avoids thundering-herd retries against a struggling server."""

    attempts: int = 4          # total tries (1 initial + up to 3 retries)
    base: float = 0.5          # first backoff, seconds
    factor: float = 2.0        # exponential growth per attempt
    max_delay: float = 4.0     # per-sleep ceiling
    total_cap: float = 20.0    # hard cap on CUMULATIVE backoff for the whole turn
    jitter: float = 0.25       # add up to this fraction of the base delay, at random

    @classmethod
    def from_cfg(cls, cfg: dict) -> "RetryPolicy":
        """Build from `[llm.retry]` (all keys optional). `enabled = false` collapses to a single
        try (no retry) so an operator can turn the whole behaviour off."""
        r = ((cfg.get("llm", {}) or {}).get("retry", {}) or {})
        if not r.get("enabled", True):
            return cls(attempts=1)
        d = cls()
        return cls(
            attempts=max(1, int(r.get("attempts", d.attempts))),
            base=float(r.get("base_delay", d.base)),
            factor=float(r.get("factor", d.factor)),
            max_delay=float(r.get("max_delay", d.max_delay)),
            total_cap=float(r.get("max_total_wait", d.total_cap)),
            jitter=float(r.get("jitter", d.jitter)),
        )

    def base_exp(self, attempt: int) -> float:
        """The deterministic (jitter-free) backoff for a 1-based attempt, capped at max_delay —
        e.g. 0.5, 1, 2, 4, 4, … This is the schedule tests assert against."""
        return min(self.base * (self.factor ** (attempt - 1)), self.max_delay)

    def delay_for(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Backoff before the next try. Honor a server `Retry-After` verbatim (capped at the
        total budget); otherwise exponential-with-jitter off :meth:`base_exp`."""
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, self.total_cap)
        return self.base_exp(attempt) * (1.0 + random.random() * self.jitter)


def parse_retry_after(value) -> Optional[float]:  # noqa: ANN001 — header value: str | None
    """Parse a `Retry-After` header. Only the delta-seconds form is honored (the HTTP-date form
    is rare for these APIs and not worth a dependency); anything unparseable -> None."""
    if value is None:
        return None
    try:
        secs = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return max(0.0, secs)


def is_retryable_status(code: Optional[int]) -> bool:
    """True iff an HTTP status is worth retrying (see :data:`RETRYABLE_STATUS`)."""
    return code in RETRYABLE_STATUS


def _status_of(exc: BaseException) -> Optional[int]:
    """Best-effort HTTP status off an exception: our TransientError.status, or the SDK-style
    `.status_code` the Anthropic client puts on APIStatusError."""
    for attr in ("status", "status_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


def _is_conn_or_timeout(exc: BaseException) -> bool:
    """True for connection/timeout style failures, across `requests` and the Anthropic SDK. The
    SDK classes are matched by name so this module stays import-light (no hard anthropic dep)."""
    try:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError,
                            requests.exceptions.Timeout,
                            requests.exceptions.ChunkedEncodingError)):
            return True
    except Exception:  # noqa: BLE001 — requests is a hard dep, but never let this classify-crash
        pass
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__
    return ("Timeout" in name or "Connection" in name
            or name in {"APIConnectionError", "APITimeoutError", "OverloadedError"})


def _is_transient(exc: BaseException) -> bool:
    """The shared classifier every provider agrees on: an explicit TransientError, a retryable
    HTTP status carried on the exception, or a connection/timeout blip."""
    if isinstance(exc, ProviderError):
        return exc.retryable
    if isinstance(exc, TransientError):
        return True
    status = _status_of(exc)
    if status is not None:
        return is_retryable_status(status)
    return _is_conn_or_timeout(exc)


def sleep_cancellable(seconds: float, cancel: threading.Event) -> bool:
    """Sleep up to `seconds`, waking the instant `cancel` is set. Returns True if the full time
    elapsed, False if cancelled. Uses the event's own wait() so a barge-in never blocks on a long
    sleep (no polling loop)."""
    if seconds <= 0:
        return not cancel.is_set()
    # Event.wait returns True iff the event became set within the timeout.
    return not cancel.wait(seconds)


def run_with_retry(
    connect: Callable[[], T],
    cancel: threading.Event,
    policy: RetryPolicy,
    *,
    provider: str = "",
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
) -> T:
    """Call `connect()` and return its result, retrying TRANSIENT failures with exponential
    backoff + jitter — cancel-aware and total-wait-capped.

    A non-transient error (bad key, 404 model, validation) is re-raised immediately (fail fast).
    When retries or the total-wait budget are exhausted, a :class:`ProviderError` with
    `retryable=True` is raised so the caller/app can announce a degraded provider. If `cancel`
    fires, aborts at once with a non-retryable ProviderError. `on_retry(attempt, delay, exc)` is
    invoked before each backoff for logging (best-effort; its own errors are swallowed)."""
    total = 0.0
    attempt = 0
    while True:
        attempt += 1
        if cancel.is_set():
            raise ProviderError("cancelled before connect", provider=provider, retryable=False)
        try:
            return connect()
        except ProviderError:
            raise  # already classified upstream — don't wrap or reclassify
        except BaseException as exc:  # noqa: BLE001 — classify, then retry or re-raise
            if not _is_transient(exc):
                raise  # fail fast: propagate the original non-transient error
            status = _status_of(exc)
            # No attempts left, or the backoff budget is spent -> give up with a degraded marker.
            remaining = policy.total_cap - total
            if attempt >= policy.attempts or remaining <= 0:
                raise ProviderError(str(exc), provider=provider, status=status,
                                    retryable=True, attempts=attempt) from exc
            delay = min(policy.delay_for(attempt, getattr(exc, "retry_after", None)), remaining)
            if on_retry is not None:
                try:
                    on_retry(attempt, delay, exc)
                except Exception:  # noqa: BLE001 — logging must never break the retry
                    pass
            if not sleep_cancellable(delay, cancel):
                raise ProviderError("cancelled during backoff", provider=provider,
                                    status=status, retryable=False) from exc
            total += delay


def is_degraded_error(err: BaseException) -> bool:
    """Should the Commander hear an in-character 'the service is overloaded' line for this error?
    True for an exhausted-retry ProviderError and for any raw transient error (e.g. an Anthropic
    SDK 529/overloaded/connection error that its own retries couldn't rescue)."""
    if isinstance(err, ProviderError):
        return err.retryable
    return _is_transient(err)


def degraded_reason(err: BaseException) -> str:
    """A precise one-line reason for the log, e.g. 'Anthropic 529 Overloaded — retried 4×, giving
    up'. Always the real detail even when the spoken line is the friendly canned version."""
    if isinstance(err, ProviderError):
        parts = [p for p in (err.provider, str(err.status) if err.status else "", str(err).strip())
                 if p]
        base = " ".join(parts) if parts else "provider error"
        if err.attempts:
            base += f" — retried {err.attempts}×, giving up"
        return base
    status = _status_of(err)
    label = type(err).__name__
    tail = f" {status}" if status else ""
    return f"{label}{tail}: {err}".strip()
