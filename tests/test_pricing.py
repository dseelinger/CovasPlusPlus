"""Unit tests for the cost-instrumentation helpers in covas.llm.

Pure logic, offline: per-call cost estimation from token counts + the [pricing] table,
model-id matching (exact then prefix), usage-event normalization, and the cache_control
TTL construction that keeps system + tools cacheable across sporadic in-game turns.
"""
from __future__ import annotations

import types

from covas import llm
from covas.capabilities.checklist_capability import CHECKLIST_TOOLS

RATES = {
    "input": 1.0, "output": 5.0, "cache_write": 2.0, "cache_read": 0.10,
}
PRICING = {"claude-haiku-4-5": RATES}


def _usage(**kw) -> dict:
    base = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
    }
    base.update(kw)
    return base


# --- _rates_for ------------------------------------------------------------

def test_rates_for_exact_match():
    assert llm._rates_for("claude-haiku-4-5", PRICING) is RATES


def test_rates_for_prefix_match_covers_dated_id():
    # A bare 'claude-haiku-4-5' entry must cover the date-suffixed id.
    assert llm._rates_for("claude-haiku-4-5-20251001", PRICING) is RATES


def test_rates_for_unknown_model_is_none():
    assert llm._rates_for("gpt-4", PRICING) is None


# --- estimate_cost ---------------------------------------------------------

def test_cost_per_field_uses_matching_rate():
    # 1M of each token type * its rate / 1M == the rate in dollars.
    assert llm.estimate_cost("claude-haiku-4-5", _usage(input_tokens=1_000_000), PRICING) == 1.0
    assert llm.estimate_cost("claude-haiku-4-5", _usage(output_tokens=1_000_000), PRICING) == 5.0
    assert llm.estimate_cost(
        "claude-haiku-4-5", _usage(cache_creation_input_tokens=1_000_000), PRICING) == 2.0
    assert llm.estimate_cost(
        "claude-haiku-4-5", _usage(cache_read_input_tokens=1_000_000), PRICING) == 0.10


def test_cost_sums_all_fields():
    u = _usage(input_tokens=500_000, output_tokens=100_000,
               cache_creation_input_tokens=200_000, cache_read_input_tokens=1_000_000)
    # 0.5*1 + 0.1*5 + 0.2*2 + 1.0*0.1 = 0.5 + 0.5 + 0.4 + 0.1 = 1.5
    assert llm.estimate_cost("claude-haiku-4-5", u, PRICING) == 1.5


def test_cost_unknown_model_is_zero():
    assert llm.estimate_cost("mystery-model", _usage(input_tokens=1_000_000), PRICING) == 0.0


def test_cost_empty_pricing_is_zero():
    assert llm.estimate_cost("claude-haiku-4-5", _usage(input_tokens=1_000_000), {}) == 0.0


# --- usage_event -----------------------------------------------------------

def test_usage_event_reads_attrs_and_estimates_cost():
    usage = types.SimpleNamespace(
        input_tokens=1_000_000, output_tokens=0,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    ev = llm.usage_event({"pricing": PRICING}, "claude-haiku-4-5", usage)
    assert ev["model"] == "claude-haiku-4-5"
    assert ev["input_tokens"] == 1_000_000
    assert ev["cost_usd"] == 1.0


def test_usage_event_tolerates_missing_fields():
    # An older/odd usage object may lack the cache_* attrs -> treated as 0.
    usage = types.SimpleNamespace(input_tokens=10, output_tokens=20)
    ev = llm.usage_event({}, "claude-haiku-4-5", usage)
    assert ev["cache_creation_input_tokens"] == 0
    assert ev["cache_read_input_tokens"] == 0
    assert ev["cost_usd"] == 0.0  # no pricing -> 0


# --- _cache_control (TTL) --------------------------------------------------

def test_cache_control_1h_adds_ttl():
    assert llm._cache_control({"anthropic": {"cache_ttl": "1h"}}) == {
        "type": "ephemeral", "ttl": "1h"}


def test_cache_control_5m_omits_ttl():
    # The 5-minute cache is the API default — no ttl flag needed.
    assert llm._cache_control({"anthropic": {"cache_ttl": "5m"}}) == {"type": "ephemeral"}


def test_cache_control_defaults_to_1h():
    assert llm._cache_control({})["ttl"] == "1h"


# --- _build_kwargs still caches system + tools -----------------------------

def _cfg(tmp_path):
    sys_file = tmp_path / "personality.txt"
    sys_file.write_text("You are a test companion.", encoding="utf-8")
    return {
        "anthropic": {"model": "claude-sonnet-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "personality": {"enabled": True, "file": str(sys_file)},
        "web_search": {"enabled": True, "max_uses": 3},
        "checklist": {"file": "ultimate_checklist.md"},
    }


def test_system_block_is_cached_with_ttl(tmp_path):
    kwargs = llm._build_kwargs(_cfg(tmp_path), [{"role": "user", "content": "hi"}])
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_last_tool_is_cached_with_ttl(tmp_path):
    # Client tools now come from the capability registry — pass them in explicitly.
    kwargs = llm._build_kwargs(_cfg(tmp_path), [{"role": "user", "content": "hi"}],
                               CHECKLIST_TOOLS)
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Only the last tool carries the breakpoint (it caches everything before it).
    assert "cache_control" not in kwargs["tools"][0]
