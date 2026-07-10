"""Generic canonical-vocabulary resolution — one loose-spoken-value -> canonical matcher.

Every Spansh enum slot (allegiance, station type, faction state, …) needs the same thing: map
a possibly-misheard spoken value to the EXACT canonical string Spansh accepts, or decide it
isn't one (so the capability can speak a correction instead of silently widening the search —
Spansh ignores an unknown value). This module is that matcher, shared by the per-domain vocab
tables in `systems.py` / `stations.py` / `factions.py`, so the logic lives once.

`resolve` is strict (for building a query); `nearest` is lenient (for a spoken 'did you
mean…'). Both only ever return a value from the provided canonical set — never an invention.
"""
from __future__ import annotations

import difflib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm(text) -> str:
    """Fold a spoken/typed value to a comparison key: lowercase, drop punctuation/spaces so
    'High Tech', 'high-tech' and 'hightech' all collapse together."""
    return _NON_ALNUM.sub("", str(text).lower())


def _lookup(values, aliases: dict | None) -> dict[str, str]:
    """Normalized key -> canonical value, canonical names first (they win over aliases)."""
    lut: dict[str, str] = {}
    for v in values:
        lut[norm(v)] = v
    for spoken, v in (aliases or {}).items():
        lut.setdefault(norm(spoken), v)
    return lut


def resolve(values, spoken, *, aliases: dict | None = None, cutoff: float = 0.72) -> str | None:
    """The canonical value in `values` matching `spoken`, or None. Exact-after-normalization
    (including `aliases`) first, then a tight fuzzy match for Whisper mishears."""
    if spoken is None:
        return None
    lut = _lookup(values, aliases)
    key = norm(spoken)
    if not key:
        return None
    if key in lut:
        return lut[key]
    match = difflib.get_close_matches(key, list(lut), n=1, cutoff=cutoff)
    return lut[match[0]] if match else None


def nearest(values, spoken, *, aliases: dict | None = None, cutoff: float = 0.5) -> str | None:
    """The closest canonical value to an UNRESOLVED spoken term (looser cutoff), for a spoken
    correction. Always a real value from `values` or None."""
    return resolve(values, spoken, aliases=aliases, cutoff=cutoff)
