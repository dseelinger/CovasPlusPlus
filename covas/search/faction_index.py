"""Canonical minor-faction name resolution (lazily fetched from Spansh, cached).

Spansh's faction filters (`controlling_minor_faction` / `minor_faction_presences`) are EXACT
string matches — a single mistranscription ("Formadine" for "Formidine") returns zero systems,
which is exactly what let the model confabulate a location instead of finding the faction. So a
spoken faction name is resolved against the canonical list of faction names Spansh actually
knows BEFORE the query, and if it can't be resolved the capability offers the nearest real
names ("did you mean…") — never a dead end the model fills with fiction (the hallucination
guard, same principle as the enum vocabularies, just fetched because the list is ~6.5k long and
changes over time).

Source: `GET /api/systems/field_values/controlling_minor_faction` returns every faction that
controls at least one system (the ones worth asking about), as `{"min_max": {name: count}}`.
Fetched once per session and cached; the fetch is injected so the default test run is offline
(DESIGN §9), and a failed fetch is fail-soft — the capability falls back to the raw spoken name
rather than blocking the search.
"""
from __future__ import annotations

import difflib
from collections.abc import Callable

from . import vocab
from .spansh import _DEFAULT_UA

_FIELD_VALUES_URL = "https://spansh.co.uk/api/systems/field_values/controlling_minor_faction"

# Strict cutoff resolves a mishear to the intended faction; the looser one gathers "did you
# mean" suggestions when nothing resolves outright.
_RESOLVE_CUTOFF = 0.82
_SUGGEST_CUTOFF = 0.6


def fetch_controlling_faction_names(*, user_agent: str = _DEFAULT_UA,
                                    timeout: float = 30.0) -> list[str]:
    """Every faction name Spansh knows as a system controller. Real network GET — built only at
    the app composition root, so tests inject a fake instead."""
    import requests  # local import: keeps the offline stack importable without hitting it
    resp = requests.get(_FIELD_VALUES_URL, headers={"User-Agent": user_agent}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    names = data.get("min_max") if isinstance(data, dict) else None
    return list(names.keys()) if isinstance(names, dict) else []


class FactionIndex:
    """Lazily-loaded, cached canonical faction-name resolver. `fetch` returns the name list
    (injected; defaults to the real Spansh GET). Every method is fail-soft: if the list can't
    be fetched, `loaded` stays False and callers fall back to the raw spoken name."""

    def __init__(self, fetch: Callable[[], list[str]] = fetch_controlling_faction_names) -> None:
        self._fetch = fetch
        self._names: list[str] | None = None      # None until first access
        self._lut: dict[str, str] = {}            # normalized key -> canonical name

    @property
    def loaded(self) -> bool:
        """True once a non-empty faction list has been fetched and cached."""
        self._ensure()
        return bool(self._names)

    def _ensure(self) -> None:
        if self._names is not None:
            return
        try:
            names = list(self._fetch() or [])
        except Exception:  # noqa: BLE001 — a fetch failure degrades to "no resolution", not a crash
            names = []
        # Publish _lut BEFORE _names. `_names is not None` is the "loaded" sentinel every reader
        # checks first (in `loaded`/`resolve`/`suggestions`), so a concurrent reader that sees
        # _names populated must already see the matching _lut — otherwise it reads an empty table and
        # falsely reports "faction not found" (issue #164). Each assignment is atomic under the GIL;
        # ordering is the only thing that closes the torn-write window.
        self._lut = {vocab.norm(n): n for n in names}
        self._names = names

    def resolve(self, spoken) -> str | None:
        """The exact canonical faction name for a spoken one, or None. Exact-after-normalization
        first, then a tight fuzzy match for a mistranscription."""
        self._ensure()
        if not self._names or spoken is None:
            return None
        key = vocab.norm(spoken)
        if not key:
            return None
        if key in self._lut:
            return self._lut[key]
        m = difflib.get_close_matches(key, list(self._lut), n=1, cutoff=_RESOLVE_CUTOFF)
        return self._lut[m[0]] if m else None

    def suggestions(self, spoken, n: int = 3) -> list[str]:
        """Up to `n` nearest real faction names for a spoken term that didn't resolve — for a
        'did you mean…' correction. Every one is a real, canonical name."""
        self._ensure()
        key = vocab.norm(spoken)
        if not self._names or not key:
            return []
        return [self._lut[k] for k in
                difflib.get_close_matches(key, list(self._lut), n=n, cutoff=_SUGGEST_CUTOFF)]
