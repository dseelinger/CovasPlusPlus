"""Offline tests for the control-panel theme selector (issue #104).

Two guarantees, proven without network or real providers:
  * the Flask context processor injects the configured `ui.theme` into every template (and
    defaults to "dark" when the setting is absent), and
  * every page route server-renders `data-theme` onto the root <html> element, so the correct
    palette is present on first paint (no flash) — including the first-run setup wizard, which
    is served by a SEPARATE tiny app.

A minimal stub core (just `.cfg`) is enough: the page routes render static templates and read the
theme only through the context processor, so no App/provider construction is needed here.
"""
from __future__ import annotations

import threading

import pytest

from covas.setup_web import create_setup_app
from covas.web import create_app

# The main-app routes that render a full HTML page (each must stamp data-theme on <html>).
PAGE_ROUTES = ["/", "/settings", "/checklist", "/memory", "/crew", "/macros"]


class _StubCore:
    """The smallest core the page routes + context processor need: a config dict."""

    def __init__(self, cfg: dict):
        self.cfg = cfg


def _client(cfg: dict):
    app = create_app(_StubCore(cfg))
    app.config.update(TESTING=True)
    return app.test_client()


# --- context processor -----------------------------------------------------

@pytest.mark.parametrize("theme", ["dark", "light", "elite"])
def test_context_processor_injects_configured_theme(theme):
    html = _client({"ui": {"theme": theme}}).get("/").get_data(as_text=True)
    assert f'data-theme="{theme}"' in html


def test_theme_defaults_to_dark_when_ui_section_absent():
    html = _client({}).get("/").get_data(as_text=True)
    assert 'data-theme="dark"' in html


def test_theme_defaults_to_dark_when_theme_key_absent():
    # [ui] present (host/port) but no theme key -> still dark, never blank.
    html = _client({"ui": {"host": "127.0.0.1", "port": 8765}}).get("/").get_data(as_text=True)
    assert 'data-theme="dark"' in html


# --- every page route emits data-theme -------------------------------------

@pytest.mark.parametrize("route", PAGE_ROUTES)
def test_every_page_route_emits_data_theme(route):
    html = _client({"ui": {"theme": "elite"}}).get(route).get_data(as_text=True)
    # Rendered onto the root element, so the palette is right on first paint.
    assert '<html lang="en" data-theme="elite">' in html


def test_theme_css_static_asset_is_served():
    # Bundled via collect_data_files("covas") in the packaged build; here it's on disk under static/.
    r = _client({}).get("/static/theme.css")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert '[data-theme="light"]' in body and '[data-theme="elite"]' in body


# --- first-run wizard (separate app) ---------------------------------------

def test_setup_page_emits_configured_theme():
    cfg = {"ui": {"theme": "light"}}
    app = create_setup_app(cfg, threading.Event())
    app.config.update(TESTING=True)
    html = app.test_client().get("/setup").get_data(as_text=True)
    assert 'data-theme="light"' in html


def test_setup_page_defaults_to_dark_on_fresh_install():
    # A fresh install has no [ui].theme yet -> the wizard opens in dark, not blank.
    app = create_setup_app({}, threading.Event())
    app.config.update(TESTING=True)
    html = app.test_client().get("/setup").get_data(as_text=True)
    assert 'data-theme="dark"' in html
