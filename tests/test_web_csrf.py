"""Offline tests for the control panel's cross-origin (CSRF) guard — the fix for the drafted
security advisory (GHSA-3mxj-5926-rqmr).

The root cause was that NO route checked the request origin, so any page the Commander visited while
the panel was running could drive its mutating endpoints with a CORS-simple cross-origin POST. The
guard (`_csrf_origin_guard` in covas/web.py) refuses a state-changing request whose `Origin` (or
`Referer` fallback) isn't the panel's own origin, while letting non-browser clients — which send
neither header, and which a browser attack can't impersonate — through. These tests pin that
contract without a socket: a stub core and Flask's test client, injecting headers by hand.
"""
from __future__ import annotations

import covas.web as web


class _StubCore:
    def __init__(self, host="127.0.0.1", port=8765):
        self.cfg = {"ui": {"host": host, "port": port}}
        self.cancelled = 0

    def trigger_cancel(self) -> None:            # a cheap, side-effecting mutating endpoint
        self.cancelled += 1


def _client(**kw):
    core = _StubCore(**kw)
    app = web.create_app(core)
    app.testing = True
    return app.test_client(), core


# ---- the guard blocks forged cross-origin writes ---------------------------
def test_cross_origin_post_is_refused():
    c, core = _client()
    r = c.post("/api/cancel", headers={"Origin": "http://attacker.example"})
    assert r.status_code == 403 and r.get_json()["ok"] is False
    assert core.cancelled == 0                    # the side effect never fired


def test_cross_origin_referer_is_refused_when_origin_absent():
    c, core = _client()
    r = c.post("/api/cancel", headers={"Referer": "http://attacker.example/evil.html"})
    assert r.status_code == 403
    assert core.cancelled == 0


# ---- the guard allows legitimate same-origin and non-browser writes --------
def test_same_origin_post_passes():
    c, core = _client()
    r = c.post("/api/cancel", headers={"Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200 and core.cancelled == 1


def test_localhost_and_127_are_interchangeable():
    c, core = _client()
    assert c.post("/api/cancel", headers={"Origin": "http://localhost:8765"}).status_code == 200
    assert core.cancelled == 1


def test_origin_honors_configured_port():
    c, core = _client(port=9000)
    assert c.post("/api/cancel", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 403
    assert c.post("/api/cancel", headers={"Origin": "http://127.0.0.1:9000"}).status_code == 200
    assert core.cancelled == 1


def test_same_origin_over_lan_binding_passes():
    # Bound to a non-loopback host (documented: change [ui].host for LAN access). A LAN browser
    # loads the panel FROM that address, so its Origin == the Host it connected to — a real
    # same-origin write must pass even though the address isn't in the loopback allowlist.
    c, core = _client(host="0.0.0.0")
    r = c.post("/api/cancel", headers={"Host": "192.168.1.50:8765",
                                       "Origin": "http://192.168.1.50:8765"})
    assert r.status_code == 200 and core.cancelled == 1


def test_cross_origin_to_lan_binding_still_refused():
    # Same LAN binding, but a foreign page fetches the panel: Host is the panel's address, Origin is
    # the attacker's — mismatch, refused.
    c, core = _client(host="0.0.0.0")
    r = c.post("/api/cancel", headers={"Host": "192.168.1.50:8765",
                                       "Origin": "http://attacker.example"})
    assert r.status_code == 403 and core.cancelled == 0


def test_no_origin_header_passes_non_browser_client():
    # curl / the app itself / tests send no Origin or Referer; a browser CANNOT suppress Origin on
    # the cross-site POST this guards, so allowing header-less requests keeps local tooling working
    # without reopening the hole.
    c, core = _client()
    assert c.post("/api/cancel").status_code == 200 and core.cancelled == 1


def test_get_requests_are_not_guarded():
    # Read-only GETs never mutate state, so the guard leaves them alone even cross-origin.
    c, _ = _client()
    assert c.get("/api/hud", headers={"Origin": "http://attacker.example"}).status_code == 200
