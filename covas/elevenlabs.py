"""Fetch the user's ElevenLabs voices and TTS-capable models for the UI dropdowns."""
from __future__ import annotations
import requests

BASE = "https://api.elevenlabs.io/v1"


def _key(cfg: dict) -> str:
    """The ElevenLabs key, via firstrun so it's DPAPI-aware (decrypts / migrates plaintext) rather
    than reading the file raw. Returns "" when unconfigured — callers surface the auth error."""
    from .firstrun import elevenlabs_key
    return elevenlabs_key(cfg) or ""


def is_famous(v: dict) -> bool:
    """True for ElevenLabs 'famous' voices (celebrity likenesses, ™ names like John Wayne™).
    These are licensed for the ElevenReader app only; the TTS API rejects them with
    401 famous_voice_not_permitted, so if picked they fail soft to silence. Detect via the
    permission flag `sharing.category == 'famous'` (a clean split across the roster), NOT the
    ™ glyph (fragile) nor top-level `category` (its 'professional' value also covers many
    perfectly usable voices)."""
    return (v.get("sharing") or {}).get("category") == "famous"


def sort_by_name(items: list[dict]) -> list[dict]:
    """Alphabetize dropdown entries by display name, case-insensitive. Single shared helper
    (issue #93) so every fetcher — and any future one (Edge/Azure #88, general #92) — sorts
    the same way. Python's sort is stable, so entries with equal (case-folded) names keep
    their original relative order rather than depending on this being called twice."""
    return sorted(items, key=lambda item: item["name"].casefold())


def list_voices(cfg: dict) -> list[dict]:
    """Selectable voices for the picker (and any future random/atmospheric pool). 'Famous'
    voices are filtered out here at the single source so they can never be chosen — they
    would only 401 to silence."""
    r = requests.get(f"{BASE}/voices", headers={"xi-api-key": _key(cfg)}, timeout=15)
    r.raise_for_status()
    return sort_by_name([
        {"voice_id": v["voice_id"], "name": v["name"], "category": v.get("category", "")}
        for v in r.json().get("voices", [])
        if not is_famous(v)
    ])


def list_voices_detailed(cfg: dict) -> list[dict]:
    """Like `list_voices` but carrying the richer METADATA a matcher needs (issue #96): each entry
    keeps `category` plus the ElevenLabs `labels` (gender / age / accent / descriptive / use-case)
    and free-text `description`, so an LLM can pair a voice to a persona on something more than a
    name. A SIBLING of `list_voices` (which stays a lean id/name/category list for the picker) so
    the two callers don't fight over the shape. 'Famous' voices are filtered out identically."""
    r = requests.get(f"{BASE}/voices", headers={"xi-api-key": _key(cfg)}, timeout=15)
    r.raise_for_status()
    out: list[dict] = []
    for v in r.json().get("voices", []):
        if is_famous(v):
            continue
        labels = v.get("labels") or {}
        out.append({
            "voice_id": v["voice_id"],
            "name": v["name"],
            "category": v.get("category", ""),
            # Pass the labels through as-is (keys vary a little across roster vintages) plus the
            # free-text description — both are optional and simply absent when EL doesn't supply them.
            "labels": {k: labels[k] for k in labels if labels[k]},
            "description": v.get("description") or "",
        })
    return sort_by_name(out)


def list_models(cfg: dict) -> list[dict]:
    r = requests.get(f"{BASE}/models", headers={"xi-api-key": _key(cfg)}, timeout=15)
    r.raise_for_status()
    return sort_by_name([
        {"model_id": m["model_id"], "name": m.get("name", m["model_id"])}
        for m in r.json() if m.get("can_do_text_to_speech")
    ])
