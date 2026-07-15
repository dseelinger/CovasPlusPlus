"""Shared, typed Spansh search client (Search Prompt 3).

`nav/closest.py` grew a single-purpose Spansh station lookup for the outfitting feature.
The six in-scope voice-search categories (stations, outfitting, minor factions, star
systems, signals, misc) all talk to the same three Spansh endpoints with the same request
shape and the same failure modes — so the reusable transport lives here, category-agnostic,
and each category contributes only its query builder + result parser (`categories.py`).

Split:
  * `spansh.py`     — the transport: the injected `Http` seam + `RequestsHttp`, POST/parse,
                      400 / unreachable -> spoken `NavError`, the distance-sort assumption,
                      fleet-carrier exclusion, and the landing-pad logic. Knows nothing about
                      any particular category.
  * `categories.py` — one `CategorySpec` per category: its Spansh endpoint and its ACCEPTED
                      Spansh parameter set, a generic filter builder that fails LOUD on any
                      param the category doesn't accept (Spansh silently ignores unknown
                      filter keys — see the module docstring — so we reject them ourselves so
                      the help registry can't drift), and a typed result parser.

Everything I/O-bound is injected so the default `pytest` never hits the network (DESIGN §9).
"""
from .spansh import (Http, NavError, RequestsHttp, distance_sort, execute_search,
                     is_fleet_carrier, largest_pad, pad_filter_key, pad_ok)
from .categories import (BODIES, CATEGORIES, CategorySpec, ParamSpec, StationRecord,
                         SystemRecord, UnknownParamError, build_filters, build_query,
                         category, parse_results, parse_stations, parse_systems)
from .routes import (RICHES_ROUTE_URL, ROUTE_URL, RESULTS_URL, TRADE_ROUTE_URL, RoutePlotter,
                     RouteWaypoint, TradeHop, build_galaxy_request, build_trade_request,
                     parse_galaxy_route, parse_trade_route, stale_age_caveat, submit_and_poll)
from .mining import (Hotspot, SellMarket, best_sell, build_hotspot_request, build_sell_request,
                     find_best_sell, find_hotspots, parse_hotspots, parse_sell_markets)

__all__ = [
    "BODIES",
    "CATEGORIES",
    "CategorySpec",
    "Hotspot",
    "SellMarket",
    "best_sell",
    "build_hotspot_request",
    "build_sell_request",
    "find_best_sell",
    "find_hotspots",
    "parse_hotspots",
    "parse_sell_markets",
    "Http",
    "NavError",
    "RESULTS_URL",
    "RICHES_ROUTE_URL",
    "ROUTE_URL",
    "RoutePlotter",
    "RouteWaypoint",
    "TRADE_ROUTE_URL",
    "TradeHop",
    "build_galaxy_request",
    "build_trade_request",
    "parse_galaxy_route",
    "parse_trade_route",
    "stale_age_caveat",
    "submit_and_poll",
    "ParamSpec",
    "RequestsHttp",
    "StationRecord",
    "SystemRecord",
    "UnknownParamError",
    "build_filters",
    "build_query",
    "category",
    "parse_results",
    "distance_sort",
    "execute_search",
    "is_fleet_carrier",
    "largest_pad",
    "pad_filter_key",
    "pad_ok",
    "parse_stations",
    "parse_systems",
]
