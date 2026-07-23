"""Unit tests for the search staleness filter (offline, DESIGN §9).

Spansh's crowdsourced shipyard/outfitting/BGS data goes stale (observed live 2026-07: a
5-day-old listing offered a ship the vendor no longer stocked), so volatile searches run
fresh-first with a stale fallback spoken WITH an age caveat. These tests lock: the server-side
date-window fragment (`freshness_filter`), the client backstop (`is_fresh`), the age math
(`data_age_days`), the two-pass fresh/stale flow in the ship + outfitting lookups and
`run_query_fresh`, and the spoken caveat (`stale_note`) — all with fakes, zero network.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from covas.capabilities._search_support import run_query_fresh, stale_note
from covas.nav.closest import find_closest_module
from covas.nav.modules import Resolved
from covas.nav.ship_search import build_ship_payload, find_closest_ship
from covas.nav.ships import ResolvedShip
from covas.search.categories import category
from covas.search.spansh import NavError, data_age_days, freshness_filter, is_fresh

_TODAY = date(2026, 7, 11)
_NOW = datetime(2026, 7, 11, 16, 0, tzinfo=UTC)


class ScriptedHttp:
    """Returns one scripted body per call, recording payloads (repeats the last body if
    called again). Never touches the network."""

    def __init__(self, *bodies: object) -> None:
        self._bodies = list(bodies)
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        i = min(len(self.calls), len(self._bodies) - 1)
        self.calls.append({"url": url, "payload": payload})
        return 200, self._bodies[i]


def _station(name: str, distance: float, updated: str | None, *, field: str,
             ships: list | None = None, modules: list | None = None) -> dict:
    r: dict = {"system_name": f"{name} System", "name": name, "type": "Coriolis Starport",
               "distance": distance, "has_large_pad": True, "large_pads": 4}
    if updated is not None:
        r[field] = updated
    if ships is not None:
        r["ships"] = ships
    if modules is not None:
        r["modules"] = modules
    return r


def _anaconda() -> ResolvedShip:
    return ResolvedShip(id="anaconda", name="Anaconda")


def _ship_station(name: str, distance: float, updated: str | None) -> dict:
    return _station(name, distance, updated, field="shipyard_updated_at",
                    ships=[{"name": "Anaconda", "price": 146969450, "symbol": "Anaconda"}])


def _scoop() -> Resolved:
    return Resolved(id="fuel_scoop_6", label="class 6 Fuel Scoop", name="Fuel Scoop",
                    category="internal", size=6)


def _module_station(name: str, distance: float, updated: str | None) -> dict:
    return _station(name, distance, updated, field="outfitting_updated_at",
                    modules=[{"name": "Fuel Scoop", "class": 6}])


# --- the server-side date-window fragment --------------------------------------------------

def test_freshness_filter_builds_the_verified_date_window():
    frag = freshness_filter("shipyard_updated_at", 2, today=_TODAY)
    # DATE-ONLY strings (a datetime is a 400 — verified live), upper bound padded to tomorrow
    # so "today" is inside the window regardless of timezone.
    assert frag == {"shipyard_updated_at":
                    {"comparison": "<=>", "value": ["2026-07-09", "2026-07-12"]}}


def test_ship_payload_carries_the_window_only_when_asked():
    fresh = build_ship_payload(_anaconda(), "Sol", fresh_within_days=2, today=_TODAY)
    assert fresh["filters"]["shipyard_updated_at"]["value"] == ["2026-07-09", "2026-07-12"]
    assert "shipyard_updated_at" not in build_ship_payload(_anaconda(), "Sol")["filters"]


# --- the client-side backstop + age math ----------------------------------------------------

def test_is_fresh_mirrors_the_date_window():
    fresh = {"shipyard_updated_at": "2026-07-09 00:10:00+00"}   # window edge: kept
    stale = {"shipyard_updated_at": "2026-07-06 17:37:41+00"}   # the du Fresne case: dropped
    assert is_fresh(fresh, "shipyard_updated_at", 2, today=_TODAY)
    assert not is_fresh(stale, "shipyard_updated_at", 2, today=_TODAY)


def test_is_fresh_keeps_missing_or_unparseable_timestamps():
    # The server filter is the primary gate — a format drift must not drop results.
    assert is_fresh({}, "shipyard_updated_at", 2, today=_TODAY)
    assert is_fresh({"shipyard_updated_at": "not a date"}, "shipyard_updated_at", 2,
                    today=_TODAY)


def test_data_age_days():
    r = {"shipyard_updated_at": "2026-07-06 16:00:00+00"}
    assert data_age_days(r, "shipyard_updated_at", now=_NOW) == pytest.approx(5.0)
    assert data_age_days({}, "shipyard_updated_at", now=_NOW) is None


# --- ship lookup: fresh-first, stale fallback -----------------------------------------------

def test_fresh_match_answers_in_one_query_with_no_caveat_tag():
    http = ScriptedHttp({"results": [_ship_station("Cregglezone", 0.0, "2026-07-11 15:26:26+00")]})
    res = find_closest_ship(_anaconda(), "Wolf 397", http, now=_NOW)
    assert res.station == "Cregglezone"
    assert "stock_age_days" not in res.extra
    assert len(http.calls) == 1
    assert "shipyard_updated_at" in http.calls[0]["payload"]["filters"]


def test_stale_only_falls_back_once_and_tags_the_age():
    """The du Fresne bug: nothing fresh matches -> ONE retry without the window answers from
    stale data, tagged with its age so the capability speaks the caveat."""
    stale = _ship_station("du Fresne Exchange", 0.0, "2026-07-06 16:00:00+00")
    http = ScriptedHttp({"results": []}, {"results": [stale]})
    res = find_closest_ship(_anaconda(), "Wolf 397", http, now=_NOW)
    assert res.station == "du Fresne Exchange"
    assert res.extra["stock_age_days"] == pytest.approx(5.0)
    assert len(http.calls) == 2
    assert "shipyard_updated_at" not in http.calls[1]["payload"]["filters"]


def test_backstop_skips_a_stale_row_the_server_let_through():
    """If Spansh ever stops honouring the filter, the nearer-but-stale row is still skipped
    on the fresh pass."""
    rows = [_ship_station("Stale Port", 1.0, "2026-06-01 00:00:00+00"),
            _ship_station("Fresh Port", 9.0, "2026-07-11 01:00:00+00")]
    http = ScriptedHttp({"results": rows})
    res = find_closest_ship(_anaconda(), "Sol", http, now=_NOW)
    assert res.station == "Fresh Port"
    assert "stock_age_days" not in res.extra
    assert len(http.calls) == 1


def test_both_passes_empty_still_fails_soft():
    http = ScriptedHttp({"results": []})
    with pytest.raises(NavError) as ei:
        find_closest_ship(_anaconda(), "Sol", http, now=_NOW)
    assert "couldn't find" in str(ei.value).lower()
    assert len(http.calls) == 2


# --- outfitting lookup: same policy ---------------------------------------------------------

def test_module_lookup_runs_fresh_first_and_falls_back_with_age():
    stale = _module_station("Old Depot", 3.0, "2026-07-01 16:00:00+00")
    http = ScriptedHttp({"results": []}, {"results": [stale]})
    res = find_closest_module(_scoop(), "Sol", http, now=_NOW)
    assert res.station == "Old Depot"
    assert res.extra["stock_age_days"] == pytest.approx(10.0)
    assert "outfitting_updated_at" in http.calls[0]["payload"]["filters"]
    assert "outfitting_updated_at" not in http.calls[1]["payload"]["filters"]


def test_module_fresh_match_stays_untagged():
    http = ScriptedHttp({"results": [_module_station("New Depot", 3.0,
                                                     "2026-07-11 01:00:00+00")]})
    res = find_closest_module(_scoop(), "Sol", http, now=_NOW)
    assert res.station == "New Depot"
    assert "stock_age_days" not in res.extra
    assert len(http.calls) == 1


# --- run_query_fresh (the BGS categories) ---------------------------------------------------

def _war_system(name: str, distance: float, updated: str) -> dict:
    return {"name": name, "distance": distance, "updated_at": updated, "state": "War"}


def test_run_query_fresh_passes_fresh_results_through():
    spec = category("misc")
    http = ScriptedHttp({"results": [_war_system("LHS 2476", 5.0, "2026-07-11 12:00:00+00")]})
    results, age = run_query_fresh(spec, {"controlling_minor_faction_state": "War"}, http,
                                   "Wolf 397", user_agent="t", size=10,
                                   fresh_field="updated_at", now=_NOW)
    assert [r["name"] for r in results] == ["LHS 2476"]
    assert age is None
    assert len(http.calls) == 1
    assert "updated_at" in http.calls[0]["payload"]["filters"]


def test_run_query_fresh_falls_back_and_reports_the_age():
    spec = category("misc")
    http = ScriptedHttp({"results": []},
                        {"results": [_war_system("LHS 2412", 5.0, "2026-07-01 16:00:00+00")]})
    results, age = run_query_fresh(spec, {"controlling_minor_faction_state": "War"}, http,
                                   "Wolf 397", user_agent="t", size=10,
                                   fresh_field="updated_at", now=_NOW)
    assert [r["name"] for r in results] == ["LHS 2412"]
    assert age == pytest.approx(10.0)
    assert len(http.calls) == 2
    assert "updated_at" not in http.calls[1]["payload"]["filters"]


def test_run_query_fresh_empty_both_ways():
    spec = category("misc")
    http = ScriptedHttp({"results": []})
    results, age = run_query_fresh(spec, {"controlling_minor_faction_state": "War"}, http,
                                   "Wolf 397", user_agent="t", size=10,
                                   fresh_field="updated_at", now=_NOW)
    assert results == [] and age is None


# --- the spoken caveat ----------------------------------------------------------------------

def test_ship_capability_speaks_the_caveat_on_a_stale_answer():
    """End of the du Fresne story: a stale-fallback answer is spoken WITH the age caveat."""
    from covas.capabilities.find_closest_capability import FindClosestShipCapability, NavConfig
    from covas.nav.closest import ClosestResult

    def fake_search(resolved, system, http, **kw):
        return ClosestResult(system="Wolf 397", station="du Fresne Exchange", distance_ly=0.0,
                             pad="L", extra={"ship_price": 38453967, "stock_age_days": 5.0})

    copied: list[str] = []
    cap = FindClosestShipCapability(NavConfig(enabled=True), http=object(),
                                    get_current_system=lambda: "Wolf 397",
                                    search=fake_search, clipboard=copied.append)
    line = cap.run_tool("find_closest_ship", {"ship": "Anaconda"})
    assert "Fair warning" in line and "5 days old" in line
    assert copied == []            # current system -> the N3 rule still holds


def test_stale_note_phrasing():
    assert stale_note(None) == ""                                  # fresh path: silent
    assert stale_note(5.2, what="that listing",
                      risk="stock may have rotated") == \
        " Fair warning — that listing is 5 days old, so stock may have rotated."
    assert "1 day old" in stale_note(0.4)                          # never says "0 days"
