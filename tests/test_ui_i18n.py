"""Offline tests for the control-panel UI string mechanism (issue #182 layer 2, #196).

Proves the two guarantees the extraction relies on: English is an exact identity (so wiring a
template through `t()` can never change what an English user sees), and a non-English UI language
is GATED off until it ships a complete catalog (no half-translated panel). Stdlib-only, no network.
"""
from __future__ import annotations

import pytest

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


# ---- render guard: every wired template renders with t() resolved to English ----------------
import covas.web as web            # noqa: E402
import covas.setup_web as setup_web  # noqa: E402
from flask import render_template  # noqa: E402


class _Core:
    cfg = {"language": {"reply": "English"}, "ui": {"port": 8765, "host": "127.0.0.1"}}


# The templates wired through t() so far, each with a representative English string that must
# survive rendering unchanged (the "English renders identically" guarantee, automated).
_WIRED = {
    "index.html": ["CONTROL PANEL", "Configuration", "Live Log"],
    "settings.html": ["SETTINGS", "SAVE CHANGES", "Loading settings…"],
    "checklist.html": ["CHECKLIST", "Ultimate checklist", ">SAVE<"],
    "memory.html": ["MEMORY", "Add a memory", "No memories yet."],
    "engineers.html": ["ENGINEERS", "Engineer unlock status"],
    "macros.html": ["CUSTOM MACROS", "Author a macro", "SAVE MACRO"],
    "crew.html": ["CREW", "Crew roster", "SAVE ROSTER"],
    "_command_palette.html": ["navigate", "select", "esc close"],
}


@pytest.mark.parametrize("template,needles", list(_WIRED.items()))
def test_wired_template_renders_english_without_t_leak(template, needles):
    app = web.create_app(_Core())
    with app.app_context(), app.test_request_context("/"):
        html = render_template(template, theme="dark")
    assert "{{ t(" not in html and "{{t(" not in html   # every t() resolved
    for s in needles:
        assert s in html, f"{s!r} missing from rendered {template}"


def test_setup_wizard_renders_english_without_t_leak():
    import threading
    app = setup_web.create_setup_app({"ui": {"theme": "dark"}, "language": {"reply": "English"}},
                                     threading.Event())
    app.testing = True
    html = app.test_client().get("/").get_data(as_text=True)
    assert "{{ t(" not in html
    for s in ("FIRST-RUN SETUP", "Save AI provider", "Launch COVAS++"):
        assert s in html
