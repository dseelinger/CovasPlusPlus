"""Control-panel UI string translation (issue #182 layer 2, #196).

A tiny gettext-style helper. The English SOURCE STRING is the lookup key, so `t("Save")` returns
``"Save"`` in English (byte-identical to the old hardcoded text) and a translated string once a
language ships a catalog. That identity-for-English property is the whole safety story: wiring a
template through ``{{ t('…') }}`` cannot change what an English user sees.

We deliberately ship only the EXTRACTION MECHANISM + the English baseline here. A non-English UI
language is GATED until its catalog is actually complete — a half-translated panel is worse than an
honestly English one (the epic's rule) — so today the panel always renders English regardless of
the reply language. A translator adds their language by dropping a complete catalog into `CATALOGS`
(see ``docs/using/translating-the-ui.md``); membership in `covas.settings_schema.REPLY_LANGUAGES`
(via `covas.i18n.language_code`) is the curated gate.

Stdlib-only — no Flask-Babel, no `.po`/`.mo` compilation. One dict lookup per string.
"""
from __future__ import annotations

from . import i18n

# lang code (ISO 639-1) -> {english source string -> translation}. English is the identity map
# (empty dict == "every string falls back to itself"). A language appears here ONLY once its
# catalog is complete enough to activate; that presence is exactly what makes it shippable.
CATALOGS: dict[str, dict[str, str]] = {
    "en": {},
}


def available_ui_languages() -> list[str]:
    """The UI language codes we actually ship a (complete) catalog for — English-only today."""
    return sorted(CATALOGS)


def ui_language_code(cfg: dict) -> str:
    """The active UI language: the reply language's code IF we ship a catalog for it, else English.
    This is the gate — an untranslated (or unmapped) reply language yields a fully-English panel,
    never a half-translated one."""
    code = i18n.language_code(i18n.reply_language(cfg)) or "en"
    return code if code in CATALOGS else "en"


def translate(text: str, code: str | None) -> str:
    """gettext-style lookup: the English source is the key, and an untranslated (or English) string
    falls back to itself. Guarantees ``translate(s, "en") == s`` for every ``s``."""
    if not text:
        return text
    return CATALOGS.get(code or "en", {}).get(text, text)
