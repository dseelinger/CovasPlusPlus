"""Offline tests for the control panel's masked "API keys" endpoints (covas/web.py, issue #23).

The card is WRITE-ONLY: `GET /api/keys` exposes a set/not-set boolean per provider and never the
key material; `POST /api/keys` writes the (DPAPI-encrypted) key or clears it. DPAPI is monkeypatched
so the encrypt/decrypt round-trip runs hermetically on any OS (real crypt32 is Windows-only).
"""
from __future__ import annotations

import base64

import pytest

import covas.web as web
from covas import dpapi


@pytest.fixture(autouse=True)
def fake_dpapi(monkeypatch):
    """Reversible in-process stand-in for Windows DPAPI (mirrors tests/test_firstrun.py)."""
    monkeypatch.setattr(dpapi, "protect",
                        lambda s: "DPAPI:" + base64.b64encode(s.encode()).decode("ascii"))
    monkeypatch.setattr(dpapi, "unprotect",
                        lambda v: base64.b64decode(v[len("DPAPI:"):]).decode("utf-8"))


class _StubCore:
    def __init__(self, tmp_path):
        # Every managed section points at a key file under tmp — no real key is touched.
        self.cfg = {
            "anthropic": {"api_key_file": str(tmp_path / "AnthropicAPIKey.txt")},
            "elevenlabs": {"api_key_file": str(tmp_path / "ElevenLabsAPIKey.txt")},
            "openai": {"api_key_file": str(tmp_path / "OpenAIAPIKey.txt")},
            "gemini": {"api_key_file": str(tmp_path / "GeminiAPIKey.txt")},
            "azure": {"api_key_file": str(tmp_path / "AzureSpeechKey.txt")},
            "cartesia": {"api_key_file": str(tmp_path / "CartesiaAPIKey.txt")},
            "cg": {"api_key_file": str(tmp_path / "InaraAPIKey.txt")},
        }


def _client(tmp_path):
    app = web.create_app(_StubCore(tmp_path))
    app.testing = True
    return app.test_client()


def test_keys_get_returns_booleans_only(tmp_path):
    body = _client(tmp_path).get("/api/keys").get_json()
    assert set(body["keys"]) == set(web._KEY_SECTIONS)
    assert all(v is False for v in body["keys"].values())   # nothing stored yet


def test_keys_post_sets_and_flips_badge(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/keys", json={"section": "anthropic", "value": "  sk-ant-secret  "})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"]
    assert body["keys"]["anthropic"] is True                # badge flipped set
    assert body["keys"]["gemini"] is False                  # others untouched
    # On disk it's encrypted, not plaintext.
    stored = (tmp_path / "AnthropicAPIKey.txt").read_text(encoding="utf-8")
    assert stored.startswith("DPAPI:") and "sk-ant-secret" not in stored


def test_keys_get_never_leaks_key_material(tmp_path):
    client = _client(tmp_path)
    client.post("/api/keys", json={"section": "cartesia", "value": "cartesia-raw-key-xyz"})
    raw = client.get("/api/keys").get_data(as_text=True)
    assert "cartesia-raw-key-xyz" not in raw                # the value never appears in the GET
    assert '"cartesia":true' in raw.replace(" ", "")        # only the boolean does


def test_keys_post_blank_is_noop(tmp_path):
    client = _client(tmp_path)
    client.post("/api/keys", json={"section": "openai", "value": "sk-existing"})
    body = client.post("/api/keys", json={"section": "openai", "value": "   "}).get_json()
    assert body["ok"] and body["keys"]["openai"] is True    # blank didn't clobber the stored key
    assert (tmp_path / "OpenAIAPIKey.txt").read_text(encoding="utf-8").startswith("DPAPI:")


def test_keys_post_clear_removes(tmp_path):
    client = _client(tmp_path)
    client.post("/api/keys", json={"section": "gemini", "value": "gm-key"})
    body = client.post("/api/keys", json={"section": "gemini", "clear": True}).get_json()
    assert body["ok"] and body["keys"]["gemini"] is False   # badge flipped back to not-set
    assert (tmp_path / "GeminiAPIKey.txt").read_text(encoding="utf-8") == ""


def test_keys_post_unknown_section_rejected(tmp_path):
    r = _client(tmp_path).post("/api/keys", json={"section": "nope", "value": "x"})
    assert r.status_code == 400 and r.get_json()["ok"] is False
