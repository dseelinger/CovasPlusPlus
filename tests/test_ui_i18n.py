"""Offline tests for the control-panel UI string mechanism (issue #182 layer 2, #196).

Proves the two guarantees the extraction relies on: English is an exact identity (so wiring a
template through `t()` can never change what an English user sees), and a non-English UI language
is GATED off until it ships a complete catalog (no half-translated panel). Stdlib-only, no network.
"""
from __future__ import annotations

import covas.ui_i18n as ui


def test_english_is_identity_for_any_string():
    for s in ("Save", "Reply language", "", "Test my setup", "A string; with, punctuation. 2,000"):
        assert ui.translate(s, "en") == s
        assert ui.translate(s, None) == s          # unset code -> English -> identity


def test_translate_uses_catalog_when_present(monkeypatch):
    monkeypatch.setitem(ui.CATALOGS, "de", {"Save": "Speichern"})
    assert ui.translate("Save", "de") == "Speichern"
    assert ui.translate("Cancel", "de") == "Cancel"   # untranslated key falls back to English


def test_ui_language_code_gates_untranslated_languages():
    # German reply language, but no German catalog shipped -> UI stays English.
    assert ui.ui_language_code({"language": {"reply": "German"}}) == "en"
    assert ui.ui_language_code({}) == "en"             # default English
    assert ui.ui_language_code({"language": {"reply": "Klingon"}}) == "en"  # unmapped -> English


def test_ui_language_code_activates_a_shipped_catalog(monkeypatch):
    monkeypatch.setitem(ui.CATALOGS, "de", {"Save": "Speichern"})
    assert ui.ui_language_code({"language": {"reply": "German"}}) == "de"   # now activated
    assert ui.ui_language_code({"language": {"reply": "French"}}) == "en"   # still gated


def test_available_ui_languages_is_english_only_by_default():
    assert ui.available_ui_languages() == ["en"]
