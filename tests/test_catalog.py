"""Fetched-catalog resolver tests (issues #92 + #88).

All OFFLINE: the provider fetchers are monkeypatched, so sentinel→options wiring, the fail-soft
contract (None + reason on any error, never a raise), and the pure parse helpers are all exercised
with no key or socket. Proves the two guarantees the issues care about: catalog values resolve from a
fake payload, and a fetch failure degrades to (None, reason) so the UI keeps free-text.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from covas import catalog
from covas import settings_schema as schema
from covas.providers import gemini_llm, openai_llm


# ---- pure parse helpers ----------------------------------------------------
def test_parse_openai_models_dedupes_and_orders():
    payload = {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}, {"id": "gpt-4o-mini"},
                        {"nope": 1}, "x"]}
    assert openai_llm.parse_openai_models(payload) == ["gpt-4o-mini", "gpt-4o"]
    assert openai_llm.parse_openai_models({}) == []


def test_parse_gemini_models_strips_prefix():
    payload = {"models": [{"name": "models/gemini-2.5-flash"}, {"name": "gemini-2.5-pro"}]}
    assert gemini_llm.parse_models_list(payload) == ["gemini-2.5-flash", "gemini-2.5-pro"]


# ---- mic picker (issue #89) ------------------------------------------------
def test_input_devices_source_from_list_input_devices(monkeypatch):
    """OPT_INPUT_DEVICES resolves from firstrun.list_input_devices() — local, no network/key."""
    from covas import firstrun
    monkeypatch.setattr(firstrun, "list_input_devices",
                        lambda: [{"index": 0, "name": "Headset Mic"},
                                 {"index": 1, "name": "Webcam Mic"}])
    opts, err = catalog.resolve(schema.OPT_INPUT_DEVICES, {})
    assert err is None
    assert [o["value"] for o in opts] == ["Headset Mic", "Webcam Mic"]
    assert all(o["label"] == o["value"] for o in opts)


def test_input_devices_prefers_full_name_over_truncated_mme_clone(monkeypatch):
    """The truncated MME clone (a strict prefix of the full name) is dropped so the audible
    full-name device is what the picker offers — the whole reason for issue #89."""
    from covas import firstrun
    monkeypatch.setattr(firstrun, "list_input_devices", lambda: [
        {"index": 0, "name": "Microphone (Logi 4K Stream Edit"},   # truncated MME copy (silent)
        {"index": 1, "name": "Microphone (Logi 4K Stream Edition)"},  # full WASAPI name
        {"index": 2, "name": "Microphone (Logi 4K Stream Edition)"},  # exact dup (DirectSound)
        {"index": 3, "name": "Headset"},
    ])
    values = [o["value"] for o in catalog.resolve(schema.OPT_INPUT_DEVICES, {})[0]]
    assert values == ["Microphone (Logi 4K Stream Edition)", "Headset"]


def test_input_devices_fail_soft_when_no_audio(monkeypatch):
    """A device-enumeration failure degrades to (None, reason), never a raise — combobox keeps
    the current value typeable."""
    from covas import firstrun

    def _boom():
        raise OSError("no PortAudio")

    monkeypatch.setattr(firstrun, "list_input_devices", _boom)
    opts, err = catalog.resolve(schema.OPT_INPUT_DEVICES, {})
    assert opts is None and err


def test_input_device_setting_is_editable_combobox():
    """The mic setting is a combobox so a saved-but-unplugged device (or blank = default) stays
    valid instead of being rejected against the live list (issue #89)."""
    s = schema.by_key["audio.input_device"]
    assert s.options_source == schema.OPT_INPUT_DEVICES
    assert schema.is_combobox(s)


# ---- Piper voices picker (issue #120) --------------------------------------
def test_piper_voices_scans_onnx_with_sibling_json(tmp_path):
    """`@piper_voices` lists each *.onnx that has its sibling *.onnx.json beside it (the config)."""
    from covas.providers.piper_tts import list_piper_voices
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"")
    (tmp_path / "en_US-lessac-medium.onnx.json").write_text("{}")
    (tmp_path / "orphan.onnx").write_bytes(b"")           # no sibling json -> excluded
    (tmp_path / "notes.txt").write_text("x")              # not an onnx -> excluded
    got = list_piper_voices(str(tmp_path))
    assert [o["label"] for o in got] == ["en_US-lessac-medium.onnx"]
    assert got[0]["value"].endswith("en_US-lessac-medium.onnx")


def test_piper_voices_fail_soft_on_missing_or_blank_dir():
    """No dir / blank / unreadable -> [] (never raises), so the picker degrades to type-a-path."""
    from covas.providers.piper_tts import list_piper_voices
    assert list_piper_voices("") == []
    assert list_piper_voices(str(Path("no") / "such" / "dir")) == []


def test_piper_voices_resolve_uses_configured_voice_dir(tmp_path):
    """catalog.resolve(@piper_voices) scans the directory of the configured [piper].model, fail-soft."""
    (tmp_path / "a.onnx").write_bytes(b"")
    (tmp_path / "a.onnx.json").write_text("{}")
    opts, err = catalog.resolve(schema.OPT_PIPER_VOICES,
                                {"piper": {"model": str(tmp_path / "a.onnx")}})
    assert err is None and [o["label"] for o in opts] == ["a.onnx"]
    # No configured voice -> empty list, still no error (fail-soft).
    assert catalog.resolve(schema.OPT_PIPER_VOICES, {"piper": {"model": ""}}) == ([], None)


def test_piper_voice_field_is_searchable_custom_combobox():
    """piper.model is now an enum backed by @piper_voices, and accepts a custom typed path (#120)."""
    s = schema.by_key["piper.model"]
    assert s.type == "enum" and s.options_source == schema.OPT_PIPER_VOICES
    assert schema.is_combobox(s)  # a typed .onnx path (outside the scan) stays valid
    val, err = schema.validate_value(s, "voices/custom.onnx")
    assert err is None and val == "voices/custom.onnx"


# ---- Player-DM voice picker (issue #120) -----------------------------------
def test_player_dm_voice_is_searchable_enum_accepting_custom():
    """audio.voices.player_ref: an @elevenlabs_voices enum that still validates an EL id, a Piper
    path, and blank (=random) — its validator is combobox-lenient (allow_custom), not strict."""
    s = schema.by_key["audio.voices.player_ref"]
    assert s.type == "enum" and s.options_source == schema.OPT_EL_VOICES
    assert schema.is_combobox(s)          # not strict server-side
    for probe in ("EXAVITQu4vr4xnSDxMaL",       # a real ElevenLabs voice id
                  "voices/dm.onnx",             # a Piper .onnx path
                  ""):                          # blank = random session voice
        val, err = schema.validate_value(s, probe)
        assert err is None and val == probe
    # blank (system default) and an unlisted name are both accepted (combobox type-check only).
    assert schema.validate_value(s, "") == ("", None)
    assert schema.validate_value(s, "Some Unplugged Mic") == ("Some Unplugged Mic", None)


# ---- static sources (always available, no network) -------------------------
def test_base_urls_source_is_static_presets():
    opts, err = catalog.resolve(schema.OPT_OPENAI_BASE_URLS, {})
    assert err is None
    labels = [o["label"] for o in opts]
    assert "OpenAI" in labels and "Groq" in labels and "OpenRouter" in labels
    assert all("value" in o and "label" in o for o in opts)


def test_unknown_source_returns_reason_not_raise():
    opts, err = catalog.resolve("@nope", {})
    assert opts is None and "unknown source" in err


# ---- model catalogs (fetchers monkeypatched) -------------------------------
def test_openai_models_resolve_with_key(monkeypatch):
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: "k")
    monkeypatch.setattr(openai_llm, "list_openai_models", lambda url, key, **k: ["gpt-4o", "gpt-4o-mini"])
    opts, err = catalog.resolve(schema.OPT_OPENAI_MODELS, {"openai": {"base_url": "https://x/v1"}})
    assert err is None
    assert [o["value"] for o in opts] == ["gpt-4o", "gpt-4o-mini"]


def test_openai_models_no_key_degrades(monkeypatch):
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: None)
    opts, err = catalog.resolve(schema.OPT_OPENAI_MODELS, {})
    assert opts is None and "no OpenAI key" in err


def test_openai_models_base_url_override_is_used(monkeypatch):
    seen = {}
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: "k")
    monkeypatch.setattr(openai_llm, "list_openai_models",
                        lambda url, key, **k: seen.setdefault("url", url) and [] or [])
    catalog.resolve(schema.OPT_OPENAI_MODELS, {"openai": {"base_url": "https://cfg/v1"}},
                    base_url="https://override/v1")
    assert seen["url"] == "https://override/v1"   # the page's pending base_url wins (refetch, #92)


def test_fetch_failure_is_failsoft(monkeypatch):
    monkeypatch.setattr("covas.firstrun.openai_key", lambda cfg: "k")

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(openai_llm, "list_openai_models", boom)
    opts, err = catalog.resolve(schema.OPT_OPENAI_MODELS, {})
    assert opts is None and "connection refused" in err   # reason surfaced, no raise


def test_gemini_models_resolve(monkeypatch):
    monkeypatch.setattr("covas.firstrun.gemini_key", lambda cfg: "k")
    monkeypatch.setattr(gemini_llm, "list_gemini_models", lambda url, key, **k: ["gemini-2.5-flash"])
    opts, err = catalog.resolve(schema.OPT_GEMINI_MODELS, {})
    assert err is None and opts[0]["value"] == "gemini-2.5-flash"


# ---- anthropic: live preferred, static fallback ----------------------------
def test_anthropic_live_merges_static(monkeypatch):
    monkeypatch.setattr("covas.llm.list_anthropic_models", lambda cfg, **k: ["claude-new-1"])
    cfg = {"anthropic": {"available_models": ["claude-sonnet-5", "claude-new-1"]}}
    opts, err = catalog.resolve(schema.OPT_ANTHROPIC_MODELS_LIVE, cfg)
    vals = [o["value"] for o in opts]
    assert err is None and vals == ["claude-new-1", "claude-sonnet-5"]   # live first, static extras, deduped


def test_anthropic_falls_back_to_static_when_live_fails(monkeypatch):
    def boom(cfg, **k):
        raise RuntimeError("no key")

    monkeypatch.setattr("covas.llm.list_anthropic_models", boom)
    cfg = {"anthropic": {"available_models": ["claude-sonnet-5"]}}
    opts, err = catalog.resolve(schema.OPT_ANTHROPIC_MODELS_LIVE, cfg)
    assert err is None and [o["value"] for o in opts] == ["claude-sonnet-5"]


# ---- voice catalogs --------------------------------------------------------
def test_edge_voices_no_key(monkeypatch):
    monkeypatch.setattr("covas.providers.edge_tts.list_edge_voices",
                        lambda prefix="en-": [{"ref": "en-US-AriaNeural", "name": "Aria",
                                               "gender": "Female", "locale": "en-US"}])
    opts, err = catalog.resolve(schema.OPT_EDGE_VOICES, {})
    assert err is None
    assert opts[0]["value"] == "en-US-AriaNeural" and "en-US" in opts[0]["meta"]


def test_edge_voices_follow_reply_language_locale(monkeypatch):
    """Locale-aware pool (#198): a German reply language filters the Edge catalog to de-* voices."""
    seen = {}

    def _fake(prefix="en-"):
        seen["prefix"] = prefix
        return [{"ref": "de-DE-KatjaNeural", "name": "Katja", "gender": "Female", "locale": "de-DE"}]

    monkeypatch.setattr("covas.providers.edge_tts.list_edge_voices", _fake)
    opts, err = catalog.resolve(schema.OPT_EDGE_VOICES, {"language": {"reply": "German"}})
    assert err is None and seen["prefix"] == "de-"          # steered the pool to German
    assert opts[0]["value"] == "de-DE-KatjaNeural"
    # English (default) keeps the historical en- pool.
    catalog.resolve(schema.OPT_EDGE_VOICES, {"language": {"reply": "English"}})
    assert seen["prefix"] == "en-"


def test_azure_voices_need_key_and_region(monkeypatch):
    monkeypatch.setattr("covas.firstrun.azure_key", lambda cfg: None)
    opts, err = catalog.resolve(schema.OPT_AZURE_VOICES, {"azure": {"region": "eastus"}})
    assert opts is None and "region" in err   # key missing -> degrade

    monkeypatch.setattr("covas.firstrun.azure_key", lambda cfg: "k")
    monkeypatch.setattr("covas.providers.azure_tts.list_azure_voices",
                        lambda key, region, prefix="en-": [{"ref": "en-GB-RyanNeural", "name": "Ryan",
                                                            "gender": "Male", "locale": "en-GB"}])
    opts, err = catalog.resolve(schema.OPT_AZURE_VOICES, {"azure": {"region": "uksouth"}})
    assert err is None and opts[0]["value"] == "en-GB-RyanNeural"


def test_cartesia_voices_key_gated(monkeypatch):
    monkeypatch.setattr("covas.firstrun.cartesia_key", lambda cfg: None)
    opts, err = catalog.resolve(schema.OPT_CARTESIA_VOICES, {})
    assert opts is None and "Cartesia" in err


# ---- option_pairs adapter for the voice layer ------------------------------
def test_option_pairs_maps_and_failsofts(monkeypatch):
    monkeypatch.setattr(catalog, "resolve", lambda s, cfg, **k: ([{"value": "v", "label": "L"}], None))
    assert catalog.option_pairs("@x", {}) == [("v", "L")]
    monkeypatch.setattr(catalog, "resolve", lambda s, cfg, **k: (None, "offline"))
    assert catalog.option_pairs("@x", {}) is None


# ---- combobox contract: unlisted value stays valid (issue #92) -------------
@pytest.mark.parametrize("key", ["openai.model", "gemini.model", "edge.voice", "azure.voice",
                                 "cartesia.voice", "openai.base_url"])
def test_combobox_accepts_custom_value(key):
    s = schema.by_key[key]
    assert schema.is_combobox(s)
    # A value NOT in any fetched list must validate (custom / at-your-own-risk escape hatch).
    val, err = schema.validate_value(s, "totally-custom-value", options=["something-else"])
    assert err is None and val == "totally-custom-value"


def test_static_tts_enums_are_strict():
    # openai_tts.voice / model are fixed sets — a bogus value IS rejected (not a combobox).
    s = schema.by_key["openai_tts.voice"]
    assert not schema.is_combobox(s)
    assert schema.validate_value(s, "robot")[0] is None


# ---- /api/catalog base_url allowlist (security advisory: key exfiltration) --------------
# `base_url` flows to an `Authorization: Bearer <key>` fetch, and GET /api/catalog is reachable
# cross-origin, so a free-form override would leak the user's key to an attacker's host. The web
# boundary only honors a base_url that is a known preset or the user's OWN configured endpoint.
class _CatalogCore:
    def __init__(self, configured_base_url=""):
        self.cfg = {"openai": {"base_url": configured_base_url}}


def _catalog_client(configured_base_url=""):
    import covas.web as web
    app = web.create_app(_CatalogCore(configured_base_url))
    app.testing = True
    return app.test_client()


def test_catalog_rejects_foreign_base_url_without_fetching(monkeypatch):
    import covas.web as web
    called = {"resolve": False}
    monkeypatch.setattr(web.catalog, "resolve",
                        lambda *a, **k: called.__setitem__("resolve", True) or ([], None))
    r = _catalog_client().get("/api/catalog?source=@openai_models&base_url=https://attacker.example")
    body = r.get_json()
    assert r.status_code == 200                       # fail-soft contract: always 200 + {options,error}
    assert body["options"] == [] and "not an allowed endpoint" in body["error"]
    assert called["resolve"] is False                 # never resolved -> key never attached anywhere


def _record_base_url(seen):
    def _resolve(source, cfg, base_url=None):
        seen["base_url"] = base_url
        return [], None
    return _resolve


def test_catalog_allows_preset_base_url(monkeypatch):
    import covas.web as web
    seen = {}
    monkeypatch.setattr(web.catalog, "resolve", _record_base_url(seen))
    _catalog_client().get("/api/catalog?source=@openai_models&base_url=https://api.groq.com/openai/v1")
    assert seen["base_url"] == "https://api.groq.com/openai/v1"   # a known preset passes through


def test_catalog_allows_users_own_configured_base_url(monkeypatch):
    import covas.web as web
    seen = {}
    monkeypatch.setattr(web.catalog, "resolve", _record_base_url(seen))
    _catalog_client("https://myllm.local/v1").get(
        "/api/catalog?source=@openai_models&base_url=https://myllm.local/v1")
    assert seen["base_url"] == "https://myllm.local/v1"           # matches the configured endpoint
