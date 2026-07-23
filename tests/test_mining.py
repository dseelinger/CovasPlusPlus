"""Unit tests for the mining search layer (#45) — offline, hermetic (DESIGN §9).

A fake Http replays a recorded Spansh response (POST /search is synchronous, so no poll), and
freshness uses an injected `now`/`today`, so no network and no real clock. Fixtures
(`spansh_hotspots_painite.json`, `spansh_sell_painite.json`) are trimmed REAL API responses recorded
live 2026-07 — see the module docstring in `covas/search/mining.py`.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from covas.search import NavError
from covas.search.mining import (
    best_sell,
    build_hotspot_request,
    build_sell_request,
    find_best_sell,
    find_hotspots,
    parse_hotspots,
    parse_sell_markets,
)

FIXTURES = Path(__file__).parent / "fixtures"
# The fixtures' newest timestamps are 2026-07-15; anchor "now" just after so freshness is stable.
NOW = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)


class _FakeHttp:
    """Replays one scripted (status, body) for the synchronous /search POST; records the URL/body."""

    def __init__(self, post=(200, {"results": []})):
        self._post = post
        self.posts: list[tuple[str, dict]] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.posts.append((url, payload))
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    def get_json(self, url, params=None, *, headers=None, timeout=20.0):  # pragma: no cover
        raise AssertionError("mining search is synchronous — get_json should never be called")


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["results"]


# --- hotspot request/parse -------------------------------------------------

def test_build_hotspot_request_ring_signals_filter():
    q = build_hotspot_request(material="Painite", reference_system="Sol", min_count=2, size=5)
    assert q["reference_system"] == "Sol" and q["size"] == 5
    sig = q["filters"]["ring_signals"]
    assert sig == [{"name": "Painite", "value": [2, 1000]}]     # count range, min honoured
    assert q["sort"] == [{"distance": {"direction": "asc"}}]    # nearest first


def test_parse_hotspots_from_fixture():
    hs = parse_hotspots(_load("spansh_hotspots_painite.json"), "Painite")
    # Two bodies each hold Painite in one ring -> two hotspots, nearest first.
    assert [h.body for h in hs] == ["Barnard's Star 5", "WISE 0855-0714 6"]
    h0 = hs[0]
    assert h0.system == "Barnard's Star" and h0.ring == "Barnard's Star 5 A Ring"
    assert h0.material == "Painite" and h0.count == 2
    assert h0.reserve_level == "Depleted" and h0.ring_type == "Metal Rich"
    assert h0.distance_ly == pytest.approx(5.95, abs=0.01)
    assert h0.updated == "2026-07-11 17:32:59+00"


def test_parse_hotspots_only_returns_requested_material():
    # The B ring holds LTDs/Tritium/etc but no Painite; only Painite-bearing rings come back.
    hs = parse_hotspots(_load("spansh_hotspots_painite.json"), "Painite")
    assert all(h.material == "Painite" for h in hs)
    assert all("B Ring" not in h.ring for h in hs)             # the non-Painite ring is skipped


def test_parse_hotspots_matches_multiword_material():
    ltd = parse_hotspots(_load("spansh_hotspots_painite.json"), "Low Temperature Diamonds")
    assert len(ltd) == 1
    assert ltd[0].ring == "Barnard's Star 5 B Ring" and ltd[0].count == 1


def test_hotspot_age_days_reads_signals_timestamp():
    h = parse_hotspots(_load("spansh_hotspots_painite.json"), "Painite")[0]
    assert h.age_days(now=NOW) == pytest.approx(4.02, abs=0.1)  # 2026-07-11 -> 2026-07-15


def test_parse_hotspots_skips_malformed():
    assert parse_hotspots([{"name": "x"}, "junk", {"rings": [{"signals": None}]}], "Painite") == []
    assert parse_hotspots(None, "Painite") == []


def test_find_hotspots_posts_to_bodies_endpoint():
    http = _FakeHttp((200, {"results": _load("spansh_hotspots_painite.json")}))
    hs = find_hotspots(http, material="Painite", reference_system="Sol")
    assert len(hs) == 2
    url, body = http.posts[0]
    assert url.endswith("/bodies/search")
    assert body["filters"]["ring_signals"][0]["name"] == "Painite"


def test_find_hotspots_transport_error_is_naverror():
    with pytest.raises(NavError):
        find_hotspots(_FakeHttp(ConnectionError("boom")), material="Painite", reference_system="Sol")


# --- best-sell request/parse ----------------------------------------------

def test_build_sell_request_market_filter_and_sort():
    q = build_sell_request(commodity="Painite", reference_system="Sol", size=20)
    assert q["filters"] == {"market": [{"name": "Painite"}]}
    assert q["sort"] == [{"market_sell_price": [{"name": "Painite", "direction": "desc"}]}]
    assert q["size"] == 20 and q["reference_system"] == "Sol"


def test_build_sell_request_large_pad_filter():
    q = build_sell_request(commodity="Painite", reference_system="Sol", requires_large_pad=True)
    assert q["filters"]["has_large_pad"] == {"value": True}


def test_parse_sell_markets_drops_carriers_and_reads_price():
    markets = parse_sell_markets(_load("spansh_sell_painite.json"), "Painite")
    # Fixture leads with two stale fleet carriers (highest price) then fresh starports; carriers drop.
    assert all(m.station_type != "Drake-Class Carrier" for m in markets)
    assert markets[0].station == "Bell Vision" and markets[0].sell_price == 467596
    assert markets[0].pad == "L" and markets[0].updated == "2026-07-15 05:42:44+00"


def test_parse_sell_markets_can_include_carriers():
    markets = parse_sell_markets(_load("spansh_sell_painite.json"), "Painite", include_carriers=True)
    assert markets[0].station_type == "Drake-Class Carrier" and markets[0].sell_price == 715960


def test_best_sell_prefers_freshest_over_stale_carrier_price():
    markets = parse_sell_markets(_load("spansh_sell_painite.json"), "Painite")  # carriers already gone
    best, stale = best_sell(markets, max_age_days=2, now=NOW)
    assert best.station == "Bell Vision" and stale is False     # fresh real station wins


def test_best_sell_falls_back_to_stale_with_flag():
    # Everything stale (all carriers, kept in) -> best available, flagged stale.
    markets = parse_sell_markets(_load("spansh_sell_painite.json"), "Painite", include_carriers=True)
    stale_only = [m for m in markets if (m.age_days(now=NOW) or 0) > 2]
    best, stale = best_sell(stale_only, max_age_days=2, now=NOW)
    assert stale is True and best.sell_price == 715960          # the frozen carrier quote, flagged


def test_best_sell_empty_is_none():
    assert best_sell([], now=NOW) == (None, False)


def test_best_sell_missing_timestamp_counts_fresh():
    from covas.search.mining import SellMarket
    m = SellMarket("Sys", "Stn", "Painite", 500000, 100, 1.0, 50.0, "L", updated=None)
    best, stale = best_sell([m], max_age_days=2, now=NOW)
    assert best is m and stale is False


def test_find_best_sell_end_to_end_posts_to_stations():
    http = _FakeHttp((200, {"results": _load("spansh_sell_painite.json")}))
    best, stale = find_best_sell(http, commodity="Painite", reference_system="Sol", now=NOW)
    assert http.posts[0][0].endswith("/stations/search")
    assert best.station == "Bell Vision" and stale is False
