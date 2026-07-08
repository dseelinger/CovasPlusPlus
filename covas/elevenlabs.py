"""Fetch the user's ElevenLabs voices and TTS-capable models for the UI dropdowns."""
from __future__ import annotations
from pathlib import Path
import requests

BASE = "https://api.elevenlabs.io/v1"


def _key(cfg: dict) -> str:
    return Path(cfg["elevenlabs"]["api_key_file"]).read_text(encoding="utf-8").strip()


def list_voices(cfg: dict) -> list[dict]:
    r = requests.get(f"{BASE}/voices", headers={"xi-api-key": _key(cfg)}, timeout=15)
    r.raise_for_status()
    return [
        {"voice_id": v["voice_id"], "name": v["name"], "category": v.get("category", "")}
        for v in r.json().get("voices", [])
    ]


def list_models(cfg: dict) -> list[dict]:
    r = requests.get(f"{BASE}/models", headers={"xi-api-key": _key(cfg)}, timeout=15)
    r.raise_for_status()
    return [
        {"model_id": m["model_id"], "name": m.get("name", m["model_id"])}
        for m in r.json() if m.get("can_do_text_to_speech")
    ]
