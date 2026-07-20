"""Per-language control-panel UI catalogs (issue #196 — curated-language fill-in).

Each ``<code>.json`` in this package maps an English SOURCE string (the ``t()`` key wired into the
templates by #196) to its translation, for one ISO 639-1 language. These are **LLM-authored
translations pending native review** — see ``docs/using/language.md``.

A file's mere presence activates that language: :func:`load` discovers every ``*.json`` here and
``covas.ui_i18n`` registers them into its catalog gate. The gate stays "complete catalog only", so
each file covers **every** key the templates use (verified in tests) — a missing key would fall
back to English and produce a half-translated panel, exactly what the epic forbids.

Stdlib-only (``json``); a corrupt catalog is skipped, never fatal — English still serves.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).parent


def load() -> dict[str, dict[str, str]]:
    """{code: {english_source -> translation}} for every ``*.json`` catalog present. Fail-soft:
    an unreadable/malformed file is skipped so one bad catalog can't break the control panel."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted(_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out[path.stem] = {str(k): str(v) for k, v in data.items()}
    return out
