"""Unit tests for the packaged-app user-data-dir split (I1).

A source run must be unchanged (app_dir == data_dir == project root); a frozen/relocated
run must seed config into the writable data dir and resolve WRITABLE fields there while
SHIPPED-asset fields stay under the app dir. Frozen behavior is simulated via the
COVAS_APP_DIR / COVAS_DATA_DIR env seam (and one test flips sys.frozen directly).
"""
from __future__ import annotations

import sys
from pathlib import Path

from covas import config

_MINIMAL_CONFIG = """\
[personality]
file = "personality.txt"
presets_file = "personalities/presets.md"
custom_dir = "personalities/custom"
campaign_file = "campaign.txt"

[elevenlabs]
api_key_file = "ElevenLabsAPIKey.txt"

[checklist]
file = "ultimate_checklist.md"

[logging]
dir = "logs"

[sound_cues]
listen = ["sounds/voiceinput1.wav"]
"""


def _clear_env(mp):
    mp.delenv("COVAS_APP_DIR", raising=False)
    mp.delenv("COVAS_DATA_DIR", raising=False)


# --- source run: nothing changes ---------------------------------------------

def test_source_mode_roots_are_the_project_root(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert config.app_dir() == config._PROJECT_ROOT
    assert config.data_dir() == config._PROJECT_ROOT
    assert config.app_dir() == config.data_dir()


# --- env override seam --------------------------------------------------------

def test_env_overrides_both_roots(monkeypatch, tmp_path):
    app, data = tmp_path / "app", tmp_path / "data"
    monkeypatch.setenv("COVAS_APP_DIR", str(app))
    monkeypatch.setenv("COVAS_DATA_DIR", str(data))
    assert config.app_dir() == app
    assert config.data_dir() == data
    assert config.config_path() == data / "config.toml"
    assert config.overrides_path() == data / "overrides.json"


# --- frozen branch selection --------------------------------------------------

def test_frozen_branch_uses_meipass_and_appdata(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    bundle, appdata = tmp_path / "bundle", tmp_path / "roaming"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("APPDATA", str(appdata))
    assert config.app_dir() == bundle
    assert config.data_dir() == appdata / "COVAS++"


# --- relocation: seed + split resolution -------------------------------------

def test_load_config_seeds_and_relocates(monkeypatch, tmp_path):
    app, data = tmp_path / "app", tmp_path / "data"
    app.mkdir()
    (app / "config.toml").write_text(_MINIMAL_CONFIG, encoding="utf-8")
    monkeypatch.setenv("COVAS_APP_DIR", str(app))
    monkeypatch.setenv("COVAS_DATA_DIR", str(data))

    cfg = config.load_config()

    # config seeded into the writable data dir
    assert (data / "config.toml").exists()

    # WRITABLE fields resolve under data_dir
    for resolved in (cfg["logging"]["dir"], cfg["personality"]["file"],
                     cfg["personality"]["custom_dir"], cfg["elevenlabs"]["api_key_file"],
                     cfg["checklist"]["file"], cfg["sound_cues"]["listen"][0]):
        assert Path(resolved).is_relative_to(data.resolve()), resolved

    # SHIPPED-asset field resolves under app_dir
    assert Path(cfg["personality"]["presets_file"]).is_relative_to(app.resolve())


def test_seed_does_not_overwrite_existing_user_config(monkeypatch, tmp_path):
    app, data = tmp_path / "app", tmp_path / "data"
    app.mkdir(); data.mkdir()
    (app / "config.toml").write_text(_MINIMAL_CONFIG, encoding="utf-8")
    # user already has a config with a customized logging dir — must be preserved
    (data / "config.toml").write_text(
        _MINIMAL_CONFIG.replace('dir = "logs"', 'dir = "CUSTOM_LOGS"'), encoding="utf-8")
    monkeypatch.setenv("COVAS_APP_DIR", str(app))
    monkeypatch.setenv("COVAS_DATA_DIR", str(data))

    cfg = config.load_config()
    assert "CUSTOM_LOGS" in cfg["logging"]["dir"]


def test_overrides_loaded_from_data_dir(monkeypatch, tmp_path):
    app, data = tmp_path / "app", tmp_path / "data"
    app.mkdir(); data.mkdir()
    (app / "config.toml").write_text(_MINIMAL_CONFIG, encoding="utf-8")
    (data / "config.toml").write_text(_MINIMAL_CONFIG, encoding="utf-8")
    (data / "overrides.json").write_text('{"logging": {"dir": "OVR_LOGS"}}', encoding="utf-8")
    monkeypatch.setenv("COVAS_APP_DIR", str(app))
    monkeypatch.setenv("COVAS_DATA_DIR", str(data))

    cfg = config.load_config()
    assert "OVR_LOGS" in cfg["logging"]["dir"]


def test_save_overrides_writes_under_data_dir(monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setenv("COVAS_DATA_DIR", str(data))
    monkeypatch.setenv("COVAS_APP_DIR", str(tmp_path / "app"))

    config.save_overrides({"personality": {"file": "x.txt"}})
    assert (data / "overrides.json").exists()
    assert config.load_overrides() == {"personality": {"file": "x.txt"}}


# --- upgrade migration: shipped config is the BASE so new sections always appear --------------

def test_stale_user_config_gains_new_bundled_sections(monkeypatch, tmp_path):
    """The upgrade bug fix: a data-dir config seeded by an OLDER build (missing sections the current
    shipped config has — e.g. a pre-#12 config has no [openai], so the API-keys card had no
    `api_key_file` to write and the key couldn't be set at all) must gain those sections from the
    SHIPPED config on load, while the user's own edits still win on top."""
    app, data = tmp_path / "app", tmp_path / "data"
    app.mkdir(); data.mkdir()
    # Shipped (current) config carries the new [openai] section...
    (app / "config.toml").write_text(
        _MINIMAL_CONFIG + '\n[openai]\napi_key_file = "OpenAIAPIKey.txt"\n'
        'base_url = "https://api.openai.com/v1"\n', encoding="utf-8")
    # ...but the user's stale config predates it (no [openai]) and has one personal edit.
    (data / "config.toml").write_text(
        _MINIMAL_CONFIG.replace('dir = "logs"', 'dir = "MY_LOGS"'), encoding="utf-8")
    monkeypatch.setenv("COVAS_APP_DIR", str(app))
    monkeypatch.setenv("COVAS_DATA_DIR", str(data))

    cfg = config.load_config()

    # the new section appeared from the shipped base, and its WRITABLE key path resolved to data_dir
    assert cfg["openai"]["base_url"] == "https://api.openai.com/v1"
    assert Path(cfg["openai"]["api_key_file"]).is_relative_to(data.resolve())
    # the user's own edit still wins over the shipped default
    assert "MY_LOGS" in cfg["logging"]["dir"]


def test_source_run_does_not_pull_in_bundled_base(monkeypatch, tmp_path):
    """In a source run app_dir()==data_dir() (same file), so the shipped-base merge is a no-op —
    load_config reads only the one config (plus overrides). Guards the CONFIG_PATH-patch seam that
    other tests rely on: patching CONFIG_PATH must NOT drag in the repo's full config.toml."""
    _clear_env(monkeypatch)
    cfgfile = tmp_path / "config.toml"
    cfgfile.write_text('[logging]\ndir = "ONLY_LOGS"\n', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfgfile)
    monkeypatch.setattr(config, "OVERRIDES_PATH", tmp_path / "overrides.json")

    cfg = config.load_config()
    assert "ONLY_LOGS" in cfg["logging"]["dir"]
    assert "openai" not in cfg          # repo's full config.toml did NOT leak in (app==data)
