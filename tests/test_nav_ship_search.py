"""Unit tests for the Spansh shipyard lookup (offline, DESIGN §9).

A fake Http returns a RECORDED Spansh response (tests/fixtures/spansh_stations_ship_anaconda
.json — trimmed from the live API: a nearest fleet CARRIER then two real stations, all listing
an Anaconda), so parsing + nearest-by-distance + the carrier/pad filtering are exercised with
zero network. The `ships` name filter is honoured server-side, so — unlike modules' mount —
there's nothing to post-filter for a variant; these tests lock the carrier-exclusion, pad
logic, price read-back, and error handling.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.nav.closest import ClosestResult, NavError
from covas.nav.ship_search import (build_ship_payload, find_closest_ship, _sells_ship)
from covas.nav.ships import resolve_ship

_FIXTURE = Path(__file__).parent / "fixtures" / "spansh_stations_ship_anaconda.json"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class FakeHttp:
    """Records requests and returns a scripted (status, body). Never touches the network."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self._status = status
        self._body = body if body is not None else _load_fixture()
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self._status, self._body


def _anaconda():
    return resolve_ship("Anaconda")


# --- request building --------------------------------------------------------------------

def test_build_payload_sends_ship_name_and_reference():
    p = build_ship_payload(_anaconda(), "Sol", size=25)      # no pad constraint
    ships = p["filters"]["ships"]
    assert ships == [{"name": "Anaconda"}]                   # exact canonical name
    assert not any(k.startswith("has_") for k in p["filters"])   # no pad filter
    # Carriers are excluded SERVER-SIDE (they'd otherwise swamp the window) via a non-carrier
    # type-include filter.
    types = p["filters"]["type"]["value"]
    assert "Drake-Class Carrier" not in types
    assert "Coriolis Starport" in types and "Outpost" in types
    assert p["reference_system"] == "Sol"
    assert p["sort"] == [{"distance": {"direction": "asc"}}]
    assert p["size"] == 25 and p["page"] == 0


def test_build_payload_pushes_pad_filter_server_side():
    a = _anaconda()
    assert build_ship_payload(a, "Sol", pad_size="L")["filters"]["has_large_pad"] == {"value": True}
    assert build_ship_payload(a, "Sol", pad_size="M")["filters"]["has_medium_pad"] == {"value": True}
    assert not any(k.startswith("has_")
                   for k in build_ship_payload(a, "Sol", pad_size="any")["filters"])


# --- nearest-by-distance + carrier exclusion ---------------------------------------------

def test_nearest_noncarrier_station_wins():
    """The nearest result in the fixture is a fleet carrier (excluded); the nearest real
    station selling the Anaconda is Wolf 359 / Cayley Enterprise."""
    http = FakeHttp()
    res = find_closest_ship(_anaconda(), "Sol", http)
    assert isinstance(res, ClosestResult)
    assert res.station == "Cayley Enterprise" and res.system == "Wolf 359"
    assert res.distance_ly > 0.0
    assert len(http.calls) == 1                              # exactly one query


def test_result_carries_pad_price_and_arrival():
    res = find_closest_ship(_anaconda(), "Sol", FakeHttp())
    assert res.pad == "L"
    assert res.extra.get("ship_price") == 146969450         # read from the station ships list
    assert "distance_to_arrival" in res.extra


def test_fleet_carriers_are_skipped_as_transient():
    body = {"count": 2, "results": [
        {"system_name": "Sol", "name": "K7X-99Z", "type": "Drake-Class Carrier",
         "distance": 0.5, "has_large_pad": True, "large_pads": 1,
         "ships": [{"name": "Anaconda", "price": 100, "symbol": "Anaconda"}]},
        {"system_name": "Sol", "name": "Daylight Depot", "type": "Outpost", "distance": 1.2,
         "has_large_pad": True, "large_pads": 2,
         "ships": [{"name": "Anaconda", "price": 100, "symbol": "Anaconda"}]},
    ]}
    res = find_closest_ship(_anaconda(), "Sol", FakeHttp(body=body))
    assert res.station == "Daylight Depot"                  # carrier at 0.5 ly skipped


# --- sells-ship guard (pure) -------------------------------------------------------------

def test_sells_ship_checks_the_ships_list():
    station = {"ships": [{"name": "Python"}, {"name": "Anaconda"}]}
    assert _sells_ship(station, "Anaconda")
    assert not _sells_ship(station, "Krait MkII")
    assert not _sells_ship({}, "Anaconda")


# --- pad filtering integrated (crafted body) ---------------------------------------------

def test_pad_filter_rejects_all_and_raises():
    body = {"count": 1, "results": [
        {"system_name": "Sol", "name": "Tiny Outpost", "distance": 1.0,
         "has_large_pad": False, "large_pads": 0, "medium_pads": 0, "small_pads": 3,
         "ships": [{"name": "Anaconda", "price": 100, "symbol": "Anaconda"}]},
    ]}
    with pytest.raises(NavError) as ei:
        find_closest_ship(_anaconda(), "Sol", FakeHttp(body=body), pad_size="L")
    assert "pad" in str(ei.value).lower()


# --- failure modes fail soft (NavError, spoken-friendly) ---------------------------------

def test_no_current_system_raises():
    with pytest.raises(NavError) as ei:
        find_closest_ship(_anaconda(), "", FakeHttp())
    assert "current system" in str(ei.value).lower()


def test_http_400_reads_as_unknown_system():
    http = FakeHttp(status=400, body={"error": "Invalid request"})
    with pytest.raises(NavError) as ei:
        find_closest_ship(_anaconda(), "Bogus System", http)
    assert "recognise" in str(ei.value).lower() or "recognize" in str(ei.value).lower()


def test_non_200_raises():
    with pytest.raises(NavError):
        find_closest_ship(_anaconda(), "Sol", FakeHttp(status=503, body={}))


def test_empty_results_raises():
    http = FakeHttp(body={"count": 0, "results": []})
    with pytest.raises(NavError) as ei:
        find_closest_ship(_anaconda(), "Sol", http)
    assert "couldn't find" in str(ei.value).lower() or "any station" in str(ei.value).lower()


def test_transport_exception_is_wrapped():
    class Boom:
        def post_json(self, *a, **k):
            raise ConnectionError("dns fail")
    with pytest.raises(NavError) as ei:
        find_closest_ship(_anaconda(), "Sol", Boom())
    assert "couldn't reach" in str(ei.value).lower()
