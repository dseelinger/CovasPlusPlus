"""Live module-taxonomy reconciliation — keeps find-closest-module current as Frontier adds
modules. The exact sibling of `ship_index.py`, for outfitting instead of ships.

The bundled taxonomy in `modules.py` is generated from EDCD/FDevIDs outfitting.csv — complete
and structured, but a point-in-time snapshot. Frontier ships new modules every few months, and
the Spansh `modules` filter is exact-match — a module we don't know the exact name of is one we
refuse to search for. So this index does at STARTUP what would otherwise need a CSV refresh: it
fetches the module names Spansh currently knows and surfaces any the bundle is missing
(`extra_names`), which the resolver folds into its lookup so a brand-new module becomes findable
with no update.

Mirrors `ship_index.py` (and `search/faction_index.py`) exactly — lazy, cached, fail-soft: if
Spansh is unreachable the index stays empty and resolution simply falls back to the bundled
taxonomy — never a crash, never a block. The fetch is injected so the default test run is
offline (DESIGN §9).

Only the *names* are learned here. A newly-learned module resolves by its exact or fuzzy name
but has NO known sizes/mounts — so it searches unqualified (no "which size?" guidance) until the
next EDCD refresh fills its attributes in (`scripts/gen_module_taxonomy.py`). Aliases and
size/mount guidance stay curated in `modules.py`. The authoritative source is Spansh's own live
outfitting data — harvested from the stations around a hub that stocks the full module set.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from ..search.spansh import _DEFAULT_UA, STATIONS_URL, distance_sort
from .modules import MODULE_NAMES

# Shinrarta Dezhra (Jameson Memorial) is the community hub whose stations stock essentially the
# entire outfitting catalogue, so a few hundred nearby stations union to the full module list.
_HARVEST_REFERENCE = "Shinrarta Dezhra"
_HARVEST_SIZE = 250


def fetch_module_names(*, user_agent: str = _DEFAULT_UA, timeout: float = 30.0) -> list[str]:
    """Every module name Spansh currently lists in nearby station outfitting. Real network POST —
    built only at the app composition root, so tests inject a fake instead."""
    import requests  # local import: keeps the offline stack importable without hitting it
    body = {"filters": {"has_outfitting": {"value": True}}, "sort": distance_sort(),
            "size": _HARVEST_SIZE, "page": 0, "reference_system": _HARVEST_REFERENCE}
    resp = requests.post(STATIONS_URL, json=body,
                         headers={"Content-Type": "application/json", "User-Agent": user_agent},
                         timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    names: set[str] = set()
    for station in (data.get("results") if isinstance(data, dict) else None) or []:
        for module in station.get("modules") or []:
            name = module.get("name")
            if name:
                names.add(name)
    return sorted(names)


class ModuleIndex:
    """Lazily-loaded, cached view of the modules Spansh currently knows, reconciled against the
    bundled taxonomy. `fetch` returns the name list (injected; defaults to the real Spansh POST).
    Fail-soft: if the list can't be fetched, `loaded` stays False and `extra_names()` is empty, so
    the resolver just uses the bundled taxonomy."""

    def __init__(self, fetch: Callable[[], list[str]] = fetch_module_names,
                 *, bundled: tuple[str, ...] = MODULE_NAMES) -> None:
        self._fetch = fetch
        self._bundled = frozenset(bundled)
        self._names: list[str] | None = None      # None until first access
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        """True once a non-empty module list has been fetched and cached."""
        self._ensure()
        return bool(self._names)

    def refresh(self) -> None:
        """Trigger the (cached) fetch. The app calls this once on a background startup thread so
        the first module query doesn't pay the network latency; safe to call repeatedly."""
        self._ensure()

    def _ensure(self) -> None:
        with self._lock:
            if self._names is not None:
                return
            try:
                names = list(self._fetch() or [])
            except Exception:  # noqa: BLE001 — a fetch failure degrades to "bundled only", not a crash
                names = []
            self._names = names

    def extra_names(self) -> tuple[str, ...]:
        """The canonical module names Spansh knows that the bundled taxonomy is missing — i.e.
        modules added since the CSV was last regenerated. Empty until (and unless) the fetch
        succeeds."""
        self._ensure()
        if not self._names:
            return ()
        return tuple(n for n in self._names if n not in self._bundled)
