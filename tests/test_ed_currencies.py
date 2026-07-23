"""Unit tests for the grounded wallet + currency registry + honesty guardrail (issue #101).

All offline/free: registry extraction, wallet folding via the journal, the hedged status line,
the context detector's registry-driven money phrases, and the presence of the static currency
guardrail in the composed system prompt. No network, API, or ED — pure logic against dicts.
"""
from __future__ import annotations


from covas.ed import EDContext
from covas.ed import currencies
from covas.ed.currencies import (Currency, extract_balances, known_names, wallet_line)
from covas.ed.detector import ContextDetector, ContextDetectorConfig
from covas.ed.journal import apply_journal_event


# --- registry extraction ---------------------------------------------------

def test_extract_credits_from_load_game():
    bal = extract_balances({"event": "LoadGame", "Ship": "anaconda", "Credits": 1234567})
    assert bal == {"credits": 1234567}


def test_extract_carrier_balance_from_nested_finance():
    # Finance.CarrierBalance is a dotted path into the CarrierStats payload.
    ev = {"event": "CarrierStats", "CarrierID": 3700005, "Name": "Nomad",
          "Finance": {"CarrierBalance": 9876543, "ReserveBalance": 100}}
    assert extract_balances(ev) == {"carrier_balance": 9876543}


def test_extract_ignores_unmatched_event():
    # A different event carrying an integer must not be mistaken for a balance.
    assert extract_balances({"event": "Cargo", "Count": 128}) == {}


def test_extract_ignores_missing_field():
    assert extract_balances({"event": "LoadGame", "Ship": "anaconda"}) == {}


def test_extract_rejects_bool_masquerading_as_amount():
    # bool is an int subclass; a stray flag named like the field must not become a balance.
    assert extract_balances({"event": "LoadGame", "Credits": True}) == {}


def test_unknown_currency_is_invisible_to_the_wallet():
    # The "merc coins" case: a new event/field FDev adds has no registry row, so nothing is
    # extracted — the wallet never learns a balance it can't ground. This IS the degradation.
    assert extract_balances({"event": "MercCoinsEarned", "MercCoins": 250}) == {}
    assert extract_balances({"event": "LoadGame", "MercCoins": 250}) == {}


# --- wallet folding through the journal ------------------------------------

def test_load_game_folds_credits_into_wallet():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "LoadGame", "Ship": "anaconda",
                              "ShipName": "Void Runner", "FuelLevel": 24.0,
                              "FuelCapacity": 32.0, "Credits": 500000000})
    assert ctx.wallet_snapshot() == {"credits": 500000000}
    # The ship/fuel context patch still applies alongside the wallet fold.
    assert ctx.snapshot()["ship"] == "Anaconda"


def test_carrier_stats_folds_carrier_balance_and_merges_with_credits():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "LoadGame", "Credits": 1000})
    apply_journal_event(ctx, {"event": "CarrierStats", "CarrierID": 1,
                              "Finance": {"CarrierBalance": 42}})
    # Merge, not replace: credits survive when the carrier balance arrives later.
    assert ctx.wallet_snapshot() == {"credits": 1000, "carrier_balance": 42}


# --- hedged status line ----------------------------------------------------

def test_wallet_line_hedges_and_groups():
    line = wallet_line({"credits": 1234567})
    assert line == "as of login you had 1,234,567 credits"


def test_wallet_line_groups_per_active_locale():
    """The callout routes through the locale formatter (#199): German groups with '.', not ','."""
    from covas import i18n
    try:
        i18n.set_active_language_code("de")
        assert wallet_line({"credits": 1234567}) == "as of login you had 1.234.567 credits"
    finally:
        i18n.set_active_language_code(None)
    # Back to the English default -> byte-identical to the assertion above.
    assert wallet_line({"credits": 1234567}) == "as of login you had 1,234,567 credits"


def test_wallet_line_both_currencies():
    line = wallet_line({"credits": 1000, "carrier_balance": 2000})
    assert "1,000 credits" in line and "2,000 credits as of login" in line


def test_wallet_line_none_when_empty():
    assert wallet_line({}) is None


def test_wallet_line_ignores_keys_without_a_registry_row():
    # A key with no registry row (an unknown currency) can never be voiced by the wallet.
    assert wallet_line({"merc_coins": 250}) is None


def test_summary_includes_hedged_wallet_line():
    ctx = EDContext()
    apply_journal_event(ctx, {"event": "LoadGame", "Ship": "sidewinder", "Credits": 777})
    summary = ctx.summary()
    assert summary is not None and "as of login you had 777 credits" in summary


def test_summary_returns_wallet_even_with_no_other_state():
    # A wallet-only context (balance known, nothing else) still summarizes — the wallet counts
    # as "something known", so the honesty path can surface a grounded balance.
    ctx = EDContext()
    ctx.update_wallet(credits=999)
    assert ctx.summary() and "999 credits" in ctx.summary()


# --- context detector: registry-driven money phrases -----------------------

def test_detector_matches_known_currency_questions():
    det = ContextDetector.from_cfg({})
    for q in ("how many credits do I have", "what's my balance", "how much money do I have",
              "what's my carrier balance"):
        assert det.decide(q).matched, q


def test_detector_does_not_match_unknown_currency():
    # "merc coins" is not a known currency name, so it must NOT trip a status lookup — it degrades
    # to a normal turn where the LLM guardrail makes the model answer honestly.
    det = ContextDetector.from_cfg({})
    assert not det.decide("how many merc coins do I have").matched


def test_detector_status_phrases_include_registry_names():
    cfg = ContextDetectorConfig()
    for name in known_names():
        assert name in cfg.status_phrases


# --- unknown-currency honesty with a FAKE registry -------------------------

def test_fake_registry_drives_known_vs_unknown(monkeypatch):
    """Inject a one-row fake registry and assert the whole known/unknown contract follows the
    data: the fake currency becomes extractable + voiceable + detector-matched, while a currency
    absent from the fake registry (here: real credits) degrades to invisible/unmatched."""
    fake = (Currency(key="merc_coins", event="MercCoinsEarned", field="Total",
                     display="merc coins", phrasing="you hold {amount} merc coins",
                     names=("merc coins", "my merc coins")),)
    monkeypatch.setattr(currencies, "REGISTRY", fake)

    # Now the once-unknown currency is grounded...
    assert extract_balances({"event": "MercCoinsEarned", "Total": 250}) == {"merc_coins": 250}
    assert wallet_line({"merc_coins": 250}) == "you hold 250 merc coins"
    assert "merc coins" in known_names()
    # ...and credits, absent from the fake registry, are the ones that degrade to silence.
    assert extract_balances({"event": "LoadGame", "Credits": 1000}) == {}
    assert wallet_line({"credits": 1000}) is None


# --- guardrail present in the composed system prompt -----------------------

def test_currency_guardrail_in_build_system():
    # Mirror the ship-spec guardrail test in test_crew: the static currency guardrail must be in
    # build_system's output even with personality + crew OFF, and stay identical (cache-safe).
    from covas.llm import build_system, _CURRENCY_GUARDRAIL

    bare = build_system({"personality": {"enabled": False}, "crew": {"enabled": False}})
    assert bare is not None and _CURRENCY_GUARDRAIL in bare
    assert build_system({}) == build_system({})  # static -> cache-safe


def test_currency_guardrail_mentions_honesty_and_web_search():
    from covas.llm import _CURRENCY_GUARDRAIL

    low = _CURRENCY_GUARDRAIL.lower()
    assert "credits" in low and "carrier" in low
    assert "web-search" in low or "web search" in low
    assert "never invent" in low
