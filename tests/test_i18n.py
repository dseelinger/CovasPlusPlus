"""Offline unit tests for the shared i18n helpers (issue #182, layers 3-4 / #197, #198).

Pure and free — no model, network, or audio. Covers the display-name -> ISO code map, the
'follow the reply language' resolution that lets STT track the language you converse in, and the
locale helpers layer 4 uses to steer a TTS voice to one that speaks the reply language.
"""
from __future__ import annotations

from covas.i18n import (FOLLOW, language_code, locale_prefix, resolve_whisper_language,
                        voice_speaks)


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
