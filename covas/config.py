"""Load config.toml and layer overrides.json on top (UI writes overrides.json).

Two roots keep a packaged (frozen) build's writable state OUT of the read-only install
tree, while a source run behaves exactly as before:
  * app_dir()  — read-only SHIPPED assets (the bundle, or the project root in a source run)
  * data_dir() — user-WRITABLE state (config, keys, logs, overrides, drop-in content)
In a source run both are the project root, so nothing changes for dev/tests. The env vars
COVAS_APP_DIR / COVAS_DATA_DIR override either — a test seam, and a way to relocate state
without touching the repo (parity with the [audio].content_root seam).
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Read-only asset base. Frozen: the PyInstaller bundle dir. Source: the project root."""
    env = os.environ.get("COVAS_APP_DIR")
    if env:
        return Path(env)
    if _frozen():
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).resolve().parent
    return _PROJECT_ROOT


def data_dir() -> Path:
    """Writable state base. Frozen: %APPDATA%\\COVAS++. Source: the project root."""
    env = os.environ.get("COVAS_DATA_DIR")
    if env:
        return Path(env)
    if _frozen():
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "COVAS++"
    return _PROJECT_ROOT


# Back-compat: ROOT historically meant "the project root". It now aliases the read-only
# asset base — identical in a source run. CONFIG_PATH/OVERRIDES_PATH are patchable module
# constants (some tests setattr them); config_path()/overrides_path() honor a COVAS_DATA_DIR
# env override at call time, else return these constants.
ROOT = app_dir()
CONFIG_PATH = data_dir() / "config.toml"
OVERRIDES_PATH = data_dir() / "overrides.json"


def config_path() -> Path:
    env = os.environ.get("COVAS_DATA_DIR")
    return (Path(env) / "config.toml") if env else CONFIG_PATH


def overrides_path() -> Path:
    env = os.environ.get("COVAS_DATA_DIR")
    return (Path(env) / "overrides.json") if env else OVERRIDES_PATH


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# Config path fields stored relative to a root and resolved to absolute at load time.
# Keeps config.toml portable (no hardcoded C:\Users\... or username) while consumers get
# absolute paths. Split by root: WRITABLE user state -> data_dir; SHIPPED assets -> app_dir.
_DATA_PATH_FIELDS = (
    ("personality", "file"),
    ("personality", "campaign_file"),
    ("personality", "custom_dir"),
    ("anthropic", "api_key_file"),
    ("elevenlabs", "api_key_file"),
    ("azure", "api_key_file"),
    ("openai_tts", "api_key_file"),
    ("checklist", "file"),
    ("logging", "dir"),
    ("piper", "model"),
    ("whisper", "download_root"),
)
_APP_PATH_FIELDS = (
    ("personality", "presets_file"),
)
# Back-compat: the combined set (some callers/tests iterate it as one).
_PATH_FIELDS = _DATA_PATH_FIELDS + _APP_PATH_FIELDS


def _abs(p: str, base: Path | None = None) -> str:
    base = base if base is not None else data_dir()
    return p if Path(p).is_absolute() else str((base / p).resolve())


def _resolve_paths(cfg: dict) -> None:
    data, app = data_dir(), app_dir()
    for sec, key in _DATA_PATH_FIELDS:
        v = cfg.get(sec, {}).get(key)
        if isinstance(v, str) and v:
            cfg[sec][key] = _abs(v, data)
    for sec, key in _APP_PATH_FIELDS:
        v = cfg.get(sec, {}).get(key)
        if isinstance(v, str) and v:
            cfg[sec][key] = _abs(v, app)
    # Sound cues are user-supplied content -> writable data dir.
    sc = cfg.get("sound_cues", {})
    for name, val in list(sc.items()):
        if isinstance(val, list):
            sc[name] = [_abs(p, data) if isinstance(p, str) and p else p for p in val]
        elif isinstance(val, str) and val:
            sc[name] = _abs(val, data)


def _seed_config_if_missing() -> None:
    """On a frozen first run the writable config.toml doesn't exist yet — copy the bundled
    default into the data dir. No-op in a source run (the file is already the bundled one).
    Fail-soft: a copy error just leaves load_config to raise the same FileNotFound as before."""
    cp = config_path()
    if cp.exists():
        return
    bundled = app_dir() / "config.toml"
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        if bundled.exists() and bundled.resolve() != cp.resolve():
            shutil.copyfile(bundled, cp)
    except OSError:
        pass


def load_config() -> dict:
    _seed_config_if_missing()
    with open(config_path(), "rb") as f:
        cfg = tomllib.load(f)
    op = overrides_path()
    if op.exists():
        try:
            _deep_merge(cfg, json.loads(op.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a broken overrides file must not crash startup
            pass
    _resolve_paths(cfg)
    return cfg


def load_overrides() -> dict:
    op = overrides_path()
    if op.exists():
        try:
            return json.loads(op.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_overrides(overrides: dict) -> None:
    """Persist UI changes. config.toml stays untouched; this layers on top."""
    op = overrides_path()
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(overrides, indent=2), encoding="utf-8")


def deep_merge(base: dict, over: dict) -> dict:
    return _deep_merge(base, over)


# Values that count as "on" for the dev-mode mock env var (COVAS_MOCK).
_TRUEISH = {"1", "true", "yes", "on"}


def mock_enabled(cfg: dict) -> bool:
    """Whether dev-mode mock is on. The COVAS_MOCK env var wins if set (handy for a
    one-off `$env:COVAS_MOCK=1; python run_covas.py` in PowerShell without editing
    config); otherwise fall back to [dev].mock in config. Mock swaps in the fake
    providers (zero API calls/cost)."""
    env = os.environ.get("COVAS_MOCK")
    if env is not None and env.strip() != "":
        return env.strip().lower() in _TRUEISH
    return bool(cfg.get("dev", {}).get("mock", False))
