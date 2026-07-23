"""Unit tests for the local Shipyard.json ground-truth cross-check (offline, DESIGN §9).

Spansh's per-station `ships` array is the station's CATALOG, not its stock (verified live
2026-07: a minutes-fresh record listed 18 ships at a station whose own Shipyard.json stocked
exactly one — the reported Type-8 bug). The game's Shipyard.json PriceList IS stock, so the
ship search vetoes a candidate it contradicts. These tests lock: the fail-soft snapshot
reader (`ed/shipyard.py`), every no-veto guard (different station, stale snapshot, unknown
symbol), the skip-to-next-nearest behavior with the `skipped_local` tag, and the spoken note.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from covas.ed.shipyard import ShipyardSnapshot, read_shipyard_snapshot
from covas.nav.ship_search import find_closest_ship
from covas.nav.ships import ResolvedShip
from covas.search.spansh import NavError

_NOW = datetime(2026, 7, 11, 16, 0, tzinfo=UTC)

# The real shape ED writes (the recorded du Fresne visit, trimmed): PriceList held ONLY the
# Corsair while the vendor's browse UI showed — and Spansh listed — the Type-8.
_SHIPYARD_JSON = ('{ "timestamp":"2026-07-11T15:54:39Z", "event":"Shipyard", '
                  '"MarketID":3536995840, "StationName":"du Fresne Exchange", '
                  '"StarSystem":"Wolf 397", "Horizons":true, "AllowCobraMkIV":false, '
                  '"PriceList":[ { "id":0, "ShipType":"corsair", "ShipPrice":190015874 } ] }')


class ScriptedHttp:
    """Returns one scripted body per call, repeating the last (records payloads; no network)."""

    def __init__(self, *bodies: object) -> None:
        self._bodies = list(bodies)
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        i = min(len(self.calls), len(self._bodies) - 1)
        self.calls.append({"url": url, "payload": payload})
        return 200, self._bodies[i]


def _type8() -> ResolvedShip:
    return ResolvedShip(id="type_8", name="Type-8 Transporter", symbol="Type8")


def _station(name: str, system: str, distance: float, *, market_id: int | None = None) -> dict:
    r: dict = {"system_name": system, "name": name, "type": "Orbis Starport",
               "distance": distance, "has_large_pad": True, "large_pads": 4,
               "shipyard_updated_at": "2026-07-11 15:52:02+00",     # fresh — passes the window
               "ships": [{"name": "Type-8 Transporter", "price": 38453970, "symbol": "Type8"}]}
    if market_id is not None:
        r["market_id"] = market_id
    return r


def _snapshot(*, station: str = "du Fresne Exchange", system: str = "Wolf 397",
              market_id: int | None = 3536995840, when: str = "2026-07-11T15:54:39Z",
              symbols: tuple[str, ...] = ("corsair",)) -> ShipyardSnapshot:
    ts = datetime.fromisoformat(when) if when else None
    return ShipyardSnapshot(station=station, system=system, market_id=market_id,
                            timestamp=ts, symbols=frozenset(symbols))


# --- the snapshot reader (fail-soft) --------------------------------------------------------

def test_reader_parses_the_real_shape(tmp_path):
    p = tmp_path / "Shipyard.json"
    p.write_text(_SHIPYARD_JSON, encoding="utf-8")
    snap = read_shipyard_snapshot(p)
    assert snap is not None
    assert snap.station == "du Fresne Exchange" and snap.system == "Wolf 397"
    assert snap.market_id == 3536995840
    assert snap.symbols == frozenset({"corsair"})
    assert snap.age_days(now=_NOW) == pytest.approx(0.0037, abs=0.001)
    assert snap.stocks_symbol("Corsair") and not snap.stocks_symbol("Type8")
    assert snap.is_station("DU FRESNE EXCHANGE", "wolf 397")       # case-insensitive


def test_reader_fails_soft_on_anything_unusable(tmp_path):
    assert read_shipyard_snapshot(tmp_path / "missing.json") is None
    half = tmp_path / "half.json"
    half.write_text('{ "timestamp":"2026-07-11T15:54:39Z", "event":"Shipy', encoding="utf-8")
    assert read_shipyard_snapshot(half) is None                    # ED rewrites it live
    other = tmp_path / "other.json"
    other.write_text('{"event": "Market", "StationName": "X", "StarSystem": "Y"}',
                     encoding="utf-8")
    assert read_shipyard_snapshot(other) is None                   # not a Shipyard event


def test_reader_accepts_an_empty_pricelist(tmp_path):
    # A shipyard stocking nothing is a VALID observation, not a parse failure.
    p = tmp_path / "Shipyard.json"
    p.write_text('{"timestamp":"2026-07-11T15:54:39Z", "event":"Shipyard", "MarketID": 1,'
                 ' "StationName":"Empty Port", "StarSystem":"Sol", "PriceList":[]}',
                 encoding="utf-8")
    snap = read_shipyard_snapshot(p)
    assert snap is not None and snap.symbols == frozenset()


# --- the veto: skip a candidate the Commander's own visit contradicts -----------------------

def test_contradicted_nearest_is_skipped_for_the_next(tmp_path):
    """THE reported bug: Spansh (fresh!) lists the Type-8 at the station whose own shipyard
    just said UNAVAILABLE — skip it and answer with the next-nearest."""
    body = {"results": [_station("du Fresne Exchange", "Wolf 397", 0.0),
                        _station("Cregglezone", "Wolf 397", 0.0)]}
    res = find_closest_ship(_type8(), "Wolf 397", ScriptedHttp(body),
                            local_shipyard=_snapshot(), now=_NOW)
    assert res.station == "Cregglezone"
    assert res.extra["skipped_local"] == "du Fresne Exchange"


def test_no_veto_when_the_ship_is_locally_in_stock():
    body = {"results": [_station("du Fresne Exchange", "Wolf 397", 0.0)]}
    res = find_closest_ship(_type8(), "Wolf 397", ScriptedHttp(body),
                            local_shipyard=_snapshot(symbols=("corsair", "type8")), now=_NOW)
    assert res.station == "du Fresne Exchange"
    assert "skipped_local" not in res.extra


def test_no_veto_when_the_snapshot_is_stale_or_undated():
    body = {"results": [_station("du Fresne Exchange", "Wolf 397", 0.0)]}
    old = find_closest_ship(_type8(), "Wolf 397", ScriptedHttp(body),
                            local_shipyard=_snapshot(when="2026-07-08T12:00:00Z"), now=_NOW)
    assert old.station == "du Fresne Exchange"     # >2 days old: stock may have rotated back
    undated = find_closest_ship(_type8(), "Wolf 397", ScriptedHttp(body),
                                local_shipyard=_snapshot(when=""), now=_NOW)
    assert undated.station == "du Fresne Exchange"  # no timestamp -> untrusted, no veto


def test_no_veto_for_a_different_station_or_unknown_symbol():
    body = {"results": [_station("Chelbin Service Station", "Wolf 397", 0.0)]}
    res = find_closest_ship(_type8(), "Wolf 397", ScriptedHttp(body),
                            local_shipyard=_snapshot(), now=_NOW)
    assert res.station == "Chelbin Service Station"   # snapshot is about du Fresne
    live_hull = ResolvedShip(id="live:newhull", name="New Hull")   # no roster symbol
    body2 = {"results": [dict(_station("du Fresne Exchange", "Wolf 397", 0.0),
                              ships=[{"name": "New Hull", "price": 1}])]}
    res2 = find_closest_ship(live_hull, "Wolf 397", ScriptedHttp(body2),
                             local_shipyard=_snapshot(), now=_NOW)
    assert res2.station == "du Fresne Exchange"       # can't check what we can't name


def test_market_id_wins_over_a_name_mismatch():
    # Same MarketID = same station, however the names are cased/renamed.
    body = {"results": [_station("Du Fresne Exchange", "wolf 397", 0.0,
                                 market_id=3536995840)]}
    with pytest.raises(NavError) as ei:
        find_closest_ship(_type8(), "Wolf 397", ScriptedHttp(body),
                          local_shipyard=_snapshot(station="renamed"), now=_NOW)
    assert "doesn't currently stock it" in str(ei.value)


def test_everything_vetoed_is_spoken_not_silent():
    body = {"results": [_station("du Fresne Exchange", "Wolf 397", 0.0)]}
    with pytest.raises(NavError) as ei:
        find_closest_ship(_type8(), "Wolf 397", ScriptedHttp(body),
                          local_shipyard=_snapshot(), now=_NOW)
    msg = str(ei.value)
    assert "du Fresne Exchange" in msg and "stock rotates" in msg


# --- the capability speaks the skip --------------------------------------------------------

def test_capability_says_why_the_nearest_station_was_skipped():
    from covas.capabilities.find_closest_capability import FindClosestShipCapability, NavConfig

    body = {"results": [_station("du Fresne Exchange", "Wolf 397", 0.0),
                        _station("Cregglezone", "Wolf 397", 0.0)]}
    copied: list[str] = []
    cap = FindClosestShipCapability(NavConfig(enabled=True), http=ScriptedHttp(body),
                                    get_current_system=lambda: "Wolf 397",
                                    get_local_shipyard=_snapshot,
                                    clipboard=copied.append)
    # Freeze time via the snapshot's own recency: it's dated 2026-07-11 and the veto compares
    # against the real clock, so re-date it to "now" to keep the test time-independent.
    fresh = _snapshot(when=datetime.now(UTC).isoformat())
    cap._local_shipyard = lambda: fresh
    line = cap.run_tool("find_closest_ship", {"ship": "type 8"})
    assert "Spansh lists it at du Fresne Exchange" in line
    assert "Cregglezone" in line
    assert copied == []                            # both are the current system — N3 rule


def test_capability_survives_a_broken_snapshot_reader():
    body = {"results": [_station("Cregglezone", "Wolf 397", 0.0)]}
    from covas.capabilities.find_closest_capability import FindClosestShipCapability, NavConfig

    def boom():
        raise OSError("disk on fire")

    cap = FindClosestShipCapability(NavConfig(enabled=True), http=ScriptedHttp(body),
                                    get_current_system=lambda: "Wolf 397",
                                    get_local_shipyard=boom,
                                    clipboard=lambda s: None)
    line = cap.run_tool("find_closest_ship", {"ship": "type 8"})
    assert "Cregglezone" in line                   # lookup unaffected, reader failure logged
