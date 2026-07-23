"""Mining helper (#45) — nearest ring HOTSPOTS for a material + the best FRESH place to sell it.

Unlike the trade/riches/neutron planners (async job/poll route endpoints — see `routes.py`), the two
pieces a mining session needs are ordinary SYNCHRONOUS Spansh `/search` queries, so this module rides
`spansh.execute_search` (POST returns the results array inline), NOT the async `submit_and_poll`
client. It's kept OUT of `routes.py` on purpose: it shares none of the trade-route code, so a separate
module keeps that shared file (which #44 also touches) untouched and this feature self-contained.

Two builders + parsers, both LIVE-VERIFIED against the real API (2026-07):

  * HOTSPOTS — `POST /api/bodies/search` with a `ring_signals` filter (`[{"name": <material>,
    "value": [min, max]}]`), distance-sorted. Each result body carries its `rings[]`, and each ring a
    `signals[]` list of `{"name", "count"}` plus a `signals_updated_at` timestamp. Confirmed live:
    the filter narrows to bodies whose rings hold that material, `signals[].count` is the hotspot
    count, `signals_updated_at` is the per-ring freshness stamp.

  * BEST SELL — `POST /api/stations/search` filtered to stations trading the commodity
    (`market: [{"name": <commodity>}]`), sorted by that commodity's sell price DESC
    (`market_sell_price: [{"name": <commodity>, "direction": "desc"}]`). Each station carries a
    `market[]` array (`{"commodity", "sell_price", "demand", ...}`) and a `market_updated_at` stamp.

The DIFFERENTIATOR is freshness: confirmed live, the very highest sell prices are almost all on
FLEET CARRIERS with market data years stale (a carrier that jumped away, its old price frozen in the
database). Mining prices swing hard, so a stale quote costs millions. So best-sell drops transient
carriers (the shared `is_fleet_carrier` rule) and splits the rest into FRESH vs STALE on
`market_updated_at` (the shared `data_age_days` age parser): it answers with the best fresh quote,
and only falls back to a stale one WITH a spoken age caveat — never silently.

Everything I/O-bound is injected (`http`) so the default `pytest` run is offline and hermetic
(DESIGN §9). Fail soft — every parser skips a malformed entry rather than raising.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .spansh import (
    BODIES_URL,
    STATIONS_URL,
    Http,
    data_age_days,
    distance_sort,
    execute_search,
    is_fleet_carrier,
)

# Mining commodity price freshness: prices rotate constantly (a filled sell order moves the price),
# so a quote older than this many days is spoken WITH an age caveat rather than trusted silently.
# Mirrors the trade planner's TRADE_PRICE_MAX_AGE_DAYS discipline — volatile market data answered
# honestly, not dropped.
SELL_PRICE_MAX_AGE_DAYS = 2

# Hotspot signal counts observed live top out around 25; a wide upper bound just means "any hotspot
# of this material" without capping legitimately dense rings.
_HOTSPOT_COUNT_MAX = 1000


# ---- hotspot finder (POST /api/bodies/search, ring_signals filter) -------------------------

@dataclass(frozen=True)
class Hotspot:
    """One ring hotspot for the requested material. `count` is how many overlapping hotspots of
    that material the ring holds; `updated` is the ring's `signals_updated_at` freshness stamp."""
    system: str
    body: str
    ring: str
    material: str
    count: int
    distance_ly: float
    arrival_ls: float
    reserve_level: str
    ring_type: str = ""
    updated: str | None = None

    def age_days(self, *, now: datetime | None = None) -> float | None:
        """Age of this ring's hotspot data in days (None when it carries no timestamp). Reuses the
        unit-tested `spansh.data_age_days` parser."""
        return data_age_days({"t": self.updated}, "t", now=now)


def build_hotspot_request(*, material: str, reference_system: str, min_count: int = 1,
                          size: int = 10) -> dict:
    """Spansh `bodies/search` body for the nearest rings with a `material` hotspot.

    LIVE-VERIFY: the `ring_signals` filter (a `[{"name", "value": [min, max]}]` signals list) and
    the material names it accepts are confirmed against the live API (2026-07). Distance-sorted so
    results[0] is the closest ring."""
    return {
        "filters": {"ring_signals": [{"name": str(material),
                                      "value": [max(1, int(min_count)), _HOTSPOT_COUNT_MAX]}]},
        "sort": distance_sort(),
        "size": int(size),
        "page": 0,
        "reference_system": str(reference_system),
    }


def _ring_hotspots(body: dict, material: str) -> list[Hotspot]:
    """Every ring of `body` that holds a `material` hotspot, as `Hotspot`s (fail soft: a ring
    whose signals don't parse is skipped). A body can hold the material in more than one ring."""
    want = str(material).strip().lower()
    system = str(body.get("system_name") or "an unknown system")
    name = str(body.get("name") or "an unknown body")
    try:
        dist = float(body.get("distance") or 0.0)
    except (TypeError, ValueError):
        dist = 0.0
    try:
        arrival = float(body.get("distance_to_arrival") or 0.0)
    except (TypeError, ValueError):
        arrival = 0.0
    reserve = str(body.get("reserve_level") or "")
    out: list[Hotspot] = []
    for ring in body.get("rings") or []:
        if not isinstance(ring, dict):
            continue
        for sig in ring.get("signals") or []:
            if not isinstance(sig, dict) or str(sig.get("name") or "").strip().lower() != want:
                continue
            try:
                count = int(sig.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            out.append(Hotspot(
                system=system, body=name, ring=str(ring.get("name") or name),
                material=str(sig.get("name") or material), count=count,
                distance_ly=dist, arrival_ls=arrival, reserve_level=reserve,
                ring_type=str(ring.get("type") or ""),
                updated=(str(ring.get("signals_updated_at"))
                         if ring.get("signals_updated_at") else None)))
    return out


def parse_hotspots(results: list[dict], material: str) -> list[Hotspot]:
    """Flatten `bodies/search` results into `Hotspot`s for `material`, preserving the API's
    nearest-first order. Skips malformed entries (fail soft)."""
    out: list[Hotspot] = []
    for body in results or []:
        if isinstance(body, dict):
            out.extend(_ring_hotspots(body, material))
    return out


def find_hotspots(http: Http, *, material: str, reference_system: str, min_count: int = 1,
                  size: int = 10, user_agent: str | None = None) -> list[Hotspot]:
    """Run the hotspot search end to end: build the request, POST it, parse the rings. Raises
    `NavError` (spoken-friendly) on a transport/HTTP failure — the capability returns it verbatim."""
    params = build_hotspot_request(material=material, reference_system=reference_system,
                                    min_count=min_count, size=size)
    kwargs = {"reference_system": reference_system,
              "subject": "the hotspot database", "lookup_name": "hotspot lookup"}
    if user_agent:
        kwargs["user_agent"] = user_agent
    results = execute_search(BODIES_URL, params, http, **kwargs)
    return parse_hotspots(results, material)


# ---- best sell price (POST /api/stations/search, market filter) ----------------------------

@dataclass(frozen=True)
class SellMarket:
    """One place to SELL the mined commodity. `sell_price` is credits per ton the station pays;
    `demand` is how much it wants; `updated` is `market_updated_at` (the freshness stamp)."""
    system: str
    station: str
    commodity: str
    sell_price: int
    demand: int
    distance_ly: float
    arrival_ls: float
    pad: str
    station_type: str = ""
    updated: str | None = None

    def age_days(self, *, now: datetime | None = None) -> float | None:
        """Age of this market's price in days (None when it carries no timestamp)."""
        return data_age_days({"t": self.updated}, "t", now=now)


def build_sell_request(*, commodity: str, reference_system: str, size: int = 30,
                       requires_large_pad: bool = False) -> dict:
    """Spansh `stations/search` body for stations that trade `commodity`, sorted by its sell price
    (highest first).

    LIVE-VERIFY: the `market` filter (`[{"name": <commodity>}]`) and the per-commodity sort key
    (`market_sell_price: [{"name", "direction"}]`) are confirmed against the live API (2026-07).
    Freshness is applied CLIENT-SIDE from each station's `market_updated_at` (see
    `best_sell`) — verified live that the top prices are stale fleet carriers, so a server-side age
    filter would still need the carrier drop; doing both client-side keeps it to one query and one
    fixture. `requires_large_pad` narrows to big-ship-capable stations server-side."""
    filters: dict = {"market": [{"name": str(commodity)}]}
    if requires_large_pad:
        filters["has_large_pad"] = {"value": True}
    return {
        "filters": filters,
        "sort": [{"market_sell_price": [{"name": str(commodity), "direction": "desc"}]}],
        "size": int(size),
        "page": 0,
        "reference_system": str(reference_system),
    }


def _largest_pad(station: dict) -> str:
    """The biggest landing pad a station has as S/M/L (from Spansh's boolean pad fields), or ''."""
    if station.get("has_large_pad"):
        return "L"
    if station.get("has_medium_pad"):
        return "M"
    if station.get("has_small_pad"):
        return "S"
    return ""


def parse_sell_markets(results: list[dict], commodity: str, *,
                       include_carriers: bool = False) -> list[SellMarket]:
    """Map `stations/search` results into `SellMarket`s for `commodity`, dropping transient FLEET
    CARRIERS by default (their frozen price is a stale answer — the same rule the station searches
    use) and skipping malformed entries. Order (the API's sell-price-desc) is preserved."""
    want = str(commodity).strip().lower()
    out: list[SellMarket] = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        if not include_carriers and is_fleet_carrier(r):
            continue
        entry = next((e for e in (r.get("market") or [])
                      if isinstance(e, dict)
                      and str(e.get("commodity") or e.get("name") or "").strip().lower() == want),
                     None)
        if entry is None:
            continue
        try:
            sell = int(entry.get("sell_price") or 0)
            demand = int(entry.get("demand") or 0)
        except (TypeError, ValueError):
            continue
        if sell <= 0:
            continue
        try:
            dist = float(r.get("distance") or 0.0)
        except (TypeError, ValueError):
            dist = 0.0
        try:
            arrival = float(r.get("distance_to_arrival") or 0.0)
        except (TypeError, ValueError):
            arrival = 0.0
        out.append(SellMarket(
            system=str(r.get("system_name") or "an unknown system"),
            station=str(r.get("name") or "an unknown station"),
            commodity=str(entry.get("commodity") or commodity),
            sell_price=sell, demand=demand, distance_ly=dist, arrival_ls=arrival,
            pad=_largest_pad(r), station_type=str(r.get("type") or ""),
            updated=(str(r.get("market_updated_at")) if r.get("market_updated_at") else None)))
    return out


def best_sell(markets: list[SellMarket], *, max_age_days: int = SELL_PRICE_MAX_AGE_DAYS,
              now: datetime | None = None) -> tuple[SellMarket | None, bool]:
    """Pick the best place to sell: `(market, is_stale)`.

    Prefer the highest-paying FRESH market (price within `max_age_days`); only if none is fresh
    fall back to the highest-paying market overall and flag it stale (`is_stale=True`) so the caller
    speaks an age caveat. A market with no timestamp counts as fresh — the server data occasionally
    omits it, and a format drift must not drop an otherwise-good quote. `markets` is assumed
    sell-price-descending (the request sorts it), so the first fresh one is the best fresh one."""
    if not markets:
        return None, False
    for m in markets:
        age = m.age_days(now=now)
        if age is None or age <= max_age_days:
            return m, False
    return markets[0], True   # nothing fresh — best available, spoken with a caveat


def find_best_sell(http: Http, *, commodity: str, reference_system: str, size: int = 30,
                   requires_large_pad: bool = False, max_age_days: int = SELL_PRICE_MAX_AGE_DAYS,
                   now: datetime | None = None,
                   user_agent: str | None = None) -> tuple[SellMarket | None, bool]:
    """Run the best-sell search end to end and return `(market, is_stale)` (see `best_sell`).
    Raises `NavError` (spoken-friendly) on a transport/HTTP failure."""
    params = build_sell_request(commodity=commodity, reference_system=reference_system,
                                size=size, requires_large_pad=requires_large_pad)
    kwargs = {"reference_system": reference_system,
              "subject": "the market database", "lookup_name": "sell-price lookup"}
    if user_agent:
        kwargs["user_agent"] = user_agent
    results = execute_search(STATIONS_URL, params, http, **kwargs)
    markets = parse_sell_markets(results, commodity)
    return best_sell(markets, max_age_days=max_age_days, now=now)
