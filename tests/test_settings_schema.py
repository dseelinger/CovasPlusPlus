"""Unit tests for the settings schema (Prompt N1) — pure, offline, free.

The schema is the single source of truth both the web page and the voice layer
project from, so these guard the two invariants that keep it honest: it declares
defaults that match config.toml (no drift), and its validator accepts good values
while rejecting out-of-range / bad-type / unknown-option ones with a reason.
"""
from __future__ import annotations

import tomllib

import pytest

from covas import config
from covas import settings_schema as s


# --- coverage + no-drift ---------------------------------------------------

def _raw_config() -> dict:
    with open(config.CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def _dig(cfg: dict, path: tuple):
    node = cfg
    for p in path:
        node = node[p]
    return node


def test_every_setting_points_at_a_real_config_key():
    """Each schema path must exist in config.toml — no phantom settings."""
    cfg = _raw_config()
    for setting in s.SCHEMA:
        _dig(cfg, setting.path)  # raises KeyError if the path is wrong


def test_schema_defaults_match_config_toml():
    """Declared defaults must equal the config.toml values, so the two can't
    drift: change one and this fails until the other matches."""
    cfg = _raw_config()
    for setting in s.SCHEMA:
        assert setting.default == _dig(cfg, setting.path), (
            f"{setting.key}: schema default {setting.default!r} != "
            f"config.toml {_dig(cfg, setting.path)!r}")


def test_required_sections_are_covered():
    """The sections N1 calls out are all represented by at least one setting."""
    sections = {setting.path[0] for setting in s.SCHEMA}
    for required in ("anthropic", "whisper", "elevenlabs", "web_search",
                     "conversation", "nav", "router", "proactive", "elite",
                     "keybinds", "ui"):
        assert required in sections, f"schema is missing section [{required}]"


def test_enum_settings_declare_options_or_a_source():
    for setting in s.SCHEMA:
        if setting.type == "enum":
            assert setting.options is not None or setting.options_source, setting.key


def test_keys_are_unique():
    keys = [setting.key for setting in s.SCHEMA]
    assert len(keys) == len(set(keys))


# --- validate_value: booleans ---------------------------------------------

_BOOL = s.by_key["personality.enabled"]


@pytest.mark.parametrize("raw,expected", [
    (True, True), (False, False),
    ("true", True), ("On", True), ("yes", True), ("1", True),
    ("false", False), ("off", False), ("no", False), ("0", False),
])
def test_bool_accepts_truthy_and_falsy(raw, expected):
    val, err = s.validate_value(_BOOL, raw)
    assert err is None and val is expected


def test_bool_rejects_garbage():
    val, err = s.validate_value(_BOOL, "maybe")
    assert val is None and "true or false" in err


# --- validate_value: numbers + ranges -------------------------------------

_INT = s.by_key["anthropic.max_tokens"]  # min 128, max 8192


def test_int_accepts_in_range_and_coerces_strings():
    assert s.validate_value(_INT, 2000) == (2000, None)
    assert s.validate_value(_INT, "2000") == (2000, None)


def test_int_rejects_below_min_and_above_max():
    _, low = s.validate_value(_INT, 10)
    _, high = s.validate_value(_INT, 999999)
    assert "at least 128" in low
    assert "at most 8192" in high


def test_int_rejects_non_numeric_and_bool():
    assert s.validate_value(_INT, "lots")[0] is None
    # bool is an int subclass — must not sneak through as 0/1
    assert s.validate_value(_INT, True)[0] is None


def test_float_accepts_fractions():
    poll = s.by_key["elite.journal_poll_interval"]
    assert s.validate_value(poll, "0.5") == (0.5, None)
    assert s.validate_value(poll, 99)[0] is None  # above max 10.0


# --- validate_value: enums -------------------------------------------------

_ENUM = s.by_key["whisper.model"]


def test_enum_accepts_listed_option():
    assert s.validate_value(_ENUM, "medium") == ("medium", None)


def test_enum_rejects_unlisted_with_options_in_message():
    val, err = s.validate_value(_ENUM, "gigantic")
    assert val is None
    assert "gigantic" in err and "'small'" in err


def test_enum_with_dynamic_source_uses_supplied_options():
    model = s.by_key["anthropic.model"]
    opts = ["claude-opus-4-8", "claude-sonnet-5"]
    assert s.validate_value(model, "claude-sonnet-5", options=opts) == ("claude-sonnet-5", None)
    assert s.validate_value(model, "gpt-9", options=opts)[0] is None


def test_enum_with_unresolved_dynamic_source_type_checks_only():
    """Offline, an ElevenLabs voice can't be checked against a list — accept the
    string rather than reject a value we simply can't verify right now."""
    voice = s.by_key["elevenlabs.voice_id"]
    val, err = s.validate_value(voice, "some-voice-id", options=None)
    assert err is None and val == "some-voice-id"


# --- value helpers ---------------------------------------------------------

def test_set_value_builds_nested_patch():
    setting = s.by_key["anthropic.thinking.default"]
    patch = s.set_value({}, setting, "High")
    assert patch == {"anthropic": {"thinking": {"default": "High"}}}


def test_is_overridden_walks_nested_overrides():
    setting = s.by_key["anthropic.thinking.default"]
    assert s.is_overridden({"anthropic": {"thinking": {"default": "High"}}}, setting)
    assert not s.is_overridden({"anthropic": {"model": "x"}}, setting)


def test_public_schema_folds_in_value_and_overridden_flag():
    cfg = {"web_search": {"enabled": False}}
    overrides = {"web_search": {"enabled": False}}
    groups = s.public_schema(cfg, overrides)
    flat = {st["key"]: st for g in groups for st in g["settings"]}
    assert flat["web_search.enabled"]["value"] is False
    assert flat["web_search.enabled"]["overridden"] is True
    # hidden settings (voice_name) are not exposed as rows
    assert "elevenlabs.voice_name" not in flat


# ---- doc_url "Setup guide →" links (issue #121) ----------------------------
def test_doc_url_round_trips_into_the_payload():
    """A Setting's optional doc_url surfaces in the field payload alongside help; None when unset."""
    with_doc = s.Setting("x.y", ("x", "y"), "bool", "X", "G", "help", default=False,
                          doc_url="https://example.test/guide#anchor")
    without = s.Setting("x.z", ("x", "z"), "bool", "X", "G", "help", default=False)
    assert s.field_payload({}, {}, with_doc)["doc_url"] == "https://example.test/guide#anchor"
    assert s.field_payload({}, {}, without)["doc_url"] is None


def test_hud_rows_carry_setup_guide_doc_urls():
    """The three Companion-HUD toggles link to the published hud.md setup sections (verified slugs)."""
    base = "https://dseelinger.github.io/CovasPlusPlus/using/hud/"
    expected = {
        "hud.enabled": base + "#turning-it-on-and-off",
        "hud.vr_enabled": base + "#in-vr-the-in-headset-overlay",
        "hud.web_enabled": base + "#in-headset-without-steamvr-the-web-hud-openkneeboard",
    }
    for key, url in expected.items():
        assert s.by_key[key].doc_url == url
        assert s.field_payload({}, {}, s.by_key[key])["doc_url"] == url
