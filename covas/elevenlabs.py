"""Fetch the user's ElevenLabs voices and TTS-capable models for the UI dropdowns."""
from __future__ import annotations
from pathlib import Path
import requests

BASE = "https://api.elevenlabs.io/v1"


def _key(cfg: dict) -> str:
    return Path(cfg["elevenlabs"]["api_key_file"]).read_text(encoding="utf-8").strip()


def is_famous(v: dict) -> bool:
    """True for ElevenLabs 'famous' voices (celebrity likenesses, ™ names like John Wayne™).
    These are licensed for the ElevenReader app only; the TTS API rejects them with
    401 famous_voice_not_permitted, so if picked they fail soft to silence. Detect via the
    permission flag `sharing.category == 'famous'` (a clean split across the roster), NOT the
    ™ glyph (fragile) nor top-level `category` (its 'professional' value also covers many
    perfectly usable voices)."""
    return (v.get("sharing") or {}).get("category") == "famous"


def list_voices(cfg: dict) -> list[dict]:
    """Selectable voices for the picker (and any future random/atmospheric pool). 'Famous'
    voices are filtered out here at the single source so they can never be chosen — they
    would only 401 to silence."""
    r = requests.get(f"{BASE}/voices", headers={"xi-api-key": _key(cfg)}, timeout=15)
    r.raise_for_status()
    return [
        {"voice_id": v["voice_id"], "name": v["name"], "category": v.get("category", "")}
        for v in r.json().get("voices", [])
        if not is_famous(v)
    ]


def list_models(cfg: dict) -> list[dict]:
    r = requests.get(f"{BASE}/models", headers={"xi-api-key": _key(cfg)}, timeout=15)
    r.raise_for_status()
    return [
        {"model_id": m["model_id"], "name": m.get("name", m["model_id"])}
        for m in r.json() if m.get("can_do_text_to_speech")
    ]
