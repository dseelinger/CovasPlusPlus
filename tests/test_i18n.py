"""Offline unit tests for the shared i18n helpers (issue #182, layer 3 / #197).

Pure and free — no model, network, or audio. Covers the display-name -> ISO code map and the
'follow the reply language' resolution that lets STT track the language you converse in.
"""
from __future__ import annotations

from covas.i18n import FOLLOW, language_code, resolve_whisper_language


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
