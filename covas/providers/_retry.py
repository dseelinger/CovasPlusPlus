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

A THIRD, distinct outcome (issue #108): a non-transient failure whose status says the request
itself was broken — a bad/missing key, a nonexistent model, a validation error — is not a "bad
minute" (retrying it is pointless, `run_with_retry` already fails it fast) but IS the Commander's
to fix. :func:`is_config_error` classifies that case and :func:`config_hint` names the likely knob
(key vs. model), so the app can speak a "check your settings" heads-up instead of staying silent.
"""
from __future__ import annotations

import random
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

# HTTP statuses worth retrying: rate-limit (429), the transient 5xx family, and Anthropic's
# 529 "Overloaded". Everything else — 400/401/403/404 and other 4xx auth/validation errors —
# fails fast (retrying a bad key or a nonexistent model just wastes the Commander's time).
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 529})

# HTTP statuses that mean the REQUEST itself is broken — a bad/missing key, a nonexistent model, or
# an invalid request body — as opposed to a server having a bad minute. These fail fast (above) AND
# are user-fixable, so they earn a spoken "check your settings" heads-up instead of the silent
# cue+log fallback (issue #108).
CONFIG_STATUS = frozenset({400, 401, 403, 404, 422})

T = TypeVar("T")


class TransientError(Exception):
    """Raised BY a provider's connect step to feed a known-transient failure into the retry loop
    (a retryable HTTP status, optionally carrying the server's `Retry-After`). Distinct from a
    hard :class:`RuntimeError` so :func:`run_with_retry` retries it without re-parsing status."""

    def __init__(self, message: str, *, status: int | None = None,
                 retry_after: float | None = None, provider: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.provider = provider


class ProviderError(RuntimeError):
    """The final outcome of a provider call the retry loop couldn't rescue. `retryable=True` means
    it was a transient failure we exhausted (overloaded/timeout) — the app speaks the degraded
    line; `retryable=False` is a fail-fast/cancelled outcome. Carries the provider name, last HTTP
    status, and attempt count so the log reason is precise."""

    def __init__(self, message: str, *, provider: str = "", status: int | None = None,
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
    def from_cfg(cls, cfg: dict) -> RetryPolicy:
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

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Backoff before the next try. Honor a server `Retry-After` verbatim (capped at the
        total budget); otherwise exponential-with-jitter off :meth:`base_exp`."""
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, self.total_cap)
        return self.base_exp(attempt) * (1.0 + random.random() * self.jitter)


def parse_retry_after(value) -> float | None:  # noqa: ANN001 — header value: str | None
    """Parse a `Retry-After` header. Only the delta-seconds form is honored (the HTTP-date form
    is rare for these APIs and not worth a dependency); anything unparseable -> None."""
    if value is None:
        return None
    try:
        secs = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return max(0.0, secs)


def is_retryable_status(code: int | None) -> bool:
    """True iff an HTTP status is worth retrying (see :data:`RETRYABLE_STATUS`)."""
    return code in RETRYABLE_STATUS


def _status_of(exc: BaseException) -> int | None:
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
    on_retry: Callable[[int, float, BaseException], None] | None = None,
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


def is_config_error(err: BaseException) -> bool:
    """Should the Commander hear a 'check your settings' heads-up for this error (issue #108)? True
    for a fail-fast :class:`ProviderError` (retryable=False) whose status is a config-shaped 4xx, or
    for a raw exception carrying that same status (e.g. the Anthropic SDK's ``APIStatusError``).

    Deliberately narrow: an UNCLASSIFIED exception (no status at all — a tool bug, a code crash)
    returns False here, same as it does for :func:`is_degraded_error`, so it keeps today's silent
    cue+log behaviour instead of misdiagnosing a bug as a settings problem."""
    if isinstance(err, ProviderError):
        return (not err.retryable) and (err.status in CONFIG_STATUS)
    status = _status_of(err)  # catches the Anthropic SDK's APIStatusError.status_code
    return status in CONFIG_STATUS


def config_hint(err: BaseException) -> str:
    """Plain-language pointer to which setting is likely wrong, keyed off the HTTP status: 401/403
    -> the API key, 404 -> the model id, 400/422 -> a generic model/parameter nudge. Used to build
    the spoken misconfiguration line (issue #108); callers should only call this after
    :func:`is_config_error` returns True."""
    status = err.status if isinstance(err, ProviderError) else _status_of(err)
    if status in (401, 403):
        return "the API key looks wrong or missing"
    if status == 404:
        return "the model name looks wrong"
    return "the model or request settings look wrong"


def retry_event(provider: str, attempt: int, attempts: int, delay: float,
                exc: BaseException) -> dict:
    """Shape a per-retry descriptor for the app's ``on_event('retry', …)`` log channel (issue #97).

    Wired as the ``on_retry`` hook of :func:`run_with_retry`, this is what turns a silent backoff
    into a visible 'retrying, backing off Ns' line — so a transient blip that recovers is no longer
    just a mysterious pause before the reply. `attempt` is 1-based (the try that just failed);
    `attempts` is the policy's total budget; `delay` is the coming backoff in seconds."""
    status = _status_of(exc)
    return {"provider": provider, "attempt": int(attempt), "attempts": int(attempts),
            "delay": float(delay), "reason": (f"HTTP {status}" if status else type(exc).__name__)}


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
