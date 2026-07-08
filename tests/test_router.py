"""Unit tests for the cost router (covas/router.py) — pure, offline, deterministic.

Covers every routing rule plus the override/pin paths, and the config-loading seam
(RouterConfig.from_cfg). No network, no providers — decide() is a pure function of
(config, text, context).
"""
from __future__ import annotations

import pytest

from covas.router import Route, Router, RouterConfig


# Model ids used across the tests — match the config defaults so a drift there is caught.
HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"
OPUS = "claude-opus-4-8"


def _router(**over) -> Router:
    """A Router with routing ON and the default phrase lists, overridable per test."""
    base = dict(enabled=True, default_model=HAIKU, escalate_model=SONNET,
                premium_model=OPUS, fixed_model=SONNET, base_max_tokens=1024,
                full_breakdown_max_tokens=2048)
    base.update(over)
    return Router(RouterConfig(**base))


# ---- default tier -----------------------------------------------------------
def test_routine_turn_lands_on_haiku():
    r = _router()
    route = r.decide("What's my next objective, COVAS?")
    assert route.model == HAIKU
    assert route.max_tokens == 1024
    assert "Haiku" in route.reason


@pytest.mark.parametrize("text", [
    "mark fuel scooping complete",
    "how's my fuel",
    "acknowledged, thanks",
    "",  # empty transcription still routes (to the cheap default)
])
def test_banter_and_acks_stay_on_haiku(text):
    assert _router().decide(text).model == HAIKU


# ---- escalation: wake phrase ------------------------------------------------
def test_wake_phrase_escalates_to_sonnet():
    route = _router().decide("Think hard about this one for me.")
    assert route.model == SONNET
    assert "wake phrase" in route.reason


def test_big_brain_phrase_escalates():
    assert _router().decide("Ask the big brain what to do.").model == SONNET


# ---- escalation: depth / analysis ------------------------------------------
@pytest.mark.parametrize("text", [
    "Analyze the trade route options.",
    "Explain why the frame shift drive overheats.",
    "Compare the Python and the Krait for mining.",
    "Walk me through the engineering steps in detail.",
])
def test_depth_requests_escalate_to_sonnet(text):
    route = _router().decide(text)
    assert route.model == SONNET
    assert "depth/analysis" in route.reason


# ---- escalation: current / web data ----------------------------------------
def test_current_data_phrase_escalates():
    route = _router().decide("What's the latest news on the Thargoid war?")
    assert route.model == SONNET
    assert "current/web data" in route.reason


def test_context_needs_web_flag_escalates_without_a_phrase():
    # A caller (future ED-context capability) can flag it directly.
    route = _router().decide("and that station?", context={"needs_web": True})
    assert route.model == SONNET
    assert "current/web data" in route.reason


# ---- premium override -------------------------------------------------------
def test_use_opus_override_selects_opus():
    route = _router().decide("Use opus for this, please.")
    assert route.model == OPUS
    assert "Opus" in route.reason


def test_premium_override_beats_escalation_signals():
    # Even with depth + web phrases present, the explicit Opus ask wins.
    route = _router().decide("Analyze the latest data — use opus.")
    assert route.model == OPUS


# ---- max_tokens: full breakdown --------------------------------------------
def test_full_breakdown_raises_max_tokens_but_not_the_tier():
    route = _router().decide("Give me the full breakdown.")
    assert route.max_tokens == 2048
    assert route.model == HAIKU  # tokens-only rule; no depth phrase here
    assert "max_tokens" in route.reason


def test_full_breakdown_combines_with_escalation():
    route = _router().decide("Analyze the options and give me the full breakdown.")
    assert route.model == SONNET
    assert route.max_tokens == 2048


def test_full_breakdown_cap_is_configurable():
    r = _router(full_breakdown_max_tokens=4096)
    assert r.decide("give me everything").max_tokens == 4096


# ---- manual pin (UI toggle) -------------------------------------------------
def test_config_pin_forces_tier_regardless_of_text():
    r = _router(pin="opus")
    # A plain banter turn that would otherwise be Haiku is pinned to Opus.
    assert r.decide("what's next?").model == OPUS
    assert "pinned" in r.decide("what's next?").reason


def test_context_pin_overrides_config_pin_for_one_turn():
    r = _router(pin="opus")
    route = r.decide("what's next?", context={"pin": "haiku"})
    assert route.model == HAIKU


def test_pin_still_honors_full_breakdown_cap():
    r = _router(pin="sonnet")
    route = r.decide("give me the full breakdown")
    assert route.model == SONNET
    assert route.max_tokens == 2048


@pytest.mark.parametrize("token,model", [
    ("haiku", HAIKU), ("default", HAIKU),
    ("sonnet", SONNET), ("escalate", SONNET),
    ("opus", OPUS), ("premium", OPUS),
])
def test_pin_aliases_resolve(token, model):
    assert _router(pin=token).decide("hi").model == model


def test_unknown_pin_is_ignored_falls_through_to_rules():
    # A garbage pin shouldn't wedge routing — fall through to the normal decision.
    assert _router(pin="banana").decide("hello there").model == HAIKU


# ---- disabled: fixed tier ---------------------------------------------------
def test_disabled_router_uses_fixed_model_for_every_turn():
    r = Router(RouterConfig(enabled=False, fixed_model=SONNET, base_max_tokens=1024))
    for text in ("think hard", "use opus", "analyze the latest data", "hi"):
        route = r.decide(text)
        assert route == Route(SONNET, 1024, "router off — fixed tier")


# ---- matching robustness ----------------------------------------------------
def test_matching_is_case_and_whitespace_insensitive():
    assert _router().decide("  THINK   HARD  ").model == SONNET


# ---- config loading (from_cfg) ---------------------------------------------
def test_from_cfg_reads_router_section_and_anthropic_fallback():
    cfg = {
        "anthropic": {"model": "claude-opus-4-8", "max_tokens": 512},
        "router": {
            "enabled": True,
            "default_model": "d", "escalate_model": "e", "premium_model": "p",
            "full_breakdown_max_tokens": 3000,
            "escalate_phrases": ["zap"],
        },
    }
    r = Router.from_cfg(cfg)
    assert r.cfg.enabled is True
    assert r.cfg.default_model == "d"
    assert r.cfg.fixed_model == "claude-opus-4-8"   # from [anthropic]
    assert r.cfg.base_max_tokens == 512             # from [anthropic]
    assert r.cfg.full_breakdown_max_tokens == 3000
    # Custom phrase list is honored; the default lists it replaced are gone.
    assert r.decide("zap it").model == "e"
    assert r.decide("think hard").model == "d"      # no longer an escalate phrase


def test_from_cfg_defaults_when_sections_absent():
    # A bare config (no [router]) yields a disabled router falling back to [anthropic].
    r = Router.from_cfg({"anthropic": {"model": SONNET, "max_tokens": 1024}})
    assert r.cfg.enabled is False
    assert r.decide("think hard").model == SONNET    # disabled -> fixed


def test_from_cfg_missing_anthropic_uses_dataclass_defaults():
    r = Router.from_cfg({})
    assert r.cfg.base_max_tokens == 1024
    assert r.cfg.fixed_model == "claude-sonnet-5"


# ---- strip_control: keep the control phrase out of the model's input --------
def test_strip_control_removes_premium_phrase_and_filler():
    r = _router()
    cleaned = r.strip_control(
        "Use opus for this. What's the best handheld weapon right now?")
    assert cleaned == "What's the best handheld weapon right now?"


def test_strip_control_removes_wake_phrase():
    assert _router().strip_control("Think hard about the route to Colonia.") == \
        "about the route to Colonia."


def test_strip_control_leaves_ordinary_turns_untouched():
    # No control phrase -> unchanged, including an incidental "for me".
    r = _router()
    assert r.strip_control("Scan that planet for me.") == "Scan that planet for me."
    assert r.strip_control("What's my next objective?") == "What's my next objective?"


def test_strip_control_does_not_touch_depth_or_web_words():
    # "analyze"/"latest" are content that only *routes*; they must survive intact.
    r = _router()
    assert r.strip_control("Analyze the latest trade data.") == \
        "Analyze the latest trade data."


def test_strip_control_standalone_phrase_falls_back_to_original():
    # Nothing left after stripping -> keep the original (no real request to preserve).
    assert _router().strip_control("use opus") == "use opus"


def test_strip_control_routing_still_uses_raw_text():
    # The stripped text is only the model's input; decide() still escalates on the raw.
    r = _router()
    raw = "Use opus and analyze the route."
    assert r.decide(raw).model == OPUS
    assert "opus" not in r.strip_control(raw).lower()
