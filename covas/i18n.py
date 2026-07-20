"""Shared i18n helpers (issue #182 — multilingual support across the voice stack).

The curated set of languages COVAS++ delivers end-to-end lives in
`settings_schema.REPLY_LANGUAGES`; this module maps each display name to the ISO 639-1
code that Whisper (STT) and the TTS providers speak, and resolves the *effective* STT
language so speech-to-text can follow the reply language automatically (layer 3, #197).

Stdlib-only and dependency-free per CLAUDE.md — a small, honest table beats pulling in a
locale library for six languages.
"""
from __future__ import annotations

# Display name (exactly as in REPLY_LANGUAGES) -> ISO 639-1 code (Whisper's language codes,
# which the Edge/Azure TTS locale tables also key off in later layers #198).
_LANG_CODES: dict[str, str] = {
    "English": "en",
    "German": "de",
    "French": "fr",
    "Russian": "ru",
    "Spanish": "es",
    "Portuguese": "pt",
}

# Sentinel for [whisper].language: "track whatever [language].reply is set to". The shipped
# default, so changing the reply language moves STT with it and English installs keep "en".
FOLLOW = "follow"


def language_code(name: str | None) -> str | None:
    """ISO 639-1 code for a REPLY_LANGUAGES display name, or None if unknown/blank."""
    return _LANG_CODES.get((name or "").strip())


def reply_language(cfg: dict) -> str:
    """The configured reply-language display name (defaults to English)."""
    return str((cfg.get("language", {}) or {}).get("reply", "English") or "English").strip()


def resolve_whisper_language(cfg: dict) -> str | None:
    """The effective whisper.cpp language code, or None for auto-detect.

    - ``"follow"`` (default): derive from ``[language].reply`` (English->en, German->de, ...);
      an unknown reply language falls back to auto-detect (None) rather than forcing a guess.
    - ``""``       : auto-detect — whisper.cpp detects the language per utterance.
    - ``"<code>"`` : a forced explicit code (unchanged legacy behaviour, e.g. ``"en"``).
    """
    w = cfg.get("whisper", {}) or {}
    raw = str(w.get("language") or "").strip()
    if raw == FOLLOW:
        return language_code(reply_language(cfg))  # None -> auto-detect when unmapped
    return raw or None
