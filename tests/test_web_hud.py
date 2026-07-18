"""Unit tests for the web HUD surface (issue #103) — offline, free, no VR, no network.

Exercises the two Flask endpoints that back OpenKneeboard's Web Dashboard tab:
  * `/api/hud` — the live snapshot as JSON, FAIL SOFT: {"enabled": false} when the HUD is off,
    when there's no HUD capability, or when the model raises — never a 500.
  * `/hud` — the transparent page, which must be self-contained (no external asset URL).

A tiny stub core stands in for `App`: it only needs `.cfg` and (optionally) `.hud.model.snapshot`.
"""
from __future__ import annotations

import covas.web as web
from covas.capabilities.hud_capability import HudSnapshot


class _FakeModel:
    def __init__(self, snap=None, raises=False):
        self._snap = snap
        self._raises = raises

    def snapshot(self):
        if self._raises:
            raise RuntimeError("model boom")
        return self._snap


class _FakeHud:
    def __init__(self, model):
        self.model = model


class _StubCore:
    def __init__(self, *, web_enabled=False, hud=None):
        self.cfg = {"hud": {"web_enabled": web_enabled}}
        self.hud = hud


def _client(core):
    app = web.create_app(core)
    app.testing = True
    return app.test_client()


def test_api_hud_reports_disabled_when_web_hud_off():
    snap = HudSnapshot(voice_state="Listening")
    core = _StubCore(web_enabled=False, hud=_FakeHud(_FakeModel(snap)))
    body = _client(core).get("/api/hud").get_json()
    assert body == {"enabled": False}


def test_api_hud_returns_the_four_fields_when_enabled():
    snap = HudSnapshot(voice_state="Speaking", checklist="Scan beacon (2/10 done)",
                       route="3 jumps to Colonia", callout="Arrived in Sol.")
    core = _StubCore(web_enabled=True, hud=_FakeHud(_FakeModel(snap)))
    r = _client(core).get("/api/hud")
    assert r.status_code == 200
    body = r.get_json()
    assert body == {
        "enabled": True,
        "voice_state": "Speaking",
        "checklist": "Scan beacon (2/10 done)",
        "route": "3 jumps to Colonia",
        "callout": "Arrived in Sol.",
    }


def test_api_hud_is_disabled_when_no_hud_capability():
    # Wiring failed at startup => core.hud is None; the route must not 500.
    core = _StubCore(web_enabled=True, hud=None)
    r = _client(core).get("/api/hud")
    assert r.status_code == 200 and r.get_json() == {"enabled": False}


def test_api_hud_fail_soft_when_model_raises():
    core = _StubCore(web_enabled=True, hud=_FakeHud(_FakeModel(raises=True)))
    r = _client(core).get("/api/hud")
    assert r.status_code == 200 and r.get_json() == {"enabled": False}


def test_hud_page_renders_and_is_self_contained():
    core = _StubCore(web_enabled=True, hud=_FakeHud(_FakeModel(HudSnapshot())))
    r = _client(core).get("/hud")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # Transparent by construction, polls the JSON endpoint.
    assert "background: transparent" in html
    assert "/api/hud" in html
    # Self-contained: no external asset request (offline-first — Chromium here has no network).
    lowered = html.lower()
    for needle in ("http://", "https://", "//cdn", "src=\"//", "@import"):
        # allow the localhost /api/hud fetch (relative) but no absolute external URLs
        assert needle not in lowered, f"web HUD must not reference external asset: {needle!r}"


def test_note_web_ui_started_is_called_on_create_app():
    calls = {"n": 0}

    class _Core(_StubCore):
        def note_web_ui_started(self):
            calls["n"] += 1

    web.create_app(_Core())
    assert calls["n"] == 1  # the control panel signals the core that /hud is now served
