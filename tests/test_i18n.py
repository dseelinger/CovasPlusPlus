"""Offline unit tests for the shared i18n helpers (issue #182, layers 3-5 / #197, #198, #199).

Pure and free — no model, network, or audio. Covers the display-name -> ISO code map, the
'follow the reply language' resolution that lets STT track the language you converse in, the
locale helpers layer 4 uses to steer a TTS voice to one that speaks the reply language, and the
layer-5 locale number/date formatting for spoken + on-screen callouts.
"""
from __future__ import annotations

from datetime import datetime

from covas import i18n
from covas.i18n import (FOLLOW, format_date_short, format_decimal, format_int, language_code,
                        locale_prefix, resolve_whisper_language, voice_speaks)


def test_language_code_maps_curated_names():
    assert language_code("English") == "en"
    assert language_code("German") == "de"
    assert language_code("French") == "fr"
    assert language_code("Russian") == "ru"
    assert language_code("Spanish") == "es"
    assert language_code("Portuguese") == "pt"


def test_language_code_unknown_or_blank_is_none():
    assert language_code("Klingon") is None
    assert language_code("") is None
    assert language_code(None) is None


def _cfg(whisper_lang: str, reply: str = "English") -> dict:
    return {"whisper": {"model": "small", "language": whisper_lang}, "language": {"reply": reply}}


def test_follow_derives_code_from_reply_language():
    assert resolve_whisper_language(_cfg(FOLLOW, reply="German")) == "de"
    assert resolve_whisper_language(_cfg(FOLLOW, reply="English")) == "en"
    assert resolve_whisper_language(_cfg(FOLLOW, reply="Portuguese")) == "pt"


def test_follow_with_unmapped_reply_falls_back_to_autodetect():
    # An unknown reply language must not force a wrong code — auto-detect (None) instead.
    assert resolve_whisper_language(_cfg(FOLLOW, reply="Klingon")) is None


def test_blank_is_autodetect_and_explicit_code_is_forced():
    assert resolve_whisper_language(_cfg("", reply="German")) is None  # blank wins over reply
    assert resolve_whisper_language(_cfg("en", reply="German")) == "en"  # explicit override wins


def test_missing_sections_are_safe():
    assert resolve_whisper_language({}) is None
    assert resolve_whisper_language({"whisper": {"language": FOLLOW}}) == "en"  # reply defaults English


# ---- locale helpers (layer 4 / #198) ---------------------------------------
def test_locale_prefix_is_code_plus_hyphen():
    assert locale_prefix("German") == "de-"
    assert locale_prefix("English") == "en-"
    assert locale_prefix("Portuguese") == "pt-"


def test_locale_prefix_unknown_or_blank_is_none():
    assert locale_prefix("Klingon") is None
    assert locale_prefix("") is None
    assert locale_prefix(None) is None


def test_voice_speaks_matches_primary_subtag():
    assert voice_speaks("de-DE", "de") is True
    assert voice_speaks("de-AT", "de") is True   # Austrian German still speaks German
    assert voice_speaks("de", "de") is True       # bare code
    assert voice_speaks("en-US", "de") is False   # English voice does NOT speak German


def test_voice_speaks_is_permissive_when_it_cannot_tell():
    # No target language -> never steer (True). English maps to a real code and is checked normally.
    assert voice_speaks("en-US", None) is True
    assert voice_speaks("en-US", "") is True
    # Untagged voice (OpenAI/ElevenLabs/multilingual) -> assume it copes, don't steer.
    assert voice_speaks("", "de") is True
    assert voice_speaks(None, "de") is True


# ---- locale number/date formatting (layer 5 / #199) ------------------------
_THIN = " "   # the narrow no-break space fr/ru group with


def test_format_int_grouping_per_locale():
    assert format_int(2_000_000, "en") == "2,000,000"
    assert format_int(2_000_000, "de") == "2.000.000"
    assert format_int(2_000_000, "es") == "2.000.000"
    assert format_int(2_000_000, "pt") == "2.000.000"
    assert format_int(2_000_000, "fr") == f"2{_THIN}000{_THIN}000"
    assert format_int(2_000_000, "ru") == f"2{_THIN}000{_THIN}000"


def test_format_int_english_is_byte_identical_to_native():
    # The whole "English stays untouched" guarantee: our formatter == the old f"{n:,}".
    for n in (0, 5, 1234, -98765, 2_000_000, 1_000_000_000):
        assert format_int(n, "en") == f"{n:,}"
        assert format_int(n, None) == f"{n:,}"       # unmapped/unset -> English


def test_format_decimal_places_and_separators():
    assert format_decimal(1234.5, 1, "en") == "1,234.5"
    assert format_decimal(1234.5, 1, "de") == "1.234,5"
    assert format_decimal(1234.56, 2, "de") == "1.234,56"
    assert format_decimal(1234.5, 1, "fr") == f"1{_THIN}234,5"
    assert format_decimal(4200, 0, "de") == "4.200"       # distance/light-seconds case
    assert format_decimal(1234.56, 2, "en") == f"{1234.56:,.2f}"   # en identical to native


def test_format_date_short_per_locale():
    dt = datetime(2026, 7, 15)
    assert format_date_short(dt, "en") == "Jul 15"        # identical to old strftime('%b')+day
    assert format_date_short(dt, "de") == "15. Juli"
    assert format_date_short(dt, "fr") == "15 juil."
    assert format_date_short(dt, "es") == "15 jul"
    assert format_date_short(dt, None) == "Jul 15"        # unmapped -> English


def test_active_language_code_drives_convenience_formatters():
    try:
        i18n.set_active_language_code("de")
        assert i18n.active_language_code() == "de"
        assert i18n.fmt_int(2_000_000) == "2.000.000"
        assert i18n.fmt_num(1234.5, 1) == "1.234,5"
        assert i18n.fmt_date(datetime(2026, 7, 15)) == "15. Juli"
    finally:
        i18n.set_active_language_code(None)               # reset — don't leak into other tests
    assert i18n.fmt_int(2_000_000) == "2,000,000"         # back to English default
