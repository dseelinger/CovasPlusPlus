"""Offline guard for the native-window entry's wizard→panel handoff URLs (I9).

The single-window handoff navigates the SAME native window from the wizard to the panel via
`window.load_url`. WebView2 no-ops a `load_url` to the URL the window is ALREADY on, so the two
URLs MUST differ by path or the window sits on the finished wizard forever (the live bug this
guards against). Importing the entry module is safe — it imports `webview` lazily inside main(),
so this needs no GUI/pywebview.
"""
from __future__ import annotations

import run_covas_app


def test_wizard_and_panel_urls_differ_by_path():
    wizard, panel = run_covas_app._wizard_panel_urls("127.0.0.1", 8765)
    assert wizard != panel                     # a same-URL load_url would no-op → stuck on wizard
    assert wizard.endswith("/setup")           # the wizard's dedicated route
    assert panel.endswith("/")                 # the panel/home
    assert wizard.startswith("http://127.0.0.1:8765")
    assert panel.startswith("http://127.0.0.1:8765")
