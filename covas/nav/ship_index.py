"""Live ship-roster reconciliation — keeps find-closest-ship current as Frontier adds hulls.

The bundled roster in `ships.py` is a point-in-time snapshot (the canonical Spansh names +
curated aliases/families that make offline resolution fast, testable, and hallucination-proof).
But Frontier ships new hulls every few months, and the Spansh `ships` filter is exact-match —
a ship we don't know the exact name of is one we refuse to search for. So this index does at
STARTUP what would otherwise need a code change per release: it fetches the ship names Spansh
currently knows and surfaces any the bundle is missing (`extra_names`), which the resolver
folds into its lookup so a brand-new ship becomes findable with no update.

Mirrors `search/faction_index.py` exactly (lazy, cached, fail-soft): if Spansh is unreachable
the index stays empty and resolution simply falls back to the bundled roster — never a crash,
never a block. The fetch is injected so the default test run is offline (DESIGN §9).

Only the *names* are learned here. Aliases (short forms / mishears) and ambiguous-family
disambiguation ("which Krait?") remain curated in `ships.py` — a newly-learned ship resolves by
its exact or fuzzy name, but won't get a nickname or join a family until someone edits the
roster (the drift is then a nicety, not a dead end). Spansh has no ship-list reference endpoint
(verified: /api/ships etc. 404), so the authoritative source is its own live shipyard data —
harvested from the shipyards around a hub that stocks the full roster.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from ..search.spansh import _DEFAULT_UA, STATIONS_URL, distance_sort
from .ships import SHIP_NAMES

# Jameson Memorial (Shinrarta Dezhra) is the community hub whose shipyards stock the entire
# roster, so a few hundred nearby shipyards union to the full, current ship list.
_HARVEST_REFERENCE = "Shinrarta Dezhra"
_HARVEST_SIZE = 250


def fetch_ship_names(*, user_agent: str = _DEFAULT_UA, timeout: float = 30.0) -> list[str]:
    """Every ship name Spansh currently lists in nearby shipyards. Real network POST — built
    only at the app composition root, so tests inject a fake instead."""
    import requests  # local import: keeps the offline stack importable without hitting it
    body = {"filters": {"has_shipyard": {"value": True}}, "sort": distance_sort(),
            "size": _HARVEST_SIZE, "page": 0, "reference_system": _HARVEST_REFERENCE}
    resp = requests.post(STATIONS_URL, json=body,
                         headers={"Content-Type": "application/json", "User-Agent": user_agent},
                         timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    names: set[str] = set()
    for station in (data.get("results") if isinstance(data, dict) else None) or []:
        for ship in station.get("ships") or []:
            name = ship.get("name")
            if name:
                names.add(name)
    return sorted(names)


class ShipIndex:
    """Lazily-loaded, cached view of the ships Spansh currently knows, reconciled against the
    bundled roster. `fetch` returns the name list (injected; defaults to the real Spansh POST).
    Fail-soft: if the list can't be fetched, `loaded` stays False and `extra_names()` is empty,
    so the resolver just uses the bundled roster."""

    def __init__(self, fetch: Callable[[], list[str]] = fetch_ship_names,
                 *, bundled: tuple[str, ...] = SHIP_NAMES) -> None:
        self._fetch = fetch
        self._bundled = frozenset(bundled)
        self._names: list[str] | None = None      # None until first access
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        """True once a non-empty ship list has been fetched and cached."""
        self._ensure()
        return bool(self._names)

    def refresh(self) -> None:
        """Trigger the (cached) fetch. The app calls this once on a background startup thread so
        the first ship query doesn't pay the network latency; safe to call repeatedly."""
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
        """The canonical ship names Spansh knows that the bundled roster is missing — i.e. hulls
        added since the roster was last curated. Empty until (and unless) the fetch succeeds."""
        self._ensure()
        if not self._names:
            return ()
        return tuple(n for n in self._names if n not in self._bundled)
