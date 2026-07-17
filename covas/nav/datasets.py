"""Runtime reader for the bundled dataset manifest (issue #101).

The regen scripts (`scripts/refresh_datasets.py` and friends) EMIT `data/datasets_manifest.json`
— per dataset: `{source, source_ref, generated_at, row_count}`. This module is the offline,
read-only consumer of that file, shared by `check_setup.py` (dataset-age warnings) and the
`game_data_status` capability ("when was your ship data last updated?"). Pure + offline; a
missing/corrupt manifest degrades to an empty list, never an error (fail-soft).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

_MANIFEST = Path(__file__).resolve().parent / "data" / "datasets_manifest.json"

# Friendly display names for the manifest keys (spoken/printed, not the raw slug).
_LABELS = {
    "ship_roster": "ship roster (names)",
    "ship_specs": "ship specifications",
    "module_taxonomy": "outfitting modules",
    "engineering_blueprints": "engineering blueprints",
    "engineering_materials": "engineering materials",
}


@dataclass(frozen=True)
class DatasetInfo:
    """One dataset's manifest row plus its computed age. `age_days` is None when the date is
    absent/unparseable, so callers treat 'unknown age' as its own case rather than 0."""
    name: str
    source: str
    source_ref: str
    generated_at: str
    row_count: int

    @property
    def label(self) -> str:
        return _LABELS.get(self.name, self.name.replace("_", " "))

    @property
    def age_days(self) -> int | None:
        try:
            return (date.today() - date.fromisoformat(self.generated_at)).days
        except (ValueError, TypeError):
            return None


@lru_cache(maxsize=1)
def load_manifest() -> tuple[DatasetInfo, ...]:
    """Every bundled dataset's provenance, sorted by name. Empty tuple if the manifest is
    missing or unreadable — the caller decides how to phrase 'no manifest'."""
    try:
        raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    out = [
        DatasetInfo(name=name, source=str(d.get("source", "")),
                    source_ref=str(d.get("source_ref", "")),
                    generated_at=str(d.get("generated_at", "")),
                    row_count=int(d.get("row_count", 0) or 0))
        for name, d in sorted(raw.items())
    ]
    return tuple(out)


def stale_datasets(max_age_days: int) -> list[DatasetInfo]:
    """Datasets older than `max_age_days` (unknown-age rows are treated as stale, so a broken
    date still nags rather than hiding)."""
    return [d for d in load_manifest()
            if d.age_days is None or d.age_days > max_age_days]
