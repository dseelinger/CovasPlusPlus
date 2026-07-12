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
