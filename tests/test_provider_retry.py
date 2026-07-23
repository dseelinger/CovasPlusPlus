"""Unit tests for the shared transient-error retry policy (issue #97).

Offline and free: no network, no sleeps beyond sub-millisecond backoffs on tiny test policies.
Covers the classifier (which statuses/errors are transient), the backoff schedule + total-wait
cap, cancel-abort, fail-fast on non-transient errors, and the degraded-signal helpers the app
uses to decide whether to speak an "overloaded" line.
"""
from __future__ import annotations

import threading

import pytest

from covas.providers._retry import (
    CONFIG_STATUS,
    ProviderError,
    RetryPolicy,
    TransientError,
    config_hint,
    degraded_reason,
    is_config_error,
    is_degraded_error,
    is_retryable_status,
    parse_retry_after,
    run_with_retry,
    sleep_cancellable,
)


# ---- classifier -----------------------------------------------------------
@pytest.mark.parametrize("code", [429, 500, 502, 503, 529])
def test_retryable_statuses(code):
    assert is_retryable_status(code) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 418, 422, 200, None])
def test_non_retryable_statuses(code):
    assert is_retryable_status(code) is False


def test_parse_retry_after():
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("not-a-number") is None
    assert parse_retry_after("-5") == 0.0        # clamped to non-negative


# ---- backoff schedule + policy --------------------------------------------
def test_base_exp_schedule_is_exponential_and_capped():
    p = RetryPolicy(base=0.5, factor=2.0, max_delay=4.0)
    assert [p.base_exp(n) for n in range(1, 7)] == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]


def test_delay_for_honors_retry_after_over_backoff():
    p = RetryPolicy(base=0.5, total_cap=20.0)
    assert p.delay_for(1, retry_after=7.0) == 7.0          # server hint wins verbatim
    assert p.delay_for(1, retry_after=99.0) == 20.0        # but never past the total budget


def test_delay_for_jitter_stays_within_bounds():
    p = RetryPolicy(base=1.0, factor=1.0, max_delay=1.0, jitter=0.25)
    for _ in range(50):
        d = p.delay_for(1)
        assert 1.0 <= d <= 1.25                            # base .. base*(1+jitter)


def test_from_cfg_reads_knobs_and_disable():
    p = RetryPolicy.from_cfg({"llm": {"retry": {"attempts": 6, "base_delay": 0.2,
                                                "max_total_wait": 9.0}}})
    assert p.attempts == 6 and p.base == 0.2 and p.total_cap == 9.0
    off = RetryPolicy.from_cfg({"llm": {"retry": {"enabled": False}}})
    assert off.attempts == 1                                # disabled -> single try


# ---- sleep_cancellable ----------------------------------------------------
def test_sleep_cancellable_aborts_immediately_when_set():
    cancel = threading.Event()
    cancel.set()
    assert sleep_cancellable(10.0, cancel) is False         # returns at once, not after 10s


def test_sleep_cancellable_elapses_when_not_cancelled():
    assert sleep_cancellable(0.01, threading.Event()) is True


# ---- run_with_retry -------------------------------------------------------
def _tiny(**kw) -> RetryPolicy:
    """A policy whose sleeps are microscopic so tests don't actually wait."""
    base = dict(attempts=3, base=0.001, factor=1.0, max_delay=0.001, total_cap=1.0, jitter=0.0)
    base.update(kw)
    return RetryPolicy(**base)


def test_retries_transient_then_succeeds():
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("503", status=503)
        return "ok"

    assert run_with_retry(connect, threading.Event(), _tiny()) == "ok"
    assert calls["n"] == 3


def test_on_retry_fires_once_per_backoff_with_attempt_and_delay():
    # The providers rely on this hook to surface a backoff to the log (issue #97): it must fire
    # before each retry (not after the final success/failure) with the 1-based attempt + delay.
    seen = []
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("503", status=503)
        return "ok"

    def on_retry(attempt, delay, exc):
        seen.append((attempt, delay, exc.status))

    run_with_retry(connect, threading.Event(), _tiny(), on_retry=on_retry)
    assert [a for a, _d, _s in seen] == [1, 2]        # two retries, 1-based, none after success
    assert all(d >= 0 for _a, d, _s in seen) and all(s == 503 for *_x, s in seen)


def test_retry_event_shape():
    from covas.providers._retry import retry_event
    ev = retry_event("OpenAI", 2, 4, 1.0, TransientError("boom", status=503))
    assert ev == {"provider": "OpenAI", "attempt": 2, "attempts": 4,
                  "delay": 1.0, "reason": "HTTP 503"}
    # No status on the exception -> fall back to the class name, still a usable log reason.
    assert retry_event("Gemini", 1, 3, 0.5, ConnectionError())["reason"] == "ConnectionError"


def test_exhausts_retries_and_marks_degraded():
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        raise TransientError("529 Overloaded", status=529, provider="Anthropic")

    with pytest.raises(ProviderError) as ei:
        run_with_retry(connect, threading.Event(), _tiny(attempts=3), provider="Anthropic")
    err = ei.value
    assert calls["n"] == 3                        # 1 initial + 2 retries
    assert err.retryable is True and err.attempts == 3 and err.status == 529


def test_total_wait_cap_stops_early():
    """A cap below the full backoff budget cuts retries short even with attempts to spare."""
    calls = {"n": 0}
    # constant 0.005s backoff, cap 0.012 -> sleeps at .005, .005, .002 then the budget is spent.
    policy = RetryPolicy(attempts=10, base=0.005, factor=1.0, max_delay=0.01,
                         total_cap=0.012, jitter=0.0)

    def connect():
        calls["n"] += 1
        raise TransientError("500", status=500)

    with pytest.raises(ProviderError):
        run_with_retry(connect, threading.Event(), policy)
    assert calls["n"] == 4                        # stopped by the cap, not the attempt count (10)


def test_cancel_aborts_backoff():
    calls = {"n": 0}
    cancel = threading.Event()

    def connect():
        calls["n"] += 1
        cancel.set()                               # simulate a barge-in during the attempt
        raise TransientError("503", status=503)

    with pytest.raises(ProviderError) as ei:
        run_with_retry(connect, cancel, RetryPolicy(attempts=5, base=10.0, total_cap=100.0))
    assert calls["n"] == 1                          # no second attempt; no 10s sleep
    assert ei.value.retryable is False             # cancelled, not exhausted


def test_fails_fast_on_non_transient():
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        raise RuntimeError("404 model not found")

    with pytest.raises(RuntimeError) as ei:
        run_with_retry(connect, threading.Event(), _tiny())
    assert calls["n"] == 1                          # no retry on a 4xx
    assert not isinstance(ei.value, ProviderError)  # original error propagated untouched


def test_provider_error_from_connect_propagates_untouched_no_retry():
    """A provider that raises its own fail-fast ProviderError (issue #108: the structured
    non-200/non-retryable outcome each raw provider now raises) must propagate AS-IS — no retry, no
    reclassification — per `run_with_retry`'s 'already classified upstream' contract."""
    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        raise ProviderError("Gemini LLM 404: model not found", provider="Gemini",
                            status=404, retryable=False)

    with pytest.raises(ProviderError) as ei:
        run_with_retry(connect, threading.Event(), _tiny())
    assert calls["n"] == 1                          # no retry on a fail-fast ProviderError
    assert ei.value.status == 404 and ei.value.retryable is False


def test_status_carrying_404_is_not_retried():
    class _Boom(Exception):
        status_code = 404

    calls = {"n": 0}

    def connect():
        calls["n"] += 1
        raise _Boom()

    with pytest.raises(_Boom):
        run_with_retry(connect, threading.Event(), _tiny())
    assert calls["n"] == 1


# ---- degraded-signal helpers (consumed by app.py) -------------------------
def test_is_degraded_error_for_exhausted_and_transient():
    assert is_degraded_error(ProviderError("x", retryable=True))
    assert not is_degraded_error(ProviderError("x", retryable=False))

    class _Overloaded(Exception):
        status_code = 529

    assert is_degraded_error(_Overloaded())           # a raw transient (e.g. Anthropic SDK 529)
    assert not is_degraded_error(RuntimeError("bad api key"))  # no status, not a conn error


def test_degraded_reason_is_precise():
    err = ProviderError("Overloaded", provider="Anthropic", status=529,
                        retryable=True, attempts=4)
    reason = degraded_reason(err)
    assert "Anthropic" in reason and "529" in reason and "retried 4×, giving up" in reason


# ---- misconfiguration classifier (issue #108) ------------------------------
@pytest.mark.parametrize("code", sorted(CONFIG_STATUS))
def test_is_config_error_true_for_fail_fast_provider_error(code):
    """Every config-shaped status, carried on a fail-fast (retryable=False) ProviderError, classifies."""
    assert is_config_error(ProviderError("x", status=code, retryable=False)) is True


def test_is_config_error_false_for_retryable_provider_error():
    """A RETRYABLE ProviderError never classifies as config, even if it happens to carry a
    config-shaped status (a status set by an exhausted-retry outcome, not a fail-fast one) — the two
    branches (#97 degraded vs #108 misconfig) are mutually exclusive by construction."""
    assert is_config_error(ProviderError("x", status=404, retryable=True)) is False


@pytest.mark.parametrize("code", [429, 500, 502, 503, 529, 200, 418, None])
def test_is_config_error_false_for_non_config_statuses(code):
    assert is_config_error(ProviderError("x", status=code, retryable=False)) is False


def test_is_config_error_true_for_raw_status_carrying_exception():
    """The Anthropic SDK path (APIStatusError.status_code) never wraps in ProviderError — the
    classifier must still work off the bare status attribute."""
    class _Boom(Exception):
        status_code = 401
    assert is_config_error(_Boom()) is True


def test_is_config_error_false_for_unclassified_exception():
    """No status at all (a tool bug, a code crash) must NOT classify as config — misdiagnosing a bug
    as a settings problem would send the Commander on a wild goose chase."""
    assert is_config_error(RuntimeError("boom")) is False


def test_config_hint_names_key_for_401_and_403():
    assert "key" in config_hint(ProviderError("x", status=401, retryable=False))
    assert "key" in config_hint(ProviderError("x", status=403, retryable=False))


def test_config_hint_names_model_for_404():
    assert "model" in config_hint(ProviderError("x", status=404, retryable=False))


def test_config_hint_generic_for_400_and_422():
    for code in (400, 422):
        hint = config_hint(ProviderError("x", status=code, retryable=False))
        assert "key" not in hint and hint  # a non-empty, non-key-specific nudge


def test_anthropic_keyless_raises_structured_config_error(monkeypatch):
    """`llm.list_anthropic_models` (the keyless case named in issue #108) must raise a structured,
    401-shaped ProviderError, not a bare RuntimeError, so it classifies the same as every other
    provider's missing-key case."""
    from covas import llm as llm_mod
    monkeypatch.setattr("covas.firstrun.anthropic_key", lambda cfg: None)
    with pytest.raises(ProviderError) as ei:
        llm_mod.list_anthropic_models({})
    assert ei.value.status == 401 and ei.value.provider == "Anthropic" and is_config_error(ei.value)
