"""Unit tests for the engineer unlock dashboard web surface (issue #133) — offline, free.

Exercises the two Flask endpoints backing the control-panel Engineering page:
  * `/engineers/state` — the join view-model as JSON, joining the bundled reference table with
    the live EngineerProgress map on a (stub) EDContext. FAIL SOFT: no `ed_ctx`, a raising
    context, or no progress yet all degrade to `has_progress: false` with every engineer shown
    locked — never a 500.
  * `/engineers` — the page itself, which must render offline and be self-contained (no external
    asset URL, since Chromium here has no network).

A tiny stub core stands in for `App`: the route only reaches `core.cfg` (theme) and
`core.ed_ctx.engineer_progress`.
"""
from __future__ import annotations

import covas.web as web
from covas.ed.engineers import ENGINEERS, EngineerStatus


class _StubCtx:
    def __init__(self, progress=None, raises=False):
        self._progress = progress or {}
        self._raises = raises

    def engineer_progress(self):
        if self._raises:
            raise RuntimeError("ctx boom")
        return dict(self._progress)


class _StubCore:
    def __init__(self, ed_ctx=None):
        self.cfg = {"ui": {"theme": "dark"}}
        self.ed_ctx = ed_ctx


def _client(core):
    app = web.create_app(core)
    app.testing = True
    return app.test_client()


def test_state_joins_live_progress():
    ctx = _StubCtx({"Felicity Farseer": EngineerStatus(progress="Unlocked", rank=5),
                    "The Dweller": EngineerStatus(progress="Invited")})
    r = _client(_StubCore(ctx)).get("/engineers/state")
    assert r.status_code == 200
    body = r.get_json()
    assert body["has_progress"] is True
    assert body["total"] == len(ENGINEERS)
    rows = {e["name"]: e for e in body["engineers"]}
    assert rows["Felicity Farseer"]["group"] == "unlocked"
    assert rows["Felicity Farseer"]["grade"] == 5
    assert rows["The Dweller"]["group"] == "in_progress"


def test_state_fail_soft_when_no_ed_ctx():
    # ED monitoring off => core.ed_ctx is None; the endpoint must still 200 with every engineer
    # locked and has_progress false.
    r = _client(_StubCore(ed_ctx=None)).get("/engineers/state")
    assert r.status_code == 200
    body = r.get_json()
    assert body["has_progress"] is False
    assert body["counts"]["locked"] == len(ENGINEERS)


def test_state_fail_soft_when_ctx_raises():
    r = _client(_StubCore(_StubCtx(raises=True))).get("/engineers/state")
    assert r.status_code == 200
    assert r.get_json()["has_progress"] is False


def test_page_renders_and_is_self_contained():
    r = _client(_StubCore(_StubCtx())).get("/engineers")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "/engineers/state" in html          # the page polls the JSON endpoint
    lowered = html.lower()
    # Self-contained: only the bundled /static/theme.css, no external asset request.
    for needle in ("http://", "https://", "//cdn", "@import"):
        assert needle not in lowered, f"engineers page must not reference external asset: {needle!r}"
