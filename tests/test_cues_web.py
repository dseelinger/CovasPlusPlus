"""I8: the panel's "Open cues folder" endpoint (offline; os.startfile stubbed).

The route opens <data_dir>/sounds after ensuring the per-type skeleton, and reports the path so
the client can show it even when the OS can't open a file manager (non-Windows / headless).

Also covers #109: the sibling "Reload cues" endpoint that re-scans + hot-swaps a live CuePlayer
without a restart.
"""
from __future__ import annotations

import covas.web as web
from covas.audio import CUE_TYPES, CuePlayer


class _StubCore:
    def __init__(self, tmp_path, *, with_cues=False, ambient_counts=None):
        # content_root is the C11/I8 test seam: sounds land under it, not the repo.
        self.cfg = {"audio": {"content_root": str(tmp_path)}}
        # A real CuePlayer (no mixer -> legacy sd path, never opened since we never call play())
        # so /api/cues/reload has something to reload against, mirroring the app's own wiring.
        self.cues = CuePlayer(self.cfg) if with_cues else None
        self._ambient_counts = ambient_counts

    def reload_audio_content(self) -> dict:
        """Stub of App.reload_audio_content (#110): the endpoint calls it for the ambient side."""
        return dict(self._ambient_counts or {})


def _client(tmp_path, *, with_cues=False, ambient_counts=None):
    app = web.create_app(_StubCore(tmp_path, with_cues=with_cues, ambient_counts=ambient_counts))
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


# ---- #109: /api/cues/reload — pairs with open, no restart needed to pick up a drop-in ---------
def test_reload_cues_reports_per_type_counts(tmp_path):
    r = _client(tmp_path, with_cues=True).post("/api/cues/reload")
    body = r.get_json()
    assert r.status_code == 200 and body["ok"]
    assert set(body["counts"]) == set(CUE_TYPES)   # every cue type reported, even at 0


def test_reload_cues_fail_soft_when_no_cue_player_wired(tmp_path):
    """core.cues can be absent (e.g. the audio layer never started) — the endpoint still 200s
    with empty counts rather than 500ing."""
    r = _client(tmp_path, with_cues=False).post("/api/cues/reload")
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] and body["counts"] == {}


# ---- #110: the SAME endpoint also reloads the C11 ambient content (SFX/music/chatter/threat) ----
def test_reload_also_returns_ambient_content_counts(tmp_path):
    """One Reload cues action refreshes BOTH surfaces: turn cues (counts) + ambient (content)."""
    amb = {"sfx": 3, "music": 1, "chatter": 4, "threat": 2}
    r = _client(tmp_path, with_cues=True, ambient_counts=amb).post("/api/cues/reload")
    body = r.get_json()
    assert r.status_code == 200 and body["ok"]
    assert set(body["counts"]) == set(CUE_TYPES)      # turn-stage side unchanged
    assert body["content"] == amb                     # ambient side reported alongside
