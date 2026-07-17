"""Shared read/write for the committed dataset manifest (issue #101).

One JSON at `covas/nav/data/datasets_manifest.json` records, per generated dataset,
`{source, source_ref, generated_at, row_count}` — so "how fresh is your game data?" has a
real answer (see `covas/nav/datasets.py` for the runtime reader that `check_setup.py` and the
`game_data_status` capability consume). The regen scripts EMIT into it; nothing at runtime
writes it. Covers BOTH the nav datasets (ship roster / specs / module taxonomy) and the ed
datasets (engineering blueprints / materials), so a single file answers for all bundled data.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = _ROOT / "covas" / "nav" / "data" / "datasets_manifest.json"


def load() -> dict:
    """The manifest as a dict keyed by dataset name; empty if it doesn't exist yet."""
    if not MANIFEST_PATH.exists():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def update(name: str, *, source: str, source_ref: str, row_count: int,
           generated_at: str | None = None) -> None:
    """Record/refresh one dataset's provenance. `generated_at` defaults to today (ISO date).
    Writes the whole manifest back, sorted, so a regen produces a small reviewable diff."""
    data = load()
    data[name] = {
        "source": source,
        "source_ref": source_ref,
        "generated_at": generated_at or date.today().isoformat(),
        "row_count": int(row_count),
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
