"""Unit tests for the shared Spansh client (offline, DESIGN §9).

Two recorded fixtures (trimmed from the live API 2026-07) drive parsing; query building is a
pure function of a category's accepted params. Together they cover, per category:

  * build_query produces a valid Spansh body (validated filters, distance sort, reference),
  * an unknown param FAILS LOUD (UnknownParamError) — the guard against registry drift, since
    Spansh silently ignores unknown filter keys,
  * parse_results turns a real response into typed records.

Everything runs offline against a fake Http / recorded JSON — no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.search import (BODIES, CATEGORIES, NavError, StationRecord, SystemRecord,
                          build_query, category, execute_search, parse_stations,
                          parse_systems)
from covas.search.categories import (UnknownParamError, build_filters, parse_results)

_FIX = Path(__file__).parent / "fixtures"
_SYSTEMS = _FIX / "spansh_systems_federation_sol.json"
_STATIONS = _FIX / "spansh_stations_largepad_sol.json"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


class FakeHttp:
    """Records requests, returns a scripted (status, body). Never touches the network."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self._status, self._body = status, body
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self._status, self._body


# --- the six categories are all registered, with real endpoints ----------------------------

def test_all_six_categories_present():
    assert set(CATEGORIES) == {"stations", "outfitting", "star_systems",
                               "minor_factions", "signals", "misc"}


def test_each_category_targets_a_real_search_endpoint():
    for spec in CATEGORIES.values():
        assert spec.endpoint.startswith("https://spansh.co.uk/api/")
        assert spec.endpoint.endswith("/search")
        assert spec.result_kind in ("station", "system")


# --- query building: valid params render into the Spansh filter shapes ---------------------

def test_enum_param_renders_as_value_list():
    q = build_query(category("star_systems"), {"allegiance": "Federation"}, "Sol")
    assert q["filters"]["allegiance"] == {"value": ["Federation"]}
    assert q["reference_system"] == "Sol"
    assert q["sort"] == [{"distance": {"direction": "asc"}}]


def test_enum_param_accepts_a_list_value():
    q = build_query(category("star_systems"), {"government": ["Anarchy", "Democracy"]}, "Sol")
    assert q["filters"]["government"] == {"value": ["Anarchy", "Democracy"]}


def test_range_with_both_bounds_renders_inclusive_comparison():
    # Spansh numeric filters use {"value", "comparison"} — NOT {min,max} (silently ignored).
    q = build_query(category("star_systems"),
                    {"population": {"min": 1_000_000, "max": 1_000_000_000}}, "Sol")
    assert q["filters"]["population"] == {"value": [1_000_000, 1_000_000_000],
                                          "comparison": "<=>"}


def test_range_one_sided_renders_a_comparison():
    lo = build_query(category("star_systems"), {"population": {"min": 1_000_000_000}}, "Sol")
    assert lo["filters"]["population"] == {"value": 1_000_000_000, "comparison": ">="}
    hi = build_query(category("stations"), {"distance_to_arrival": {"max": 1000}}, "Sol")
    assert hi["filters"]["distance_to_arrival"] == {"value": 1000, "comparison": "<="}


def test_services_renders_as_list_of_name_objects():
    q = build_query(category("stations"), {"services": ["Shipyard", "Outfitting"]}, "Sol")
    assert q["filters"]["services"] == [{"name": "Shipyard"}, {"name": "Outfitting"}]


def test_bool_param_renders_as_value_bool():
    q = build_query(category("star_systems"), {"needs_permit": False}, "Sol")
    assert q["filters"]["needs_permit"] == {"value": False}


def test_pad_param_renders_to_the_boolean_pad_key():
    q = build_query(category("stations"), {"has_large_pad": "M"}, "Sol")
    # A pad-kind slot maps a pad SIZE to Spansh's has_<size>_pad boolean filter.
    assert q["filters"]["has_medium_pad"] == {"value": True}
    assert "has_large_pad" not in q["filters"]


def test_none_valued_slot_is_skipped():
    q = build_query(category("star_systems"),
                    {"allegiance": "Federation", "government": None}, "Sol")
    assert "government" not in q["filters"]
    assert q["filters"]["allegiance"] == {"value": ["Federation"]}


# --- FAIL LOUD on an unknown param (the anti-drift guard) -----------------------------------

def test_unknown_param_raises_loud():
    with pytest.raises(UnknownParamError) as ei:
        build_query(category("star_systems"), {"not_a_real_filter": "x"}, "Sol")
    assert "star_systems" in str(ei.value) and "not_a_real_filter" in str(ei.value)


def test_param_valid_on_one_category_still_rejected_on_another():
    # `power` is a real star-systems param but NOT a signals param — must still fail loud.
    with pytest.raises(UnknownParamError):
        build_query(category("signals"), {"power": "Zachary Hudson"}, "Sol")


def test_validate_params_lists_accepted_on_failure():
    with pytest.raises(UnknownParamError) as ei:
        category("minor_factions").validate_params(["allegiance", "bogus"])
    msg = str(ei.value)
    assert "bogus" in msg and "allegiance" in msg  # allegiance is accepted, shown in the list


# --- outfitting is bespoke; bodies is an unimplemented seam --------------------------------

def test_outfitting_build_is_bespoke_and_refuses_generic_builder():
    with pytest.raises(NotImplementedError):
        build_filters(category("outfitting"), {"module": "Multi-Cannon"})


def test_bodies_is_an_unimplemented_seam():
    assert BODIES.implemented is False
    with pytest.raises(NotImplementedError):
        build_query(BODIES, {}, "Sol")


def test_category_lookup_rejects_the_bodies_seam_and_unknowns():
    with pytest.raises(KeyError):
        category("bodies")
    with pytest.raises(KeyError):
        category("teleportation")


# --- parsing real recorded responses -------------------------------------------------------

def test_parse_systems_from_fixture():
    systems = parse_systems(_load(_SYSTEMS)["results"])
    assert systems and isinstance(systems[0], SystemRecord)
    sol = systems[0]
    assert sol.name == "Sol"
    assert sol.allegiance == "Federation"
    assert sol.distance_ly == 0.0
    assert isinstance(sol.population, int) and sol.population > 0
    # Powerplay 2.0 lists several powers contesting a system -> a tuple.
    assert isinstance(sol.power, tuple) and len(sol.power) >= 1
    assert sol.controlling_minor_faction == "Mother Gaia"


def test_parse_stations_from_fixture():
    stations = parse_stations(_load(_STATIONS)["results"])
    assert stations and isinstance(stations[0], StationRecord)
    first = stations[0]
    assert first.station and first.system
    assert first.pad in ("S", "M", "L")
    assert first.distance_ly >= 0.0


def test_parse_stations_drops_fleet_carriers():
    body = [
        {"system_name": "Sol", "name": "K7X-99Z", "type": "Drake-Class Carrier",
         "distance": 0.5, "has_large_pad": True, "large_pads": 1},
        {"system_name": "Sol", "name": "Walz Depot", "type": "Outpost", "distance": 1.2,
         "has_large_pad": True, "large_pads": 2},
    ]
    stations = parse_stations(body)
    assert [s.station for s in stations] == ["Walz Depot"]   # the transient carrier is gone


def test_parse_results_dispatches_on_result_kind():
    sys_records = parse_results(category("star_systems"), _load(_SYSTEMS)["results"])
    assert all(isinstance(r, SystemRecord) for r in sys_records)
    stn_records = parse_results(category("stations"), _load(_STATIONS)["results"])
    assert all(isinstance(r, StationRecord) for r in stn_records)


# --- transport: shared error handling (offline, fake Http) ---------------------------------

def test_execute_search_returns_results_list():
    http = FakeHttp(body=_load(_SYSTEMS))
    results = execute_search("https://spansh.co.uk/api/systems/search", {}, http)
    assert isinstance(results, list) and results
    assert http.calls[0]["headers"]["Content-Type"] == "application/json"


def test_execute_search_empty_results_does_not_raise():
    # An empty result set is category-specific to interpret, so the transport returns [].
    assert execute_search("u", {}, FakeHttp(body={"results": []})) == []
    assert execute_search("u", {}, FakeHttp(body={"count": 0})) == []


def test_execute_search_400_reads_as_unknown_system():
    http = FakeHttp(status=400, body={"error": "Invalid request"})
    with pytest.raises(NavError) as ei:
        execute_search("u", {}, http, reference_system="Bogus", subject="the systems database")
    assert "recognise" in str(ei.value).lower() and "Bogus" in str(ei.value)


def test_execute_search_non_200_raises():
    with pytest.raises(NavError):
        execute_search("u", {}, FakeHttp(status=503, body={}), lookup_name="system lookup")


def test_execute_search_transport_exception_is_wrapped():
    class Boom:
        def post_json(self, *a, **k):
            raise ConnectionError("dns fail")
    with pytest.raises(NavError) as ei:
        execute_search("u", {}, Boom())
    assert "couldn't reach" in str(ei.value).lower()
