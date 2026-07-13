"""I8: the panel's "Open cues folder" endpoint (offline; os.startfile stubbed).

The route opens <data_dir>/sounds after ensuring the per-type skeleton, and reports the path so
the client can show it even when the OS can't open a file manager (non-Windows / headless).
"""
from __future__ import annotations

import covas.web as web
from covas.audio import CUE_TYPES


class _StubCore:
    def __init__(self, tmp_path):
        # content_root is the C11/I8 test seam: sounds land under it, not the repo.
        self.cfg = {"audio": {"content_root": str(tmp_path)}}


def _client(tmp_path):
    app = web.create_app(_StubCore(tmp_path))
    app.testing = True
    return app.test_client()


def test_open_cues_creates_skeleton_and_reports_opened(tmp_path, monkeypatch):
    opened = {}
    monkeypatch.setattr("os.startfile", lambda p: opened.setdefault("path", p), raising=False)
    r = _client(tmp_path).post("/api/cues/open")
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["opened"] is True
    assert body["path"].replace("\\", "/").endswith("sounds")
    # skeleton exists for every cue type, and startfile got the sounds dir
    for t in CUE_TYPES:
        assert (tmp_path / "sounds" / t).is_dir()
    assert opened["path"].replace("\\", "/").endswith("sounds")


def test_open_cues_fail_soft_when_os_cannot_open(tmp_path, monkeypatch):
    def _boom(_p):
        raise OSError("no file manager here")

    monkeypatch.setattr("os.startfile", _boom, raising=False)
    body = _client(tmp_path).post("/api/cues/open").get_json()
    assert body["ok"] and body["opened"] is False and body["path"]  # still returns the path
