"""Fetched-catalog resolver for the settings dropdowns (issues #92 + #88).

One place that turns an `options_source` sentinel from `settings_schema` into a concrete option list
for the web settings page (`/api/catalog`), the voice settings layer (`app._settings_option_pairs`),
and the command-palette pickers (#94). Each option is a small dict:

    {"value": <id/url the setting stores>, "label": <human name>, "meta": <secondary text or "">}

`meta` is the per-row secondary text the #94 palette shows (voice locale/gender, model source) so
similar entries are distinguishable.

FAIL-SOFT is the whole point (CLAUDE.md): `resolve()` NEVER raises. On any failure — offline, no key,
a bad base_url — it returns ``(None, "<reason>")`` and the caller degrades to
free-text with the current value preserved (an editable combobox, never an empty/blocking dropdown).
The network lives in the provider fetchers (each already split into a pure parse + a thin fetch);
this module only wires sentinel → fetcher and swallows errors. That keeps it unit-testable offline by
monkeypatching the fetchers.
"""
from __future__ import annotations

from typing import Optional

from . import settings_schema as schema

# Known OpenAI-compatible endpoints (issue #92). One `openai`/`openai_tts` provider covers all four —
# only base_url differs — so we offer them as presets; "Custom…" is just any other typed URL (the
# combobox accepts it). Mirrors the presets documented in config.toml's [openai] block.
OPENAI_BASE_URL_PRESETS: list[dict] = [
    {"value": "https://api.openai.com/v1", "label": "OpenAI", "meta": ""},
    {"value": "https://api.groq.com/openai/v1", "label": "Groq", "meta": ""},
    {"value": "https://api.deepseek.com/v1", "label": "DeepSeek", "meta": ""},
    {"value": "https://openrouter.ai/api/v1", "label": "OpenRouter", "meta": ""},
]


def _ids(ids, meta: str = "") -> list[dict]:
    """Wrap a bare id list as option dicts (label == value)."""
    return [{"value": i, "label": i, "meta": meta} for i in ids]


def _voices(voices) -> list[dict]:
    """Map the providers' normalized voice dicts ({ref/voice_id, name, gender, locale, category})
    to option dicts; the meta line carries locale/gender/category so the palette can disambiguate."""
    out: list[dict] = []
    for v in voices or []:
        val = v.get("ref") or v.get("voice_id") or ""
        if not val:
            continue
        bits = [b for b in (v.get("locale") or v.get("category") or "", v.get("gender") or "") if b]
        out.append({"value": val, "label": v.get("name") or val, "meta": " · ".join(bits)})
    return out


def _dedup_input_devices(devices: list[dict]) -> list[dict]:
    """Collapse the OS's per-host-API duplicates of one physical mic into a single option (#89).

    Windows enumerates the same mic under MME, DirectSound, and WASAPI; the MME copy TRUNCATES the
    name to ~31 chars (e.g. "Microphone (Logi 4K Stream Edit"), and because `Recorder._resolve` does
    a first-hit substring match, that silent truncated copy used to win. list_input_devices() gives
    only {index, name}, so we prefer by NAME: drop any device whose name is a strict PREFIX of a
    longer one (the truncation) and de-duplicate exact repeats, keeping first-seen order. The result
    is the full-name (typically WASAPI/DirectSound) entry the Recorder resolves to the audible device."""
    names = [str(d.get("name", "")).strip() for d in devices if str(d.get("name", "")).strip()]
    kept: list[str] = []
    for n in names:
        if n in kept:
            continue  # exact duplicate
        # Skip a name that is a strict prefix of any OTHER device name (a truncated MME clone).
        if any(other != n and other.startswith(n) for other in names):
            continue
        kept.append(n)
    return [{"value": n, "label": n, "meta": ""} for n in kept]


def _cfg(cfg: dict, *keys, default=""):
    node = cfg
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k)
    return node if node is not None else default


def resolve(source: str, cfg: dict, *, base_url: Optional[str] = None
            ) -> tuple[Optional[list[dict]], Optional[str]]:
    """Resolve ONE options_source to ``(options, None)`` or, fail-soft, ``(None, "reason")``.

    `base_url` overrides the config base_url for the OpenAI model list, so the page can refetch when
    the user changes the endpoint before saving (issue #92). NEVER raises."""
    from . import firstrun
    try:
        # --- static / no-network sources (always available) ---
        if source == schema.OPT_OPENAI_BASE_URLS:
            return list(OPENAI_BASE_URL_PRESETS), None

        # --- local capture devices (mic picker, #89) — no network, no key ---
        if source == schema.OPT_INPUT_DEVICES:
            return _dedup_input_devices(firstrun.list_input_devices()), None

        # --- LLM model catalogs ---
        if source == schema.OPT_OPENAI_MODELS:
            from .providers.openai_llm import list_openai_models, _DEFAULT_BASE_URL
            url = (base_url or _cfg(cfg, "openai", "base_url") or _DEFAULT_BASE_URL).rstrip("/")
            key = firstrun.openai_key(cfg)
            if not key:
                return None, "no OpenAI key"
            return _ids(list_openai_models(url, key)), None

        if source == schema.OPT_GEMINI_MODELS:
            from .providers.gemini_llm import list_gemini_models, _DEFAULT_BASE_URL
            url = (_cfg(cfg, "gemini", "base_url") or _DEFAULT_BASE_URL).rstrip("/")
            key = firstrun.gemini_key(cfg)
            if not key:
                return None, "no Gemini key"
            return _ids(list_gemini_models(url, key)), None

        if source == schema.OPT_ANTHROPIC_MODELS_LIVE:
            # Prefer the live list; fall back to the static available_models so this never comes back
            # empty (issue #92). Live ids first, then any static-only extras, de-duplicated.
            from .llm import list_anthropic_models
            static = list(_cfg(cfg, "anthropic", "available_models", default=[]) or [])
            try:
                live = list_anthropic_models(cfg)
            except Exception:  # noqa: BLE001 — offline / no key: static list still serves
                return (_ids(static), None) if static else (None, "no Anthropic key")
            merged = live + [m for m in static if m not in live]
            return _ids(merged), None

        # --- TTS voice catalogs ---
        if source == schema.OPT_EDGE_VOICES:
            from .providers.edge_tts import list_edge_voices
            return _voices(list_edge_voices()), None

        if source == schema.OPT_AZURE_VOICES:
            from .providers.azure_tts import list_azure_voices
            key = firstrun.azure_key(cfg)
            region = str(_cfg(cfg, "azure", "region")).strip()
            if not key or not region:
                return None, "Azure voice list needs the key + region"
            return _voices(list_azure_voices(key, region)), None

        if source == schema.OPT_CARTESIA_VOICES:
            from .providers.cartesia_tts import list_cartesia_voices, _DEFAULT_BASE_URL
            key = firstrun.cartesia_key(cfg)
            if not key:
                return None, "no Cartesia key"
            url = (_cfg(cfg, "cartesia", "base_url") or _DEFAULT_BASE_URL).rstrip("/")
            return _voices(list_cartesia_voices(key, url)), None

        # --- ElevenLabs (reuse the existing normalizers; unify the fetch path) ---
        if source in (schema.OPT_EL_VOICES, schema.OPT_EL_MODELS):
            from . import elevenlabs as el
            if source == schema.OPT_EL_MODELS:
                return [{"value": m["model_id"], "label": m.get("name") or m["model_id"], "meta": ""}
                        for m in el.list_models(cfg)], None
            out = []
            for v in el.list_voices(cfg):
                cat = v.get("category", "")
                out.append({"value": v["voice_id"], "label": v.get("name") or v["voice_id"],
                            "meta": cat})
            return out, None

        return None, f"unknown source {source!r}"
    except Exception as e:  # noqa: BLE001 — a catalog fetch must never break settings or the loop
        return None, str(e)


def option_pairs(source: str, cfg: dict) -> Optional[list[tuple[str, str]]]:
    """`(value, label)` pairs for the voice settings layer (`app._settings_option_pairs`), or None
    on any failure so the capability can say "couldn't reach it" instead of guessing."""
    opts, _err = resolve(source, cfg)
    if opts is None:
        return None
    return [(o["value"], o["label"]) for o in opts]
