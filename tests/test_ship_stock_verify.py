"""Unit tests for the EDSM current-stock verification on the ship search (offline, DESIGN §9).

The reported Type-10 bug: Spansh's per-station `ships` array is the station's CATALOG — a
fresh record listed 34 ships at Laplace Ring / Balante while the station stocked 16 (verified
live 2026-07-11 against both Inara and EDSM, which agree with each other because both store
the LATEST EDDN shipyard message). These tests lock the fix: `edsm_stock.fetch_ship_stock`
parsing + failure shapes, and `find_closest_ship`'s verification walk — confirm the nearest
candidate EDSM agrees is in stock, veto contradicted ones (`skipped_stock`), fall back to the
nearest no-data candidate WITH a caveat (`stock_unverified`), survive a dead EDSM, and stay
byte-identical to legacy behavior when no `stock_lookup` is injected (the existing tests).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from covas.nav.edsm_stock import (EdsmStockLookup, EdsmUnavailable, SHIPYARD_URL,
                                  fetch_ship_stock, norm_ship_name)
from covas.nav.ship_search import _STOCK_CHECK_LIMIT, find_closest_ship
from covas.nav.ships import ResolvedShip
from covas.search.spansh import NavError

_NOW = datetime(2026, 7, 11, 16, 0, tzinfo=timezone.utc)

# Trimmed from the live EDSM response for Laplace Ring / Balante (2026-07-11) — note the
# "Mk III" spacing EDSM uses where Spansh writes "MkIII".
_EDSM_BODY = {"id": 3009, "name": "Balante", "url": "https://www.edsm.net/...",
              "ships": [{"id": 1, "name": "Sidewinder"}, {"id": 6, "name": "Viper Mk III"},
                        {"id": 15, "name": "Type-10 Defender"}]}


class FakeGet:
    """Scripted GET seam for the EDSM client (records calls; never touches the network)."""

    def __init__(self, status: int = 200, body: object = None, exc: Exception | None = None):
        self._status, self._body, self._exc = status, body, exc
        self.calls: list[dict] = []

    def get_json(self, url, params=None, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "params": params, "headers": headers,
                           "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return self._status, self._body


class FakeStock:
    """Scripted stock oracle keyed by station name: a frozenset (stock), None (no data), or
    'boom' (raise). Counts calls so budget/memo behavior is assertable."""

    def __init__(self, by_station: dict):
        self._by_station = by_station
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system, station):
        self.calls.append((system, station))
        val = self._by_station.get(station)
        if val == "boom":
            raise ConnectionError("edsm down")
        return val


class ScriptedHttp:
    """One scripted Spansh body per call, repeating the last (records payloads; no network)."""

    def __init__(self, *bodies: object) -> None:
        self._bodies = list(bodies)
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        i = min(len(self.calls), len(self._bodies) - 1)
        self.calls.append({"url": url, "payload": payload})
        return 200, self._bodies[i]


def _type10() -> ResolvedShip:
    return ResolvedShip(id="type_10", name="Type-10 Defender", symbol="Type9_Military")


def _station(name: str, system: str, distance: float, *, fresh: bool = True) -> dict:
    return {"system_name": system, "name": name, "type": "Coriolis Starport",
            "distance": distance, "has_large_pad": True, "large_pads": 4,
            "shipyard_updated_at": ("2026-07-11 15:52:02+00" if fresh
                                    else "2026-07-01 12:00:00+00"),
            "ships": [{"name": "Type-10 Defender", "price": 124755340,
                       "symbol": "Type9_Military"}]}


def _in_stock(*spansh_names: str) -> frozenset:
    return frozenset(norm_ship_name(n) for n in spansh_names)


# --- the EDSM client (parse + failure shapes) ------------------------------------------------

def test_fetch_parses_and_normalizes_the_live_shape():
    http = FakeGet(body=_EDSM_BODY)
    stock = fetch_ship_stock("Balante", "Laplace Ring", http)
    assert stock is not None
    # EDSM's "Viper Mk III" must equal Spansh's "Viper MkIII" once normalized — that spacing
    # difference is exactly why raw string comparison would be wrong.
    assert norm_ship_name("Viper MkIII") in stock
    assert norm_ship_name("Type-10 Defender") in stock
    assert norm_ship_name("Anaconda") not in stock
    call = http.calls[0]
    assert call["url"] == SHIPYARD_URL
    assert call["params"] == {"systemName": "Balante", "stationName": "Laplace Ring"}
    assert "User-Agent" in call["headers"]


def test_fetch_returns_none_when_edsm_has_no_usable_list():
    assert fetch_ship_stock("X", "Y", FakeGet(body=[])) is None          # not a dict
    assert fetch_ship_stock("X", "Y", FakeGet(body={"id": 1})) is None   # no ships key
    assert fetch_ship_stock("X", "Y", FakeGet(body={"ships": []})) is None   # empty != absent
    assert fetch_ship_stock("X", "Y", FakeGet(body={"ships": "?"})) is None  # drifted shape


def test_fetch_raises_unavailable_on_transport_or_http_failure():
    with pytest.raises(EdsmUnavailable):
        fetch_ship_stock("X", "Y", FakeGet(exc=ConnectionError("dns fail")))
    with pytest.raises(EdsmUnavailable):
        fetch_ship_stock("X", "Y", FakeGet(status=503, body=None))


def test_lookup_class_binds_config():
    http = FakeGet(body=_EDSM_BODY)
    lookup = EdsmStockLookup(http, base_url="https://example.test/shipyard",
                             user_agent="UA-test", timeout=5.0)
    assert lookup("Balante", "Laplace Ring") is not None
    call = http.calls[0]
    assert call["url"] == "https://example.test/shipyard"
    assert call["headers"]["User-Agent"] == "UA-test" and call["timeout"] == 5.0


# --- the verification walk -------------------------------------------------------------------

def test_contradicted_nearer_candidate_is_skipped_for_a_confirmed_one():
    """THE reported bug: Spansh (fresh!) lists the Type-10 at a station whose real stock
    doesn't have it — skip to the nearest candidate current stock CONFIRMS."""
    body = {"results": [_station("Laplace Ring", "Balante", 8.0),
                        _station("Stronghold Carrier", "Ebor", 19.3)]}
    stock = FakeStock({"Laplace Ring": _in_stock("Anaconda", "Python"),
                       "Stronghold Carrier": _in_stock("Type-10 Defender", "Anaconda")})
    res = find_closest_ship(_type10(), "Diaguandri", ScriptedHttp(body),
                            stock_lookup=stock, now=_NOW)
    assert res.station == "Stronghold Carrier" and res.system == "Ebor"
    assert res.extra["skipped_stock"] == "Laplace Ring"
    assert res.extra.get("stock_verified") is True
    assert "stock_unverified" not in res.extra


def test_confirmed_farther_candidate_beats_nearer_unknown():
    # No data for the nearest is NOT evidence of absence — but a positively confirmed
    # station wins, matching what Inara's own nearest-seller list would show.
    body = {"results": [_station("No Data Port", "A", 5.0),
                        _station("Confirmed Dock", "B", 9.0)]}
    stock = FakeStock({"Confirmed Dock": _in_stock("Type-10 Defender")})   # others -> None
    res = find_closest_ship(_type10(), "Sol", ScriptedHttp(body),
                            stock_lookup=stock, now=_NOW)
    assert res.station == "Confirmed Dock"
    assert res.extra.get("stock_verified") is True
    assert "skipped_stock" not in res.extra          # unknown was skipped, not contradicted


def test_nothing_confirmable_answers_nearest_unknown_with_caveat():
    body = {"results": [_station("No Data Port", "A", 5.0),
                        _station("Also No Data", "B", 9.0)]}
    res = find_closest_ship(_type10(), "Sol", ScriptedHttp(body),
                            stock_lookup=FakeStock({}), now=_NOW)
    assert res.station == "No Data Port"             # nearest survives, caveated
    assert res.extra.get("stock_unverified") is True
    assert "stock_verified" not in res.extra


def test_dead_edsm_degrades_to_the_legacy_answer_with_caveat():
    body = {"results": [_station("Laplace Ring", "Balante", 8.0)]}
    stock = FakeStock({"Laplace Ring": "boom"})
    res = find_closest_ship(_type10(), "Diaguandri", ScriptedHttp(body),
                            stock_lookup=stock, now=_NOW)
    assert res.station == "Laplace Ring"             # verification is a bonus, never fatal
    assert res.extra.get("stock_unverified") is True
    assert len(stock.calls) == 1                     # gave up after the first failure


def test_everything_contradicted_is_spoken_not_silent():
    body = {"results": [_station("Laplace Ring", "Balante", 8.0)]}
    stock = FakeStock({"Laplace Ring": _in_stock("Anaconda")})
    with pytest.raises(NavError) as ei:
        find_closest_ship(_type10(), "Diaguandri", ScriptedHttp(body),
                          stock_lookup=stock, now=_NOW)
    msg = str(ei.value)
    assert "Laplace Ring" in msg and "stock" in msg.lower()


def test_check_budget_is_bounded():
    # More catalog-matching candidates than the walk may check: the lookup fires at most
    # _STOCK_CHECK_LIMIT times per pass, and a still-unconfirmed search fails spoken.
    n = _STOCK_CHECK_LIMIT + 5
    body = {"results": [_station(f"Port {i}", "Sol", float(i)) for i in range(n)]}
    stock = FakeStock({f"Port {i}": _in_stock("Anaconda") for i in range(n)})
    with pytest.raises(NavError):
        find_closest_ship(_type10(), "Sol", ScriptedHttp(body),
                          stock_lookup=stock, now=_NOW)
    # Both passes (fresh + stale fallback) walk the same stations; the memo makes the second
    # pass free, so the underlying oracle is hit at most once per station.
    assert len(stock.calls) == _STOCK_CHECK_LIMIT


def test_stale_fallback_pass_reuses_memoized_checks():
    # Fresh pass: only Laplace Ring is fresh, and it's contradicted -> no fresh answer.
    # Stale pass re-walks Laplace Ring (memo — no second fetch) then confirms the stale one.
    body = {"results": [_station("Laplace Ring", "Balante", 8.0),
                        _station("Old Reliable", "C", 30.0, fresh=False)]}
    stock = FakeStock({"Laplace Ring": _in_stock("Anaconda"),
                       "Old Reliable": _in_stock("Type-10 Defender")})
    res = find_closest_ship(_type10(), "Diaguandri", ScriptedHttp(body),
                            stock_lookup=stock, now=_NOW)
    assert res.station == "Old Reliable"
    assert res.extra.get("stock_verified") is True
    assert res.extra.get("stock_age_days") == pytest.approx(10.2, abs=0.1)
    assert [s for _, s in stock.calls].count("Laplace Ring") == 1     # memoized across passes


# --- the capability speaks the outcome -------------------------------------------------------

def _cap(body, stock):
    from covas.capabilities.find_closest_capability import FindClosestShipCapability, NavConfig
    return FindClosestShipCapability(NavConfig(enabled=True), http=ScriptedHttp(body),
                                     get_current_system=lambda: "Diaguandri",
                                     stock_lookup=stock, clipboard=lambda s: None)


def test_capability_says_why_the_nearer_listing_was_skipped():
    body = {"results": [_station("Laplace Ring", "Balante", 8.0),
                        _station("Stronghold Carrier", "Ebor", 19.3)]}
    cap = _cap(body, FakeStock({"Laplace Ring": _in_stock("Anaconda"),
                                "Stronghold Carrier": _in_stock("Type-10 Defender")}))
    line = cap.run_tool("find_closest_ship", {"ship": "type 10"})
    assert "Laplace Ring" in line and "isn't actually available" in line
    assert "Stronghold Carrier" in line and "Ebor" in line
    assert "couldn't verify" not in line                 # confirmed answer: no caveat


def test_capability_caveats_an_unverified_answer():
    body = {"results": [_station("No Data Port", "A", 5.0)]}
    cap = _cap(body, FakeStock({}))
    line = cap.run_tool("find_closest_ship", {"ship": "type 10"})
    assert "No Data Port" in line
    assert "couldn't verify live stock" in line
