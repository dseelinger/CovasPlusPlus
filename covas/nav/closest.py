"""Station location for OUTFITTING — the nearest station SELLING a resolved module.

This is the outfitting category's query builder + parser. The reusable Spansh transport it
used to own (the injected `Http` seam, `RequestsHttp`, POST/parse, 400 / unreachable ->
spoken `NavError`, the distance-sort assumption, fleet-carrier exclusion, and the pad logic)
now lives in `covas/search/spansh.py`, shared with the other five search categories (Search
Prompt 3). This module keeps only what is specific to buying a module:

  * the module-name/class request filter + the server-side pad filter (`build_payload`),
  * the MOUNT post-filter — Spansh's module filter honours only `name`/`class`/`rating`; the
    top-level mount key is SILENTLY IGNORED, so each result carries the station's full
    `modules` list and we filter `weapon_mode` ourselves (`_sells_mount`),
  * picking the nearest fixed station that fits mount + pad (`_nearest_match`, `_to_result`).

Behaviour and this module's public surface are unchanged from before the refactor — the
outfitting capability and `tests/test_nav_closest.py` import the same names and see the same
spoken lines. `http` is injected so the default test run never hits the network (DESIGN §9).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The reusable transport + pad logic now live in the shared Spansh client. Re-exported here
# (Http / RequestsHttp / NavError / _DEFAULT_UA) so the outfitting capability's existing
# imports from `nav.closest` keep working unchanged.
from ..search.spansh import (Http, NavError, RequestsHttp, STATIONS_URL, _DEFAULT_UA,
                             execute_search, is_fleet_carrier, largest_pad as _largest_pad,
                             pad_filter_key as _pad_filter_key, pad_ok as _pad_ok)

# Nearest-station search window. We fetch this many nearest stations that sell the module
# (name+class, pad filtered server-side), then post-filter locally by mount + carrier. Amply
# covers the case where the very nearest station stocks the module but not the wanted mount.
_SEARCH_SIZE = 50
_DEFAULT_BASE_URL = STATIONS_URL

# Kept for the outfitting-specific error wording (execute_search names itself via these).
_STATION_SUBJECT = "the station database"
_STATION_LOOKUP = "station lookup"


@dataclass(frozen=True)
class ClosestResult:
    """The nearest matching station. `extra` carries the softer details the spoken line may
    mention (arrival distance, station type, mount confirmation)."""
    system: str
    station: str
    distance_ly: float
    pad: str                     # "L" | "M" | "S" — the largest pad the station has
    extra: dict = field(default_factory=dict)


# ---- outfitting-specific filtering (pure) -------------------------------------------------

def _sells_mount(result: dict, module_name: str, size: int | None, mount: str | None) -> bool:
    """Does this station's outfitting actually include the wanted module VARIANT? Confirms
    the mount (Spansh can't filter it), and re-checks name/class as a belt-and-braces guard."""
    if mount is None:
        return True
    for m in result.get("modules") or []:
        if m.get("name") != module_name:
            continue
        if size is not None and m.get("class") != size:
            continue
        if m.get("weapon_mode") == mount:
            return True
    return False


# ---- query build + parse ------------------------------------------------------------------

def build_payload(resolved, current_system: str, *, pad_size: str | None = None,
                  size: int = _SEARCH_SIZE) -> dict:
    """The Spansh station-search request body. Module name+class and the PAD constraint go
    server-side; MOUNT is not sent (Spansh ignores it — see the module docstring) and is
    applied when parsing results."""
    module_filter: dict = {"name": resolved.name}
    if resolved.size is not None:
        module_filter["class"] = [str(resolved.size)]
    filters: dict = {"modules": [module_filter]}
    pad_key = _pad_filter_key(pad_size)
    if pad_key is not None:
        filters[pad_key] = {"value": True}
    return {
        "filters": filters,
        "sort": [{"distance": {"direction": "asc"}}],
        "size": int(size),
        "page": 0,
        "reference_system": current_system,
    }


def _nearest_match(results: list[dict], resolved, pad_size: str | None) -> dict | None:
    """First (nearest — results are distance-sorted) fixed station that stocks the wanted
    mount variant and has a big-enough pad. Pad is already filtered server-side; the
    `_pad_ok` check is a backstop, and fleet carriers are dropped as transient."""
    for r in results:
        if is_fleet_carrier(r):
            continue
        if not _sells_mount(r, resolved.name, resolved.size, resolved.mount):
            continue
        if not _pad_ok(r, pad_size):
            continue
        return r
    return None


def _to_result(r: dict) -> ClosestResult:
    extra = {
        "distance_to_arrival": r.get("distance_to_arrival"),
        "station_type": r.get("type"),
        "is_planetary": r.get("is_planetary"),
    }
    return ClosestResult(
        system=r.get("system_name") or "an unknown system",
        station=r.get("name") or "an unknown station",
        distance_ly=float(r.get("distance") or 0.0),
        pad=_largest_pad(r) or "?",
        extra={k: v for k, v in extra.items() if v is not None},
    )


def find_closest_module(resolved, current_system: str, http: Http, *,
                        pad_size: str | None = None,
                        base_url: str = _DEFAULT_BASE_URL,
                        user_agent: str = _DEFAULT_UA,
                        search_size: int = _SEARCH_SIZE) -> ClosestResult:
    """Nearest station selling `resolved`, from `current_system`, via Spansh (`http` injected).

    Raises NavError (with a spoken-friendly message) on: no current system, a Spansh error /
    unreachable API, no station selling the module, or none within the search window that has
    the wanted mount + a big-enough pad. Returns a ClosestResult on success."""
    if not current_system or not str(current_system).strip():
        raise NavError("I don't know your current system yet — is Elite Dangerous running "
                       "with monitoring on? Jump somewhere and I'll have it.")

    payload = build_payload(resolved, current_system, pad_size=pad_size, size=search_size)
    results = execute_search(base_url, payload, http, user_agent=user_agent, timeout=20.0,
                             reference_system=current_system,
                             subject=_STATION_SUBJECT, lookup_name=_STATION_LOOKUP)

    if not results:
        # Pad is filtered server-side, so an empty result under a pad constraint usually means
        # "none with a big-enough pad" rather than "the module doesn't exist".
        if pad_size:
            raise NavError(f"I couldn't find a station with a {pad_size} pad selling a "
                           f"{resolved.label} near you — try relaxing the pad size.")
        raise NavError(f"I couldn't find any station selling a {resolved.label} — it may be "
                       "rare or unavailable near you.")

    match = _nearest_match(results, resolved, pad_size)
    if match is None:
        # Stations sell the module, but none in-window fit the mount/pad constraint.
        pad_note = f" with a {pad_size} pad" if pad_size else ""
        raise NavError(f"I found stations selling that module, but none nearby stock the "
                       f"{resolved.label}{pad_note}. Try relaxing the pad size or mount.")
    return _to_result(match)
