"""Offline unit tests for the Tier-2 update check (covas/updates.py).

The semver compare and the GitHub release-JSON parse are pure — driven here with fake
payloads, no network (the conftest guard blocks sockets anyway). The `check_for_update`
fail-soft contract is exercised by monkeypatching `updates.requests` so no wire traffic
happens; a real fetch and the download/launch are on-hardware only.
"""
from __future__ import annotations

import pytest

from covas import updates
from covas.__version__ import __version__

# ---- semver compare -----------------------------------------------------------------

@pytest.mark.parametrize("latest,current", [
    ("1.0.1", "1.0.0"),
    ("1.1.0", "1.0.9"),
    ("2.0.0", "1.9.9"),
    ("v1.2.3", "1.2.2"),        # tolerate a leading 'v'
    ("1.2.3", "v1.2.2"),
    ("1.2", "1.1.9"),           # missing patch -> padded with 0
    ("1.2.3", "1.2.3-rc1"),     # a release outranks its own prerelease
    ("0.1.0", "0.0.9"),
])
def test_is_newer_true(latest, current):
    assert updates.is_newer(latest, current) is True


@pytest.mark.parametrize("latest,current", [
    ("1.0.0", "1.0.0"),         # equal is not newer
    ("1.0.0", "1.0.1"),         # older
    ("1.2.3", "1.2.3"),
    ("v1.2.3", "1.2.3"),        # same version, 'v' prefix
    ("1.2.3-rc1", "1.2.3"),     # a prerelease is NOT newer than its release
    ("1.9.9", "2.0.0"),
    ("", "1.0.0"),              # garbage fails soft to "not newer"
    ("not-a-version", "1.0.0"),
])
def test_is_newer_false(latest, current):
    assert updates.is_newer(latest, current) is False


# ---- release JSON parse -------------------------------------------------------------

def test_parse_release_basic():
    rel = updates.parse_release({
        "tag_name": "v1.2.0",
        "html_url": "https://example/releases/v1.2.0",
        "assets": [
            {"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
            {"name": "COVAS++ Setup.exe", "browser_download_url": "https://x/setup.exe"},
        ],
    })
    assert rel == {
        "tag": "v1.2.0",
        "url": "https://example/releases/v1.2.0",
        "asset_url": "https://x/setup.exe",
    }


def test_parse_release_falls_back_to_name_when_no_tag():
    rel = updates.parse_release({"name": "1.3.0", "assets": []})
    assert rel["tag"] == "1.3.0"
    assert rel["asset_url"] is None


@pytest.mark.parametrize("payload", [
    {"tag_name": "v1.2.0", "draft": True},        # drafts ignored
    {"tag_name": "v1.2.0", "prerelease": True},   # prereleases ignored
    {"assets": []},                               # no tag/name
    {},
    None,
    "not a dict",
])
def test_parse_release_none(payload):
    assert updates.parse_release(payload) is None


def test_installer_asset_is_first_exe():
    assert updates._installer_asset([
        {"name": "a.zip", "browser_download_url": "z"},
        {"name": "SETUP.EXE", "browser_download_url": "one"},   # case-insensitive
        {"name": "other.exe", "browser_download_url": "two"},
    ]) == "one"
    assert updates._installer_asset([]) is None
    assert updates._installer_asset([{"name": "readme.md"}]) is None


# ---- check_for_update: fail-soft, offline, and the happy path ------------------------

class _FakeResp:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._payload


def _patch_get(monkeypatch, resp=None, raises=None):
    def fake_get(url, **kw):
        if raises:
            raise raises
        return resp
    monkeypatch.setattr(updates.requests, "get", fake_get)


def test_check_offline_returns_no_update(monkeypatch):
    _patch_get(monkeypatch, raises=OSError("no network"))
    out = updates.check_for_update(current="1.0.0")
    assert out["available"] is False
    assert out["latest"] is None
    assert out["current"] == "1.0.0"


def test_check_http_error_fails_soft(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(exc=RuntimeError("500")))
    assert updates.check_for_update(current="1.0.0")["available"] is False


def test_check_newer_release_available(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp({
        "tag_name": "v2.0.0",
        "html_url": "https://example/2.0.0",
        "assets": [{"name": "COVAS++ Setup.exe", "browser_download_url": "https://x/s.exe"}],
    }))
    out = updates.check_for_update(current="1.0.0")
    assert out["available"] is True
    assert out["latest"] == "v2.0.0"
    assert out["url"] == "https://example/2.0.0"
    assert out["asset_url"] == "https://x/s.exe"


def test_check_same_version_not_available(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp({"tag_name": "1.0.0", "assets": []}))
    out = updates.check_for_update(current="1.0.0")
    assert out["available"] is False
    assert out["latest"] == "1.0.0"


def test_check_prerelease_payload_ignored(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp({"tag_name": "v9.9.9", "prerelease": True}))
    out = updates.check_for_update(current="1.0.0")
    assert out["available"] is False
    assert out["latest"] is None


def test_default_current_is_app_version(monkeypatch):
    # No newer release than ourselves -> not available, and the default `current` is wired
    # to the real __version__ (not a hardcoded string).
    _patch_get(monkeypatch, resp=_FakeResp({"tag_name": __version__, "assets": []}))
    out = updates.check_for_update()
    assert out["current"] == __version__
    assert out["available"] is False


# ---- installer host allowlist (security advisory: the download+exec sink) ------------

@pytest.mark.parametrize("url", [
    "https://github.com/dseelinger/CovasPlusPlus/releases/download/v1/COVAS++ Setup.exe",
    "https://objects.githubusercontent.com/github-production-release-asset/x/setup.exe",
    "https://codeload.githubusercontent.com/x",     # subdomain of githubusercontent.com
])
def test_trusted_asset_url_accepts_github(url):
    assert updates._is_trusted_asset_url(url) is True


@pytest.mark.parametrize("url", [
    "http://github.com/x/s.exe",                    # not https
    "https://attacker.example/malware.exe",         # foreign host
    "https://github.com.attacker.example/s.exe",    # suffix spoof — host is attacker.example
    "https://notgithub.com/s.exe",
    "file:///C:/Windows/System32/calc.exe",
    "", None, "not a url",
])
def test_trusted_asset_url_rejects_everything_else(url):
    assert updates._is_trusted_asset_url(url) is False


def test_download_refuses_untrusted_url_before_any_io(monkeypatch):
    """The sink guards itself: an untrusted URL raises BEFORE mkstemp/requests/Popen ever run, so a
    forged asset_url can't stream or execute anything."""
    called = {"get": False, "popen": False}
    monkeypatch.setattr(updates.requests, "get",
                        lambda *a, **k: called.__setitem__("get", True))
    monkeypatch.setattr(updates.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", True))
    with pytest.raises(ValueError, match="untrusted"):
        updates.download_and_launch_installer("https://attacker.example/malware.exe")
    assert called == {"get": False, "popen": False}
