"""Station location for SHIPYARDS — the nearest station SELLING a resolved ship.

The ship-buying sibling of `closest.py`. It reuses the SAME shared Spansh transport and pad/
carrier plumbing from `covas/search/spansh.py` (the injected `Http` seam, POST/parse, HTTP
400 / unreachable -> spoken `NavError`, the distance-sort assumption, fleet-carrier exclusion,
the server-side pad filter, `pad_ok`) and the SAME `ClosestResult` type as the outfitting
lookup. This module only adds what is specific to buying a SHIP:

  * the `ships` request filter + the server-side pad filter (`build_ship_payload`),
  * a belt-and-braces `_sells_ship` guard over each station's `ships` list,
  * the fresh-first / stale-fallback shipyard-data policy (stock rotates — see
    `_FRESHNESS_FIELD`),
  * the local ground-truth VETO — Spansh's `ships` array is the station's CATALOG, not its
    stock (see `ed/shipyard.py`), so a candidate the Commander's own recent Shipyard.json
    proves out-of-stock is skipped for the next-nearest (`_locally_out_of_stock`) — and
  * picking the nearest fixed station that fits pad (`_nearest_match`, `_to_result`), noting
    the ship's PRICE from the result so the capability can mention it.

Unlike the module filter (whose mount key Spansh silently ignores), the `ships` name filter
IS honoured server-side and is CASE-SENSITIVE exact-match: a wrong/loose name returns zero
(verified live 2026-07). So there is no post-filter to recover a variant — the offline
`resolve_ship()` must have produced the exact canonical name before we get here. `http` is
injected so the default test run never hits the network (DESIGN §9).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

# The reusable transport + pad/carrier logic live in the shared Spansh client; the result
# shape is shared with the outfitting lookup. Import from one place so nothing is duplicated.
from ..search.spansh import (Http, NavError, STATIONS_URL, STOCK_MAX_AGE_DAYS, _DEFAULT_UA,
                             data_age_days, distance_sort, execute_search, freshness_filter,
                             is_fleet_carrier, is_fresh, largest_pad as _largest_pad,
                             pad_filter_key as _pad_filter_key, pad_ok as _pad_ok)
from ..search.stations import STATION_TYPES
from .closest import ClosestResult

# Fleet carriers sell ships too, but they jump — a stale "nearest station" answer — so we drop
# them. UNLIKE modules (sold nearly everywhere), ships are stocked at far fewer stations, and
# near populated space fleet carriers are the overwhelming majority of shipyards (~95% of the
# nearest results in a live sample). Dropping them only client-side would blow the whole search
# window on carriers and leave nothing, so we EXCLUDE THEM SERVER-SIDE by constraining `type`
# to the non-carrier station types (STATION_TYPES deliberately omits Drake-Class Carrier, and
# covers every station type a shipyard actually appears at — verified live 2026-07). The
# client-side is_fleet_carrier check stays as a backstop.
_NON_CARRIER_TYPES = tuple(STATION_TYPES)

# Nearest-station search window. Ships are sold at far fewer stations than modules, and near
# busy systems fleet carriers (dropped) dominate the head of the list, so fetch a generous
# window to be sure a real station survives the carrier/pad filtering.
_SEARCH_SIZE = 50
_DEFAULT_BASE_URL = STATIONS_URL

_STATION_SUBJECT = "the shipyard database"
_STATION_LOOKUP = "shipyard lookup"

# Shipyard stock ROTATES, so a station's ships list is only as current as its last EDDN upload
# (observed live: a 5-day-old listing offered a ship the vendor no longer stocked). The search
# runs fresh-first (server-side date window + client backstop on this field) and falls back to
# stale data — spoken with an age caveat — only when nothing fresh matches.
_FRESHNESS_FIELD = "shipyard_updated_at"


# ---- ship-specific filtering + parse (pure) -----------------------------------------------

def _locally_out_of_stock(result: dict, resolved, snapshot, *,
                          now: datetime | None = None) -> bool:
    """Ground-truth veto: does the Commander's own recent Shipyard.json contradict this
    candidate? True only when the snapshot IS this station (market id when both sides have
    one, else name+system), is fresh enough to trust (STOCK_MAX_AGE_DAYS — stock rotates),
    and its purchasable PriceList does NOT include the ship. Anything unknowable (no
    snapshot, no roster symbol, no timestamp, different station) is False — the veto only
    ever fires on positive local evidence."""
    if snapshot is None or not getattr(resolved, "symbol", ""):
        return False
    market_id = result.get("market_id")
    if market_id is not None and snapshot.market_id is not None:
        try:
            if int(market_id) != snapshot.market_id:
                return False
        except (TypeError, ValueError):
            return False
    elif not snapshot.is_station(result.get("name"), result.get("system_name")):
        return False
    age = snapshot.age_days(now=now)
    if age is None or age > STOCK_MAX_AGE_DAYS:
        return False
    return not snapshot.stocks_symbol(resolved.symbol)


def _sells_ship(result: dict, ship_name: str) -> bool:
    """Does this station's shipyard actually list the wanted ship? The server-side `ships`
    filter already guarantees this, so it's a belt-and-braces guard against a drifted response
    (and it lets us read the ship's price back out)."""
    for s in result.get("ships") or []:
        if s.get("name") == ship_name:
            return True
    return False


def _ship_price(result: dict, ship_name: str) -> int | None:
    """The listed purchase price of `ship_name` at this station, if present."""
    for s in result.get("ships") or []:
        if s.get("name") == ship_name:
            price = s.get("price")
            return int(price) if isinstance(price, (int, float)) else None
    return None


def build_ship_payload(resolved, current_system: str, *, pad_size: str | None = None,
                       size: int = _SEARCH_SIZE, fresh_within_days: int | None = None,
                       today: date | None = None) -> dict:
    """The Spansh station-search request body for a ship: the exact ship name goes in the
    `ships` filter and the PAD constraint goes server-side (the EDDiscovery boolean form).
    `fresh_within_days` adds the server-side shipyard-data date window (None = no window —
    the stale-fallback pass)."""
    filters: dict = {"ships": [{"name": resolved.name}],
                     "type": {"value": list(_NON_CARRIER_TYPES)}}
    pad_key = _pad_filter_key(pad_size)
    if pad_key is not None:
        filters[pad_key] = {"value": True}
    if fresh_within_days is not None:
        filters.update(freshness_filter(_FRESHNESS_FIELD, fresh_within_days, today=today))
    return {
        "filters": filters,
        "sort": distance_sort(),
        "size": int(size),
        "page": 0,
        "reference_system": current_system,
    }


def _nearest_match(results: list[dict], resolved, pad_size: str | None, *,
                   max_age_days: int | None = None, today: date | None = None,
                   local_shipyard=None, now: datetime | None = None,
                   vetoed: list[str] | None = None) -> dict | None:
    """First (nearest — results are distance-sorted) non-carrier station that lists the ship
    and has a big-enough pad. Pad and data age are already filtered server-side; `_pad_ok` and
    `is_fresh` are backstops, and fleet carriers are dropped as transient (they jump).
    `max_age_days` None (the stale-fallback pass) skips the freshness backstop. A candidate
    the local `local_shipyard` snapshot proves out-of-stock is skipped, its name appended to
    `vetoed` so the spoken line can say why the nearest station wasn't the answer."""
    for r in results:
        if is_fleet_carrier(r):
            continue
        if not _sells_ship(r, resolved.name):
            continue
        if not _pad_ok(r, pad_size):
            continue
        if max_age_days is not None and not is_fresh(r, _FRESHNESS_FIELD, max_age_days,
                                                     today=today):
            continue
        if _locally_out_of_stock(r, resolved, local_shipyard, now=now):
            if vetoed is not None:
                vetoed.append(r.get("name") or "a station")
            continue
        return r
    return None


def _to_result(r: dict, ship_name: str, *, stale_age_days: float | None = None,
               skipped_local: str | None = None) -> ClosestResult:
    extra = {
        "distance_to_arrival": r.get("distance_to_arrival"),
        "station_type": r.get("type"),
        "is_planetary": r.get("is_planetary"),
        "ship_price": _ship_price(r, ship_name),
        # Present only on a stale-fallback answer — the capability speaks it as a caveat.
        "stock_age_days": stale_age_days,
        # Present when a nearer station was vetoed by the local Shipyard.json ground truth.
        "skipped_local": skipped_local,
    }
    return ClosestResult(
        system=r.get("system_name") or "an unknown system",
        station=r.get("name") or "an unknown station",
        distance_ly=float(r.get("distance") or 0.0),
        pad=_largest_pad(r) or "?",
        extra={k: v for k, v in extra.items() if v is not None},
    )


def find_closest_ship(resolved, current_system: str, http: Http, *,
                      pad_size: str | None = None,
                      base_url: str = _DEFAULT_BASE_URL,
                      user_agent: str = _DEFAULT_UA,
                      search_size: int = _SEARCH_SIZE,
                      local_shipyard=None,
                      now: datetime | None = None) -> ClosestResult:
    """Nearest station selling `resolved` ship, from `current_system`, via Spansh (`http`
    injected). Fresh-first: only shipyard data at most STOCK_MAX_AGE_DAYS old is trusted
    (stock rotates); when nothing fresh matches, ONE retry without the date window answers
    from stale data, tagged with `stock_age_days` so the caveat is spoken. `local_shipyard`
    (a `ShipyardSnapshot` from the Commander's own Shipyard.json, or None) vetoes a candidate
    the game itself reported out-of-stock — the skipped station lands in `skipped_local` so
    the reply can say why. `now` is injectable for tests.

    Raises NavError (with a spoken-friendly message) on: no current system, a Spansh error /
    unreachable API, no station selling the ship, or none within the window with a big-enough
    pad. Returns a ClosestResult on success."""
    if not current_system or not str(current_system).strip():
        raise NavError("I don't know your current system yet — is Elite Dangerous running "
                       "with monitoring on? Jump somewhere and I'll have it.")

    today = now.astimezone(timezone.utc).date() if now is not None else None
    vetoed: list[str] = []

    payload = build_ship_payload(resolved, current_system, pad_size=pad_size, size=search_size,
                                 fresh_within_days=STOCK_MAX_AGE_DAYS, today=today)
    results = execute_search(base_url, payload, http, user_agent=user_agent, timeout=20.0,
                             reference_system=current_system,
                             subject=_STATION_SUBJECT, lookup_name=_STATION_LOOKUP)
    match = _nearest_match(results, resolved, pad_size,
                           max_age_days=STOCK_MAX_AGE_DAYS, today=today,
                           local_shipyard=local_shipyard, now=now, vetoed=vetoed)

    stale_age: float | None = None
    if match is None:
        # Stale fallback — nothing with fresh data matched. One retry without the date window:
        # an old listing with a caveat beats a dead end (rare hulls / sparse space).
        payload = build_ship_payload(resolved, current_system, pad_size=pad_size,
                                     size=search_size)
        results = execute_search(base_url, payload, http, user_agent=user_agent, timeout=20.0,
                                 reference_system=current_system,
                                 subject=_STATION_SUBJECT, lookup_name=_STATION_LOOKUP)
        match = _nearest_match(results, resolved, pad_size,
                               local_shipyard=local_shipyard, now=now, vetoed=vetoed)
        if match is not None:
            stale_age = data_age_days(match, _FRESHNESS_FIELD, now=now)

    if match is None:
        if vetoed:
            # Everything that matched was contradicted by the Commander's own shipyard visit.
            raise NavError(f"Spansh lists the {resolved.label} at {vetoed[0]}, but the "
                           f"shipyard you visited there doesn't currently stock it, and I "
                           f"found nowhere else nearby. Try again later — stock rotates.")
        if not results:
            # Pad is filtered server-side, so an empty result under a pad constraint usually
            # means "none with a big-enough pad" rather than "no shipyard sells it".
            if pad_size:
                raise NavError(f"I couldn't find a station with a {pad_size} pad selling a "
                               f"{resolved.label} near you — try relaxing the pad size.")
            raise NavError(f"I couldn't find any station selling a {resolved.label} — it may "
                           "be rare or unavailable near you.")
        pad_note = f" with a {pad_size} pad" if pad_size else ""
        raise NavError(f"I found shipyards, but none nearby stock the {resolved.label}"
                       f"{pad_note}. Try relaxing the pad size.")
    # The same station can be vetoed on both passes — report it once.
    skipped = next(iter(dict.fromkeys(vetoed)), None)
    return _to_result(match, resolved.name, stale_age_days=stale_age, skipped_local=skipped)
