"""Unit tests for the reply-language slice (issue #182, layer 1) — offline, free.

Covers the two things layer 1 ships: the system prompt gains a "respond in <language>" instruction
when a non-English reply language is configured (and NOTHING changes for the English default, so the
common case keeps its exact prompt/cache), and the setting is voice-settable + schema-consistent.
No network, API, or model calls — pure inspection of the composed system prompt string.
"""
from __future__ import annotations

from covas import settings_schema as schema
from covas.llm import build_system, _language_instruction
from covas.capabilities.settings_capability import find_settings


def _cfg(reply=None):
    c = {"personality": {"enabled": False}, "crew": {"enabled": False}}
    if reply is not None:
        c["language"] = {"reply": reply}
    return c


# --- the instruction fragment ----------------------------------------------

def test_english_default_emits_no_language_instruction():
    # The default must not touch the prompt at all — same bytes, same cache — for everyone today.
    assert _language_instruction(_cfg()) is None
    assert _language_instruction(_cfg("English")) is None
    assert _language_instruction(_cfg("en")) is None
    assert _language_instruction(_cfg("")) is None


def test_non_english_language_produces_an_instruction_naming_it():
    instr = _language_instruction(_cfg("German"))
    assert instr is not None
    assert "German" in instr
    assert instr.lower().startswith("language:")


def test_build_system_appends_language_instruction_when_set():
    # With everything else OFF, a non-English language still yields a system prompt carrying it...
    de = build_system(_cfg("French"))
    assert de is not None and "French" in de
    # ...and the English default's prompt does NOT mention a reply language.
    en = build_system(_cfg("English"))
    assert (en is None) or ("respond in" not in en.lower())


def test_language_instruction_keeps_ed_proper_nouns_verbatim():
    # Grounding + voice search resolve names against a canonical English vocabulary, so the model
    # must be told NOT to translate ED proper nouns.
    instr = _language_instruction(_cfg("Russian")).lower()
    assert "verbatim" in instr
    for token in ("system", "station", "ship"):
        assert token in instr


def test_language_instruction_is_static_and_cache_safe():
    # Same config in -> byte-identical out, so it rides the cached prefix and never busts the cache.
    assert build_system(_cfg("Spanish")) == build_system(_cfg("Spanish"))


# --- the setting -----------------------------------------------------------

def test_reply_language_is_voice_settable():
    matches = find_settings("reply language")
    assert [m.key for m in matches] == ["language.reply"]
    # A bare "language" also resolves to it (not the whisper/transcription language).
    assert any(m.key == "language.reply" for m in find_settings("language"))


def test_reply_language_options_are_the_curated_set():
    s = schema.by_key["language.reply"]
    assert s.type == "enum"
    assert s.options == schema.REPLY_LANGUAGES
    assert s.default == "English" and "English" in s.options
    # The big non-English ED communities are covered.
    for lang in ("German", "French", "Russian"):
        assert lang in s.options
