"""Offline test for the control panel's typed-prompt endpoint (covas/web.py, issue #76).

`POST /api/prompt {text}` hands a typed prompt to the running core's `dispatch_text`, which runs
a full normal turn (skipping STT). The route trims and rejects empty/whitespace input, mirroring
the transcription guard. A stub core records what it was asked to dispatch — no App, no threads.
"""
from __future__ import annotations

import covas.web as web


class _StubCore:
    def __init__(self):
        self.dispatched: list[str] = []
        self.cfg = {}

    def dispatch_text(self, text: str) -> None:
        self.dispatched.append(text)


def _client():
    core = _StubCore()
    app = web.create_app(core)
    app.testing = True
    return app.test_client(), core


def test_prompt_dispatches_text():
    client, core = _client()
    r = client.post("/api/prompt", json={"text": "plot a route to Sol"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert core.dispatched == ["plot a route to Sol"]


def test_prompt_trims_surrounding_whitespace():
    client, core = _client()
    client.post("/api/prompt", json={"text": "  hello COVAS  "})
    assert core.dispatched == ["hello COVAS"]


def test_prompt_rejects_empty():
    client, core = _client()
    r = client.post("/api/prompt", json={"text": "   "})
    assert r.status_code == 400 and r.get_json()["ok"] is False
    assert core.dispatched == []                     # nothing dispatched on empty input


def test_prompt_rejects_missing_text():
    client, core = _client()
    r = client.post("/api/prompt", json={})
    assert r.status_code == 400
    assert core.dispatched == []
