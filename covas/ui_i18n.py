"""Control-panel UI string translation (issue #182 layer 2, #196).

A tiny gettext-style helper. The English SOURCE STRING is the lookup key, so `t("Save")` returns
``"Save"`` in English (byte-identical to the old hardcoded text) and a translated string once a
language ships a catalog. That identity-for-English property is the whole safety story: wiring a
template through ``{{ t('…') }}`` cannot change what an English user sees.

A non-English UI language is GATED until its catalog is actually complete — a half-translated panel
is worse than an honestly English one (the epic's rule). English is the identity baseline; the
curated languages (German/French/Russian/Spanish/Portuguese) ship complete catalogs under
``covas/translations/`` (issue #196 fill-in — LLM-authored, native review pending). A language a
Commander hasn't a complete catalog for still falls back to a fully-English panel.

Stdlib-only — no Flask-Babel, no `.po`/`.mo` compilation. One dict lookup per string.
"""
from __future__ import annotations

from . import i18n

# lang code (ISO 639-1) -> {english source string -> translation}. English is the identity map
# (empty dict == "every string falls back to itself"). A language appears here ONLY once its
# catalog is complete; that presence is exactly what makes it shippable. The curated catalogs are
# loaded from covas/translations/*.json (fail-soft — a bad catalog is skipped, English still serves).
CATALOGS: dict[str, dict[str, str]] = {
    "en": {},
}
try:
    from .translations import load as _load_catalogs
    CATALOGS.update(_load_catalogs())
except Exception:  # noqa: BLE001 — a catalog-load failure must never break the panel (English serves)
    pass


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
