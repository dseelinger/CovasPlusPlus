"""EDSM shipyard stock — a per-station oracle for what a shipyard sells RIGHT NOW.

Spansh's per-station `ships` array is the station's CATALOG — it unions every hull ever
reported there, so a fresh record still lists ships the vendor no longer stocks (verified
live 2026-07-11: Spansh listed 34 ships at Laplace Ring / Balante; the station actually
stocked 16 — the reported Type-10 bug). EDSM stores the LATEST EDDN shipyard message
instead — the actual purchasable list, byte-for-byte the same 16 ships Inara displayed for
that station. So the ship search keeps Spansh for the SEARCH (nearby candidates, server-side
filters) and asks EDSM per candidate to CONFIRM the hull is really in stock
(`nav/ship_search.py`) — which is what makes the spoken answer agree with Inara.

EDSM spells marks with spaces ("Krait Mk II") where Spansh doesn't ("Krait MkII"), so stock
sets hold NORMALIZED names (the roster's `_norm` folding) and callers must compare normalized
too — `norm_ship_name` is re-exported here for that.

Fail-soft contract, two distinct failure shapes:
  * `None` — EDSM answered but has no usable shipyard list for the station (unknown station
    or a missing/empty `ships` array). Indistinguishable from "never uploaded", so it is
    NEVER treated as evidence of absence; the caller keeps such a candidate as an unverified
    fallback.
  * `EdsmUnavailable` — transport failure / non-200. The caller stops verifying entirely and
    degrades to the old unverified behavior (a bonus check must never kill the lookup).

`http` is a tiny injected GET seam so the default test run never touches the network
(DESIGN §9); the real one is `RequestsHttp.get_json` built at the app composition root.
"""
from __future__ import annotations

from typing import Protocol

from ..search.spansh import _DEFAULT_UA

# The roster's normalization is the one true name-folding ("Krait Mk II" == "Krait MkII");
# imported once here and re-exported so every stock comparison shares it.
from .ships import _norm as norm_ship_name

SHIPYARD_URL = "https://www.edsm.net/api-system-v1/stations/shipyard"


class EdsmUnavailable(Exception):
    """EDSM couldn't be asked (transport failure or a non-200) — verification is OFF for
    this search, not evidence about any station."""


class HttpGet(Protocol):
    """One-method GET seam, mirroring `search.spansh.Http` for POSTs."""
    def get_json(self, url: str, params: dict | None = None, *, headers: dict | None = None,
                 timeout: float = 20.0) -> tuple[int, object]:
        """GET `url` with query `params`; return (status_code, parsed_json_or_None)."""
        ...


def fetch_ship_stock(system: str, station: str, http: HttpGet, *,
                     base_url: str = SHIPYARD_URL, user_agent: str = _DEFAULT_UA,
                     timeout: float = 10.0) -> frozenset[str] | None:
    """The NORMALIZED ship names `station` (in `system`) currently stocks per EDSM, or None
    when EDSM has no usable list for it. Raises `EdsmUnavailable` on transport/HTTP failure."""
    params = {"systemName": str(system or ""), "stationName": str(station or "")}
    try:
        status, body = http.get_json(base_url, params, headers={"User-Agent": user_agent},
                                     timeout=timeout)
    except Exception as e:  # noqa: BLE001 — any transport failure means "can't verify", not "not stocked"
        raise EdsmUnavailable(f"EDSM unreachable: {e}") from e
    if status != 200:
        raise EdsmUnavailable(f"EDSM HTTP {status}")
    if not isinstance(body, dict):
        return None
    ships = body.get("ships")
    if not isinstance(ships, list):
        return None
    names = frozenset(norm_ship_name(s.get("name")) for s in ships
                      if isinstance(s, dict) and s.get("name"))
    # An empty list can't be told apart from "no data yet", so it is not evidence either.
    return names or None


class EdsmStockLookup:
    """The `stock_lookup` callable `find_closest_ship` expects — (system, station) -> the
    normalized stock set / None — bound to an injected GET seam and config'd once at the
    composition root."""

    def __init__(self, http: HttpGet, *, base_url: str = SHIPYARD_URL,
                 user_agent: str = _DEFAULT_UA, timeout: float = 10.0) -> None:
        self._http = http
        self._base_url = base_url
        self._user_agent = user_agent
        self._timeout = timeout

    def __call__(self, system: str, station: str) -> frozenset[str] | None:
        return fetch_ship_stock(system, station, self._http, base_url=self._base_url,
                                user_agent=self._user_agent, timeout=self._timeout)
