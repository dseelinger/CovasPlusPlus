"""Load config.toml and layer overrides.json on top (UI writes overrides.json)."""
from __future__ import annotations
import json
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
OVERRIDES_PATH = ROOT / "overrides.json"


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# Config path fields that are stored relative to the project root and resolved
# to absolute at load time. Keeps config.toml portable (no hardcoded C:\Users\...)
# and free of the local username, while consumers still receive absolute paths.
_PATH_FIELDS = (
    ("personality", "file"),
    ("elevenlabs", "api_key_file"),
    ("checklist", "file"),
    ("logging", "dir"),
    ("piper", "model"),
)


def _abs(p: str) -> str:
    return p if Path(p).is_absolute() else str((ROOT / p).resolve())


def _resolve_paths(cfg: dict) -> None:
    for sec, key in _PATH_FIELDS:
        v = cfg.get(sec, {}).get(key)
        if isinstance(v, str) and v:
            cfg[sec][key] = _abs(v)
    sc = cfg.get("sound_cues", {})
    for name, val in list(sc.items()):
        if isinstance(val, list):
            sc[name] = [_abs(p) if isinstance(p, str) and p else p for p in val]
        elif isinstance(val, str) and val:
            sc[name] = _abs(val)


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    if OVERRIDES_PATH.exists():
        try:
            _deep_merge(cfg, json.loads(OVERRIDES_PATH.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — a broken overrides file must not crash startup
            pass
    _resolve_paths(cfg)
    return cfg


def load_overrides() -> dict:
    if OVERRIDES_PATH.exists():
        try:
            return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_overrides(overrides: dict) -> None:
    """Persist UI changes. config.toml stays untouched; this layers on top."""
    OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2), encoding="utf-8")


def deep_merge(base: dict, over: dict) -> dict:
    return _deep_merge(base, over)


# Values that count as "on" for the dev-mode mock env var (COVAS_MOCK).
_TRUEISH = {"1", "true", "yes", "on"}


def mock_enabled(cfg: dict) -> bool:
    """Whether dev-mode mock is on. The COVAS_MOCK env var wins if set (handy for a
    one-off `COVAS_MOCK=1 run_covas.py` without editing config); otherwise fall back
    to [dev].mock in config. Mock swaps in the fake providers (zero API calls/cost)."""
    env = os.environ.get("COVAS_MOCK")
    if env is not None and env.strip() != "":
        return env.strip().lower() in _TRUEISH
    return bool(cfg.get("dev", {}).get("mock", False))
