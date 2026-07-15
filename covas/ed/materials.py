"""Material inventory snapshot — the Commander's engineering materials, from the journal (#66).

ED writes a `Materials` event at every game load: the COMPLETE Raw / Manufactured / Encoded
inventory, each an array of `{"Name": <lower-case symbol>, "Count": n}`. That symbol is the
same key the bundled `data/materials.json` and blueprint recipes use, so we store a single flat
`{symbol: count}` map and match against it directly. Between full snapshots the counts drift as
you pick up or spend materials, so `MaterialCollected` / `MaterialDiscarded` deltas nudge the
stored map (see `journal.py`) — the next `Materials` event re-grounds it wholesale.

Pure capture side: raw journal in, a frozen snapshot out. Naming/sourcing is `blueprints.py`'s
job. Stored on `EDContext` (see `context.set_materials`), read by the BlueprintCapability's
tools. Local journal data only — no CAPI, no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

# The journal's three material buckets, as they appear in the `Materials` event.
_BUCKETS = ("Raw", "Manufactured", "Encoded")


@dataclass(frozen=True)
class MaterialsSnapshot:
    """The Commander's material inventory as of the last `Materials` event (plus any deltas
    applied since). `counts` maps the journal material name (lower-case, e.g. "arsenic",
    "chemicalmanipulators") to how many are held. Immutable — a delta yields a new snapshot."""
    counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    timestamp: str | None = None

    def count(self, symbol: str) -> int:
        """How many of `symbol` (journal name) are held — 0 if none/unknown."""
        return int(self.counts.get(str(symbol).strip().lower(), 0))

    def with_delta(self, symbol: str, delta: int) -> "MaterialsSnapshot":
        """A new snapshot with `symbol`'s count adjusted by `delta` (clamped at 0). Used to keep
        the inventory fresh from MaterialCollected (+) / MaterialDiscarded (-) between full
        `Materials` events, without waiting for the next wholesale snapshot."""
        key = str(symbol).strip().lower()
        if not key:
            return self
        new_counts = dict(self.counts)
        new_counts[key] = max(0, new_counts.get(key, 0) + int(delta))
        return MaterialsSnapshot(counts=MappingProxyType(new_counts), timestamp=self.timestamp)


def parse_materials(event: dict) -> MaterialsSnapshot:
    """A `Materials` journal event -> a flat inventory snapshot. Tolerant of missing buckets or
    malformed rows (a row without a Name/Count is skipped) — the watcher must never choke."""
    counts: dict[str, int] = {}
    for bucket in _BUCKETS:
        for row in event.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or "").strip().lower()
            cnt = row.get("Count")
            if name and isinstance(cnt, (int, float)):
                counts[name] = int(cnt)
    ts = str(event.get("timestamp")) if event.get("timestamp") else None
    return MaterialsSnapshot(counts=MappingProxyType(counts), timestamp=ts)
