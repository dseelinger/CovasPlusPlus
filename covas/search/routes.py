"""Spansh ROUTE endpoints — the async job/poll sibling of the synchronous /search transport.

Unlike /search (a POST returns results immediately — see `spansh.py`), Spansh's route planners
are ASYNCHRONOUS: the POST enqueues a job and returns HTTP 202 with `{"job": "<id>"}`, and you then
GET `/api/results/<id>` until it's done. Confirmed live (2026-07) for the galaxy plotter
(`/api/route`) and shared by every route planner (trade, Road-to-Riches, neutron, fleet carrier) —
the frontend polls the same `/api/results/<job>` for all of them.

This module is the FOUNDATION every planner (#42 Road-to-Riches, #44 trade, #45 mining) builds on:
  * the submit+poll client (`submit_and_poll`),
  * per-planner request builders + result parsers (galaxy + trade here; others reuse the client),
  * the data-FRESHNESS discipline for volatile market data (the differentiator — built once), and
  * the PLOT-HANDOFF seam (`RoutePlotter`) that hands a computed route to the galaxy map.

`http` and `sleep` are injected so the default test run is hermetic and instant — no network, no
real waiting (DESIGN §9).

LIVE-VERIFY: the galaxy `/api/route` contract (query params `efficiency`/`range`/`from`/`to`;
result `system_jumps[]` of `{system, jumps}`) is confirmed from a live client. The TRADE request
param NAMES and result field NAMES are built to the observed trade planner (its form fields + the
shared job/poll pattern) and are ISOLATED in `build_trade_request` / `parse_trade_route`, so a
field-name correction after on-hardware validation is a one-function change. See `docs/` +
`MANUAL_TESTS.md` for the live-verification step.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from urllib.parse import urlencode

from .spansh import _BASE, Http, NavError, data_age_days

# ---- endpoints ----------------------------------------------------------------------------
# Async route endpoints: POST -> 202 {"job"} -> GET RESULTS_URL + job until done.
ROUTE_URL = f"{_BASE}/route"                 # galaxy / neutron plotter (confirmed live)
TRADE_ROUTE_URL = f"{_BASE}/trade/route"     # trade planner (namespace confirmed; shape LIVE-VERIFY)
RICHES_ROUTE_URL = f"{_BASE}/riches/route"   # Road to Riches (seam for #42)
RESULTS_URL = f"{_BASE}/results/"            # GET <job> until the job completes (confirmed live)

_DEFAULT_UA = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion; +https://github.com/)"

# Poll bounds: the frontend polls ~20× @ 1 s (a job usually finishes in a few seconds). We mirror
# that, bounded so a stuck job degrades to a spoken timeout rather than hanging the voice loop.
POLL_MAX_ATTEMPTS = 20
POLL_INTERVAL_S = 1.0

# Market-price staleness for TRADE routes — prices rotate constantly (a filled buy order moves the
# price). Reuse the search layer's day-window age helper on each hop's price timestamp; a
# stale-but-only answer is spoken WITH an age caveat, never silently dropped.
TRADE_PRICE_MAX_AGE_DAYS = 2


# ---- query rendering ----------------------------------------------------------------------

def _qs(params: dict) -> str:
    """Render route params as a query string. Booleans go as lowercase `true`/`false` (Spansh's
    route API reads query params, not the structured JSON the /search API uses), None is dropped."""
    flat: dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        flat[k] = "true" if v is True else "false" if v is False else str(v)
    return urlencode(flat)


# ---- the async client ---------------------------------------------------------------------

def submit_and_poll(http: Http, url: str, params: dict, *, user_agent: str = _DEFAULT_UA,
                    timeout: float = 20.0, sleep: Callable[[float], None] = time.sleep,
                    max_attempts: int = POLL_MAX_ATTEMPTS, interval: float = POLL_INTERVAL_S,
                    subject: str = "the route planner", lookup_name: str = "route plot") -> object:
    """Run one async route job end to end and return its `result` payload:
    POST (params on the query string) → read the job id from the 202 body → GET RESULTS_URL+job
    until the job completes → return the parsed `result`.

    Raises `NavError` (spoken-friendly, returned verbatim to the LLM) on any transport failure, an
    HTTP 400 (bad params / unknown start system-station), or a job that never completes within the
    poll budget. `sleep` is injected so tests run instantly."""
    headers = {"Content-Type": "application/json", "User-Agent": user_agent}
    qs = _qs(params)
    full = f"{url}?{qs}" if qs else url
    try:
        status, body = http.post_json(full, {}, headers=headers, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — any transport failure degrades to a spoken note
        raise NavError(f"I couldn't reach {subject} just now ({e}). Try again in a moment.") from e

    if status == 400:
        raise NavError(f"{subject.capitalize()} couldn't plot that — double-check the start "
                       "system and station, then try again.")
    if status not in (200, 202) or not isinstance(body, dict):
        raise NavError(f"The {lookup_name} failed (HTTP {status}). Try again shortly.")

    # Most planners enqueue a job (202 + job id); a few can answer inline (200 + result). Support both.
    result = body.get("result")
    if result is not None and not body.get("job"):
        return result
    job = body.get("job")
    if not job:
        raise NavError(f"The {lookup_name} didn't start (no job id came back). Try again shortly.")

    for _ in range(max(1, int(max_attempts))):
        try:
            status, body = http.get_json(f"{RESULTS_URL}{job}", timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise NavError(f"I lost contact with {subject} while it plotted ({e}).") from e
        if status == 202:
            sleep(interval)
            continue                                   # still queued/working
        if status != 200 or not isinstance(body, dict):
            raise NavError(f"The {lookup_name} failed while computing (HTTP {status}).")
        st = str(body.get("status", "")).strip().lower()
        if st in ("queued", "running", "in_progress"):
            sleep(interval)
            continue                                   # done-signal is 200 but still working
        if st and st not in ("ok", "complete", "completed"):
            raise NavError(f"The {lookup_name} reported '{st}'. Try again shortly.")
        result = body.get("result")
        if result is None:
            raise NavError(f"The {lookup_name} finished but returned no route.")
        return result
    raise NavError(f"{subject.capitalize()} is taking too long right now — try again shortly.")


# ---- galaxy / neutron plotter (confirmed live) --------------------------------------------

@dataclass(frozen=True)
class RouteWaypoint:
    """One system on a plotted route. `jumps` is how many jumps to reach it (galaxy plotter)."""
    system: str
    jumps: int = 0


def build_galaxy_request(from_system: str, to_system: str, *, jump_range: float,
                         efficiency: int = 60) -> dict:
    """Query params for the galaxy/neutron plotter (`POST /api/route`). Confirmed live: the API
    takes exactly `efficiency` (1–100, higher = fewer jumps via more neutron boosts), `range`
    (laden jump range, ly), `from`, `to`."""
    return {"efficiency": int(efficiency), "range": float(jump_range),
            "from": str(from_system), "to": str(to_system)}


def parse_galaxy_route(result: object) -> list[RouteWaypoint]:
    """The plotted systems from a galaxy-route `result` (`{"system_jumps": [{system, jumps}]}`),
    skipping any malformed entry (fail soft). Confirmed live."""
    jumps = result.get("system_jumps") if isinstance(result, dict) else None
    out: list[RouteWaypoint] = []
    for j in jumps or []:
        if isinstance(j, dict) and j.get("system"):
            try:
                out.append(RouteWaypoint(system=str(j["system"]), jumps=int(j.get("jumps") or 0)))
            except (TypeError, ValueError):
                continue
    return out


# ---- trade planner (namespace confirmed; request/result shape LIVE-VERIFY) -----------------

@dataclass(frozen=True)
class TradeHop:
    """One leg of a trade route: buy `commodity` at the source station, sell it at the
    destination. `price_updated` is the source market's price timestamp, used for freshness."""
    source_system: str
    source_station: str
    destination_system: str
    destination_station: str
    commodity: str
    buy_price: int
    sell_price: int
    profit_per_unit: int
    profit_total: int
    price_updated: str | None = None


def build_trade_request(*, from_system: str, from_station: str, capital: int, max_cargo: int,
                        jump_range: float, max_hops: int = 4, max_arrival_distance: int | None = None,
                        requires_large_pad: bool = False, max_price_age_days: int | None = None,
                        unique: bool = True) -> dict:
    """Query params for the trade planner (`POST /api/trade/route`).

    LIVE-VERIFY: these param NAMES are built to the observed trade form (Source Station, Starting
    Capital, Maximum Hop Distance, Maximum Cargo Capacity, Maximum Hops, Maximum Distance To
    Arrival, Maximum Market Age, Requires Large Pad) + Spansh's route convention. Kept in this one
    function so a name correction after on-hardware validation is a single edit. Freshness rides as
    `max_price_age` — UNIT is a live-verify guess (seconds here); the client-side backstop
    (`annotate_trade_freshness`) is authoritative regardless."""
    params: dict = {
        "system": str(from_system),
        "station": str(from_station),
        "starting_capital": int(capital),
        "max_cargo": int(max_cargo),
        "max_hop_distance": float(jump_range),
        "max_hops": int(max_hops),
        "requires_large_pad": bool(requires_large_pad),
        "unique": bool(unique),
    }
    if max_arrival_distance is not None:
        params["max_system_distance"] = int(max_arrival_distance)
    if max_price_age_days is not None:
        params["max_price_age"] = int(max_price_age_days) * 86400  # LIVE-VERIFY unit (s vs days)
    return params


def _hop_endpoint(node: object, key: str) -> tuple[str, str]:
    """Pull (system, station) from a hop's source/destination node, tolerant of both a nested
    `{"source": {"system","station"}}` and flat `{"source_system","source_station"}` shape."""
    if isinstance(node, dict):
        sub = node.get(key)
        if isinstance(sub, dict):
            return str(sub.get("system") or ""), str(sub.get("station") or "")
        return str(node.get(f"{key}_system") or ""), str(node.get(f"{key}_station") or "")
    return "", ""


def parse_trade_route(result: object) -> list[TradeHop]:
    """Parse the trade planner `result` into `TradeHop`s, skipping malformed legs (fail soft).

    LIVE-VERIFY: field names below (`commodity`, `buy_price`, `sell_price`, `profit`,
    `total_profit`, `updated_at`) are built to the observed Spansh trade result; tolerant of the
    result being either a bare list of hops or `{"hops": [...]}`. Isolated so a correction is one
    edit."""
    hops = result if isinstance(result, list) else (
        result.get("hops") if isinstance(result, dict) else None)
    out: list[TradeHop] = []
    for h in hops or []:
        if not isinstance(h, dict):
            continue
        src_sys, src_stn = _hop_endpoint(h, "source")
        dst_sys, dst_stn = _hop_endpoint(h, "destination")
        commodity = str(h.get("commodity") or h.get("commodity_name") or "")
        if not (commodity and src_stn and dst_stn):
            continue
        try:
            buy = int(h.get("buy_price") or 0)
            sell = int(h.get("sell_price") or 0)
            per = int(h.get("profit") or h.get("profit_per_unit") or (sell - buy))
            total = int(h.get("total_profit") or h.get("profit_total") or 0)
        except (TypeError, ValueError):
            continue
        out.append(TradeHop(
            source_system=src_sys, source_station=src_stn,
            destination_system=dst_sys, destination_station=dst_stn,
            commodity=commodity, buy_price=buy, sell_price=sell,
            profit_per_unit=per, profit_total=total,
            price_updated=(str(h.get("updated_at")) if h.get("updated_at") else None)))
    return out


# ---- freshness discipline (reuses the search layer's age helper) ---------------------------

def stale_age_caveat(hops: list[TradeHop], *, max_age_days: int = TRADE_PRICE_MAX_AGE_DAYS,
                     now: datetime | None = None) -> str | None:
    """A spoken age caveat when the FRESHEST hop price is older than the window, else None. This is
    the 'answer stale, with a caveat' fallback — market data is volatile, so an old price is worth
    flagging rather than dropping. Reuses `spansh.data_age_days` (its parser is unit-tested)."""
    ages = [a for h in hops
            if (a := data_age_days({"t": h.price_updated}, "t", now=now)) is not None]
    if not ages:
        return None
    youngest = min(ages)
    if youngest <= max_age_days:
        return None
    return (f"heads up — the freshest prices on this route are about {youngest:.0f} days old, "
            "so they may have moved.")


# ---- plot handoff seam (clipboard fallback until galaxy-map automation lands) ---------------

class RoutePlotter:
    """Hand a computed route to the galaxy map (the closed-loop differentiator).

    Until the Tier-1 galaxy-map keybind automation (#32) lands, `plot_next` degrades to copying the
    NEXT waypoint's system name to the clipboard so the Commander pastes it into the galaxy-map
    search. When `set_course` is later injected (the keybind path), it's tried first and the
    clipboard is the fallback — so planners call `plot_next` unchanged across that transition. Both
    the clipboard and the (future) set_course are injected, keeping the whole thing unit-testable
    and fail-soft (a plot error is spoken, never raised into the loop)."""

    def __init__(self, *, clipboard: Callable[[str], None],
                 set_course: Callable[[str], bool] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self._clipboard = clipboard
        self._set_course = set_course      # future #32: (system) -> True if course set in-game
        self._log = log

    def plot_next(self, waypoints: list[RouteWaypoint]) -> str:
        """Set course to (or copy) the next waypoint. Returns a spoken-friendly confirmation."""
        if not waypoints:
            return "There's no route to plot."
        nxt = waypoints[0].system
        if self._set_course is not None:
            try:
                if self._set_course(nxt):
                    return f"Course set for {nxt}."
            except Exception as e:  # noqa: BLE001 — a plot fault must never crash the loop
                self._logline(f"set_course failed for {nxt}: {e}")
        try:
            self._clipboard(nxt)
        except Exception as e:  # noqa: BLE001
            self._logline(f"clipboard copy failed for {nxt}: {e}")
            return f"I couldn't copy {nxt} to your clipboard just now."
        return (f"Copied {nxt} to your clipboard — paste it into the galaxy-map search to set "
                "course. (Voice-plotting the whole route comes with the keybind actions.)")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
