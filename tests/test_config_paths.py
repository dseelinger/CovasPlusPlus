"""Unit tests for config path resolution (_resolve_paths / _abs).

Pure logic: relative config paths must resolve to absolute under the project
root so config.toml stays portable (no hardcoded C:\\Users\\... or username),
while already-absolute paths are left untouched.
"""
from __future__ import annotations

from pathlib import Path

from covas import config


# --- _abs ------------------------------------------------------------------

def test_abs_makes_relative_absolute_under_root():
    assert config._abs("logs") == str((config.ROOT / "logs").resolve())


def test_abs_leaves_absolute_untouched():
    already = str(config.ROOT.resolve())
    assert config._abs(already) == already


# --- _resolve_paths --------------------------------------------------------

def test_relative_path_field_resolved():
    cfg = {"personality": {"enabled": True, "file": "personality.txt"}}
    config._resolve_paths(cfg)
    result = cfg["personality"]["file"]
    assert Path(result).is_absolute()
    assert result == str((config.ROOT / "personality.txt").resolve())


def test_absolute_path_field_left_untouched():
    abs_path = str((config.ROOT / "voices" / "en_US.onnx").resolve())
    cfg = {"piper": {"model": abs_path}}
    config._resolve_paths(cfg)
    assert cfg["piper"]["model"] == abs_path


def test_empty_path_field_untouched():
    cfg = {"piper": {"model": ""}}
    config._resolve_paths(cfg)
    assert cfg["piper"]["model"] == ""


def test_missing_sections_do_not_crash():
    cfg: dict = {}
    config._resolve_paths(cfg)  # must not raise
    assert cfg == {}


def test_all_path_fields_resolved():
    cfg = {
        "personality": {"file": "personality.txt", "presets_file": "personalities/presets.md",
                        "campaign_file": "campaign.txt", "custom_dir": "personalities/custom"},
        "anthropic": {"api_key_file": "AnthropicAPIKey.txt"},
        "elevenlabs": {"api_key_file": "ElevenLabsAPIKey.txt"},
        "checklist": {"file": "ultimate_checklist.md"},
        "logging": {"dir": "logs"},
        "piper": {"model": "voices/en.onnx"},
        "whisper": {"download_root": "models"},
    }
    config._resolve_paths(cfg)
    for sec, key in config._PATH_FIELDS:
        assert Path(cfg[sec][key]).is_absolute(), f"{sec}.{key} not absolute"


# --- sound_cues (list or scalar) ------------------------------------------

def test_sound_cues_list_and_scalar_resolved():
    cfg = {"sound_cues": {
        "listening": ["sounds/a.wav", "sounds/b.wav"],
        "done": "sounds/d.wav",
    }}
    config._resolve_paths(cfg)
    for p in cfg["sound_cues"]["listening"]:
        assert Path(p).is_absolute()
    assert Path(cfg["sound_cues"]["done"]).is_absolute()
