"""Unit tests for the Spansh station lookup (offline, DESIGN §9).

A fake Http returns a RECORDED Spansh response (tests/fixtures/spansh_stations_multicannon
.json — trimmed from the live API), so parsing + nearest-by-distance + the client-side
mount/pad post-filtering are all exercised with zero network. The Spansh module filter can't
narrow by mount or pad (verified against the live API), so those are applied here — these
tests lock that behavior in.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.nav.closest import (ClosestResult, NavError, build_payload,
                               find_closest_module, _pad_ok, _sells_mount)
from covas.nav.modules import resolve

_FIXTURE = Path(__file__).parent / "fixtures" / "spansh_stations_multicannon.json"


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


def _resolved(mount: str):
    r = resolve("Multi-Cannon", "medium", mount)
    return r


# --- request building ----------------------------------------------------------------------

def test_build_payload_sends_name_class_and_reference():
    r = _resolved("gimballed")
    p = build_payload(r, "Sol", size=25)          # no pad constraint
    mod = p["filters"]["modules"][0]
    assert mod["name"] == "Multi-Cannon" and mod["class"] == ["2"]
    # Mount is NOT sent — Spansh ignores it; we post-filter on weapon_mode instead.
    assert "weapon_mode" not in mod and "mount" not in mod
    # The ignored top-level `landing_pad` key is never used; no pad filter without a pad_size.
    assert "landing_pad" not in p["filters"]
    assert not any(k.startswith("has_") for k in p["filters"])
    assert p["reference_system"] == "Sol"
    assert p["sort"] == [{"distance": {"direction": "asc"}}]
    assert p["size"] == 25


def test_build_payload_pushes_pad_filter_server_side():
    """Pad IS server-filterable via has_large_pad/has_medium_pad (the EDDiscovery form)."""
    r = _resolved("fixed")
    assert build_payload(r, "Sol", pad_size="L")["filters"]["has_large_pad"] == {"value": True}
    assert build_payload(r, "Sol", pad_size="M")["filters"]["has_medium_pad"] == {"value": True}
    assert build_payload(r, "Sol", pad_size="S")["filters"]["has_small_pad"] == {"value": True}
    # unknown/blank pad -> no pad filter
    assert not any(k.startswith("has_")
                   for k in build_payload(r, "Sol", pad_size="any")["filters"])


# --- nearest-by-distance + mount post-filter -----------------------------------------------

def test_nearest_fixed_is_the_closest_station():
    """The nearest station in the fixture (Walz Depot, 0.0 ly) sells a Fixed multi-cannon."""
    http = FakeHttp()
    res = find_closest_module(_resolved("fixed"), "Sol", http)
    assert isinstance(res, ClosestResult)
    assert res.station == "Walz Depot" and res.system == "Sol"
    assert res.distance_ly == 0.0
    assert len(http.calls) == 1                       # exactly one query


def test_mount_postfilter_skips_nearer_station_without_it():
    """Walz Depot (0.0 ly) only stocks Fixed; the nearest GIMBALLED multi-cannon is the next
    station out (Barnard's Star), proving mount is filtered from results, not the request."""
    http = FakeHttp()
    res = find_closest_module(_resolved("gimballed"), "Sol", http)
    assert res.system == "Barnard's Star"
    assert res.distance_ly > 0.0
    # The request itself never mentioned the mount.
    assert "weapon_mode" not in http.calls[0]["payload"]["filters"]["modules"][0]


def test_result_carries_pad_and_extra():
    res = find_closest_module(_resolved("fixed"), "Sol", FakeHttp())
    assert res.pad == "L"
    assert "station_type" in res.extra


def test_fleet_carriers_are_skipped_as_transient():
    """A nearer fleet carrier is ignored (it jumps around); the nearest FIXED station wins."""
    body = {"count": 2, "results": [
        {"system_name": "Sol", "name": "K7X-99Z", "type": "Drake-Class Carrier",
         "distance": 0.5, "has_large_pad": True, "large_pads": 1,
         "modules": [{"name": "Multi-Cannon", "class": 2, "weapon_mode": "Fixed"}]},
        {"system_name": "Sol", "name": "Walz Depot", "type": "Outpost", "distance": 1.2,
         "has_large_pad": True, "large_pads": 2,
         "modules": [{"name": "Multi-Cannon", "class": 2, "weapon_mode": "Fixed"}]},
    ]}
    res = find_closest_module(_resolved("fixed"), "Sol", FakeHttp(body=body))
    assert res.station == "Walz Depot"            # the carrier at 0.5 ly was skipped


# --- pad post-filter (pure) ----------------------------------------------------------------

def test_pad_ok_rank_logic():
    large = {"has_large_pad": True, "large_pads": 2, "medium_pads": 3, "small_pads": 1}
    med = {"has_large_pad": False, "large_pads": 0, "medium_pads": 2, "small_pads": 1}
    small = {"has_large_pad": False, "large_pads": 0, "medium_pads": 0, "small_pads": 4}
    assert _pad_ok(large, "L") and _pad_ok(large, "M") and _pad_ok(large, "S")
    assert not _pad_ok(med, "L") and _pad_ok(med, "M") and _pad_ok(med, "S")
    assert not _pad_ok(small, "L") and not _pad_ok(small, "M") and _pad_ok(small, "S")
    assert _pad_ok(small, None)                       # no constraint -> always ok


def test_sells_mount_checks_weapon_mode():
    station = {"modules": [
        {"name": "Multi-Cannon", "class": 2, "weapon_mode": "Fixed"},
        {"name": "Multi-Cannon", "class": 2, "weapon_mode": "Turret"},
    ]}
    assert _sells_mount(station, "Multi-Cannon", 2, "Turret")
    assert not _sells_mount(station, "Multi-Cannon", 2, "Gimbal")
    assert _sells_mount(station, "Multi-Cannon", 2, None)   # no mount constraint


# --- pad filtering integrated (crafted body) -----------------------------------------------

def test_pad_filter_can_reject_all_and_raise():
    body = {"count": 1, "results": [
        {"system_name": "Sol", "name": "Tiny Outpost", "distance": 1.0,
         "has_large_pad": False, "large_pads": 0, "medium_pads": 0, "small_pads": 3,
         "modules": [{"name": "Multi-Cannon", "class": 2, "weapon_mode": "Fixed"}]},
    ]}
    http = FakeHttp(body=body)
    with pytest.raises(NavError) as ei:
        find_closest_module(_resolved("fixed"), "Sol", http, pad_size="L")
    assert "pad" in str(ei.value).lower()


# --- failure modes fail soft (NavError, spoken-friendly) -----------------------------------

def test_no_current_system_raises():
    with pytest.raises(NavError) as ei:
        find_closest_module(_resolved("fixed"), "", FakeHttp())
    assert "current system" in str(ei.value).lower()


def test_http_400_reads_as_unknown_system():
    http = FakeHttp(status=400, body={"error": "Invalid request"})
    with pytest.raises(NavError) as ei:
        find_closest_module(_resolved("fixed"), "Bogus System", http)
    assert "recognise" in str(ei.value).lower() or "recognize" in str(ei.value).lower()


def test_non_200_raises():
    http = FakeHttp(status=503, body={})
    with pytest.raises(NavError):
        find_closest_module(_resolved("fixed"), "Sol", http)


def test_empty_results_raises():
    http = FakeHttp(body={"count": 0, "results": []})
    with pytest.raises(NavError) as ei:
        find_closest_module(_resolved("fixed"), "Sol", http)
    assert "couldn't find" in str(ei.value).lower() or "any station" in str(ei.value).lower()


def test_transport_exception_is_wrapped():
    class Boom:
        def post_json(self, *a, **k):
            raise ConnectionError("dns fail")
    with pytest.raises(NavError) as ei:
        find_closest_module(_resolved("fixed"), "Sol", Boom())
    assert "couldn't reach" in str(ei.value).lower()
