"""Shared i18n helpers (issue #182 — multilingual support across the voice stack).

The curated set of languages COVAS++ delivers end-to-end lives in
`settings_schema.REPLY_LANGUAGES`; this module maps each display name to the ISO 639-1
code that Whisper (STT) and the TTS providers speak, resolves the *effective* STT
language so speech-to-text can follow the reply language automatically (layer 3, #197),
carries the locale helpers that steer TTS voice selection (layer 4, #198), and formats
numbers/dates per the active locale in spoken + on-screen callouts (layer 5, #199).

Stdlib-only and dependency-free per CLAUDE.md — a small, honest table beats pulling in a
locale library for six languages.
"""
from __future__ import annotations

from datetime import datetime

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


def locale_prefix(name: str | None) -> str | None:
    """The locale-tag prefix a TTS catalog filters on for a REPLY_LANGUAGES display name —
    the ISO code plus a hyphen, e.g. German -> ``"de-"`` (matching Edge/Azure ``Locale`` tags
    like ``de-DE``). None when the language is unknown/blank, so callers keep the default pool.
    """
    code = language_code(name)
    return f"{code}-" if code else None


def voice_speaks(locale: str | None, code: str | None) -> bool:
    """Does a TTS voice with ``locale`` (a BCP-47 tag like ``de-DE``, or blank/None when the
    provider doesn't tag its voices) speak the ISO 639-1 language ``code`` (e.g. ``de``)?

    Deliberately permissive so we only ever steer AWAY from a demonstrable mismatch, never on a
    guess (layer 4, #198):

    - ``code`` blank/None (an unmapped or blank reply language — English maps to ``en`` and is
      checked normally): True — nothing to follow, so never steer on a guess.
    - ``locale`` blank/None (OpenAI TTS isn't locale-tagged; ElevenLabs is multilingual; a voice
      id we can't find in the catalog): True — assume it copes rather than swap a voice we can't
      actually inspect.
    - otherwise: True only when the tag matches the code exactly or as its primary subtag
      (``de`` or ``de-DE`` both speak ``de``; ``de-AT`` too).
    """
    c = (code or "").strip().lower()
    if not c:
        return True
    loc = (locale or "").strip().lower()
    if not loc:
        return True
    return loc == c or loc.startswith(c + "-")


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


# --- Locale number/date formatting (layer 5, #199) -------------------------------------------
# The whole reason this table is hand-rolled: pulling in a locale library (or relying on the C
# locale, which is process-global and platform-flaky) to separate six curated languages fails the
# "stdlib-first, small honest table" bar in CLAUDE.md. Each row is (grouping separator, decimal
# separator). English keeps ","/"." so its output stays BYTE-IDENTICAL to the old `f"{n:,}"`.
_NUMBER_FORMATS: dict[str, tuple[str, str]] = {
    "en": (",", "."),
    "de": (".", ","),
    "es": (".", ","),
    "pt": (".", ","),
    "fr": (" ", ","),   # narrow no-break space grouping (French convention)
    "ru": (" ", ","),   # Russian also groups with a (thin) space, comma decimal
}

# Short month names per curated language, plus the day/month order each writes a short date in.
# English is "Jul 15" (month-first) to stay identical to the old `strftime('%b') + day`; the other
# five lead with the day. Genitive/abbreviation choices are pragmatic v1 forms, not grammar-perfect.
_MONTHS: dict[str, tuple[str, ...]] = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "de": ("Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
    "pt": ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"),
    "ru": ("янв.", "февр.", "марта", "апр.", "мая", "июня", "июля", "авг.", "сент.", "окт.", "нояб.", "дек."),
}
_DATE_ORDER: dict[str, str] = {
    "en": "{mon} {day}", "de": "{day}. {mon}", "fr": "{day} {mon}",
    "es": "{day} {mon}", "pt": "{day} {mon}", "ru": "{day} {mon}",
}

# Process-wide "active" locale for the convenience formatters (fmt_int/fmt_num/fmt_date). Set once
# from [language].reply at startup and on every settings change (App), so the ~20 scattered callout
# sites don't each have to thread cfg through. None == English == byte-identical to the old output.
_active_code: str | None = None


def set_active_language_code(code: str | None) -> None:
    """Bind the active locale for the convenience formatters (called by App from [language].reply)."""
    global _active_code
    _active_code = (code or None)


def active_language_code() -> str | None:
    """The active locale code, or None (English / byte-identical default)."""
    return _active_code


def _localize_number(s: str, code: str | None) -> str:
    """Remap a Python-default number string (``,`` grouping, ``.`` decimal) to `code`'s separators.
    Uses a NUL placeholder so the two swaps can't collide. Unknown code -> English (no change)."""
    grp, dec = _NUMBER_FORMATS.get((code or "en"), (",", "."))
    return s.replace(",", "\x00").replace(".", dec).replace("\x00", grp)


def format_int(value: float, code: str | None = None) -> str:
    """Group an integer per `code` — e.g. 2_000_000 -> ``2,000,000`` (en) / ``2.000.000`` (de)."""
    return _localize_number(f"{int(value):,}", code)


def format_decimal(value: float, places: int, code: str | None = None) -> str:
    """Group a number to `places` decimals per `code` — e.g. 1234.5 -> ``1,234.5`` / ``1.234,5``."""
    return _localize_number(f"{value:,.{places}f}", code)


def format_date_short(dt: datetime, code: str | None = None) -> str:
    """A short spoken date — ``Jul 15`` (en), ``15. Juli`` (de), ``15 juil.`` (fr). English is
    byte-identical to the old ``strftime('%b') + day``; others lead with the day."""
    c = (code or "en") if (code or "en") in _MONTHS else "en"
    mon = _MONTHS[c][dt.month - 1]
    return _DATE_ORDER[c].format(day=dt.day, mon=mon)


def fmt_int(value: float) -> str:
    """`format_int` against the process-active locale — the drop-in for `f"{n:,}"` in callouts."""
    return format_int(value, _active_code)


def fmt_num(value: float, places: int) -> str:
    """`format_decimal` against the process-active locale — drop-in for `f"{n:,.{places}f}"`."""
    return format_decimal(value, places, _active_code)


def fmt_date(dt: datetime) -> str:
    """`format_date_short` against the process-active locale."""
    return format_date_short(dt, _active_code)
