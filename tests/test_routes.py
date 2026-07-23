"""Unit tests for the async Spansh route client + trade planner + plot handoff (#41, DESIGN §9).

Offline and hermetic: a fake Http replays queued (202) then done (200) responses, `sleep` is a
no-op so polling is instant, the clipboard is a recorder, and freshness uses an injected `now`.
No network, no real waiting.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from covas.search import NavError
from covas.search.routes import (
    RESULTS_URL,
    RoutePlotter,
    RouteWaypoint,
    build_galaxy_request,
    build_riches_request,
    build_trade_request,
    hop_age_days,
    parse_galaxy_route,
    parse_riches_route,
    parse_trade_route,
    stale_age_caveat,
    submit_and_poll,
)

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeHttp:
    """Records POSTs and replays a scripted sequence of GET (status, body) results for the poll."""

    def __init__(self, *, post=(202, {"job": "job-1"}), gets=()):
        self._post = post
        self._gets = list(gets)
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.posts.append((url, payload))
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    def get_json(self, url, params=None, *, headers=None, timeout=20.0):
        self.gets.append(url)
        return self._gets.pop(0)


class _Sleep:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _seconds: float) -> None:
        self.calls += 1


# --- submit_and_poll -------------------------------------------------------

def test_submit_and_poll_happy_path():
    http = _FakeHttp(post=(202, {"job": "abc"}),
                     gets=[(202, {"status": "queued"}), (200, {"status": "ok", "result": {"ok": 1}})])
    sleep = _Sleep()
    result = submit_and_poll(http, "http://x/route", {"range": 30}, sleep=sleep)
    assert result == {"ok": 1}
    assert http.gets == [f"{RESULTS_URL}abc", f"{RESULTS_URL}abc"]   # polled twice
    assert sleep.calls == 1                                          # slept once on the 202


def test_submit_and_poll_params_on_query_string_lowercase_bools():
    http = _FakeHttp(post=(200, {"result": []}))
    submit_and_poll(http, "http://x/route", {"requires_large_pad": True, "unique": False,
                                             "range": 30, "skip": None})
    url, _ = http.posts[0]
    assert "requires_large_pad=true" in url and "unique=false" in url and "range=30" in url
    assert "skip" not in url                                         # None dropped


def test_submit_and_poll_inline_result_no_job():
    http = _FakeHttp(post=(200, {"result": [1, 2]}))
    assert submit_and_poll(http, "http://x/route", {}) == [1, 2]
    assert http.gets == []                                           # no poll needed


def test_submit_and_poll_400_is_naverror():
    http = _FakeHttp(post=(400, {"error": "bad"}))
    with pytest.raises(NavError) as e:
        submit_and_poll(http, "http://x/route", {"from": "?"})
    assert "double-check" in str(e.value).lower() or "couldn't plot" in str(e.value).lower()


def test_submit_and_poll_transport_error_is_naverror():
    http = _FakeHttp(post=ConnectionError("boom"))
    with pytest.raises(NavError):
        submit_and_poll(http, "http://x/route", {})


def test_submit_and_poll_times_out_after_max_attempts():
    http = _FakeHttp(post=(202, {"job": "j"}), gets=[(202, {})] * 3)
    sleep = _Sleep()
    with pytest.raises(NavError) as e:
        submit_and_poll(http, "http://x/route", {}, sleep=sleep, max_attempts=3)
    assert "too long" in str(e.value).lower()
    assert len(http.gets) == 3


def test_submit_and_poll_keeps_polling_while_status_queued_in_200():
    http = _FakeHttp(post=(202, {"job": "j"}),
                     gets=[(200, {"status": "queued"}), (200, {"status": "ok", "result": 42})])
    assert submit_and_poll(http, "http://x/route", {}, sleep=_Sleep()) == 42


# --- galaxy plotter (confirmed live) ---------------------------------------

def test_build_galaxy_request():
    q = build_galaxy_request("Sol", "Colonia", jump_range=42.5, efficiency=60)
    assert q == {"efficiency": 60, "range": 42.5, "from": "Sol", "to": "Colonia"}


def test_parse_galaxy_route_reads_system_jumps_and_skips_malformed():
    result = {"system_jumps": [{"system": "Sol", "jumps": 1},
                               {"jumps": 2},                 # no system -> skipped
                               {"system": "Colonia", "jumps": 3}]}
    wps = parse_galaxy_route(result)
    assert [w.system for w in wps] == ["Sol", "Colonia"]
    assert wps[0].jumps == 1 and wps[1].jumps == 3


def test_parse_galaxy_route_empty_or_bad():
    assert parse_galaxy_route(None) == []
    assert parse_galaxy_route({"system_jumps": None}) == []


# --- trade planner (shape LIVE-VERIFY) -------------------------------------

def test_build_trade_request():
    q = build_trade_request(from_system="Sol", from_station="Galileo", capital=100_000_000,
                            max_cargo=720, jump_range=30, max_hops=4, max_arrival_distance=5000,
                            requires_large_pad=True, allow_planetary=True, max_price_age_days=2)
    assert q["system"] == "Sol" and q["station"] == "Galileo"
    assert q["starting_capital"] == 100_000_000 and q["max_cargo"] == 720
    assert q["max_hop_distance"] == 30.0 and q["max_hops"] == 4
    assert q["max_system_distance"] == 5000 and q["requires_large_pad"] is True
    assert q["allow_planetary"] is True
    assert q["max_price_age"] == 2 * 86400 and q["unique"] is True


def test_build_trade_request_defaults_and_omits_optionals():
    q = build_trade_request(from_system="Sol", from_station="Galileo", capital=1, max_cargo=1,
                            jump_range=20)
    assert q["requires_large_pad"] is False and q["allow_planetary"] is False
    assert q["unique"] is True                             # avoid-loops on by default
    assert "max_system_distance" not in q                  # no arrival cap unless asked
    assert "max_price_age" not in q                        # no freshness cap unless asked


def test_build_trade_request_avoid_loops_off_maps_to_unique_false():
    q = build_trade_request(from_system="Sol", from_station="Galileo", capital=1, max_cargo=1,
                            jump_range=20, unique=False)
    assert q["unique"] is False


def test_parse_trade_route_from_fixture():
    body = json.loads((FIXTURES / "spansh_trade_route.json").read_text(encoding="utf-8"))
    hops = parse_trade_route(body["result"])              # client returns body.result
    assert len(hops) == 2
    h0 = hops[0]
    assert h0.source_station == "Jameson Memorial" and h0.destination_station == "Galileo"
    assert h0.commodity == "Palladium" and h0.profit_per_unit == 7200
    assert h0.profit_total == 5_184_000 and h0.price_updated == "2026-07-14 22:10:03+00"


def test_parse_trade_route_accepts_bare_list_and_flat_nodes():
    hops = parse_trade_route([{
        "source_system": "A", "source_station": "S1",
        "destination_system": "B", "destination_station": "S2",
        "commodity": "Gold", "buy_price": 100, "sell_price": 250}])
    assert len(hops) == 1 and hops[0].source_station == "S1"
    assert hops[0].profit_per_unit == 150                 # derived sell-buy when profit absent


def test_parse_trade_route_skips_incomplete_hop():
    assert parse_trade_route([{"commodity": "Gold"}]) == []   # no stations -> skipped


# --- Road-to-Riches planner (shape LIVE-VERIFY) ----------------------------

def test_build_riches_request():
    q = build_riches_request(from_system="Sol", jump_range=40, radius=30, max_results=15,
                             min_value=500_000, use_mapping_value=True, max_distance=1000)
    assert q["reference_system"] == "Sol" and q["range"] == 40.0
    assert q["radius"] == 30.0 and q["max_results"] == 15
    assert q["min_value"] == 500_000 and q["use_mapping_value"] is True
    assert q["max_distance"] == 1000 and q["loop"] is False


def test_build_riches_request_omits_max_distance_when_unset():
    assert "max_distance" not in build_riches_request(from_system="Sol", jump_range=40)


def test_parse_riches_route_from_fixture():
    body = json.loads((FIXTURES / "spansh_riches_route.json").read_text(encoding="utf-8"))
    systems = parse_riches_route(body["result"])           # client returns body.result
    assert len(systems) == 3
    s0 = systems[0]
    assert s0.system == "Hypoe Flyi HW-W e1-7966" and s0.body_count == 2
    assert s0.total_value == 3_120_000 and s0.jumps == 0
    assert s0.bodies[0].subtype == "Earth-like world" and s0.bodies[0].value == 2_200_000


def test_parse_riches_route_accepts_bare_list_and_derives_total():
    systems = parse_riches_route([{"name": "A", "bodies": [
        {"name": "A 1", "value": 100}, {"name": "A 2", "estimated_value": 250}]}])
    assert len(systems) == 1 and systems[0].system == "A"
    assert systems[0].total_value == 350                   # summed when no system-level value


def test_parse_riches_route_skips_systems_without_bodies():
    assert parse_riches_route([{"system": "A", "bodies": []}, {"system": "B"}]) == []


def test_parse_riches_route_empty_or_bad():
    assert parse_riches_route(None) == []
    assert parse_riches_route({"systems": None}) == []


# --- freshness -------------------------------------------------------------

def _hop(updated):
    from covas.search.routes import TradeHop
    return TradeHop("A", "S1", "B", "S2", "Gold", 1, 2, 1, 1, price_updated=updated)


def test_stale_age_caveat_none_when_fresh():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    hops = [_hop("2026-07-15 06:00:00+00")]               # ~0.25 days old
    assert stale_age_caveat(hops, now=now) is None


def test_stale_age_caveat_flags_old_prices():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    hops = [_hop("2026-07-05 12:00:00+00"), _hop("2026-07-10 12:00:00+00")]  # youngest ~5d
    msg = stale_age_caveat(hops, now=now)
    assert msg is not None and "5 days old" in msg


def test_stale_age_caveat_none_without_timestamps():
    assert stale_age_caveat([_hop(None)]) is None


def test_stale_age_caveat_fresh_when_youngest_within_window():
    # A mix of one fresh + one old leg: the summary caveat stays quiet (per-leg tags cover it).
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    hops = [_hop("2026-07-15 06:00:00+00"), _hop("2026-07-01 12:00:00+00")]  # 0.25d + 14d
    assert stale_age_caveat(hops, now=now) is None


def test_hop_age_days_reads_price_timestamp():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    assert hop_age_days(_hop("2026-07-10 12:00:00+00"), now=now) == pytest.approx(5.0, abs=0.01)
    assert hop_age_days(_hop(None)) is None


# --- plot handoff ----------------------------------------------------------

class _Clip:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def __call__(self, text: str) -> None:
        self.copied.append(text)


def test_plot_next_copies_next_waypoint_by_default():
    clip = _Clip()
    plotter = RoutePlotter(clipboard=clip)
    msg = plotter.plot_next([RouteWaypoint("Sol", 1), RouteWaypoint("Colonia", 2)])
    assert clip.copied == ["Sol"]
    assert "Sol" in msg and "clipboard" in msg.lower()


def test_plot_next_empty_route():
    clip = _Clip()
    assert "no route" in RoutePlotter(clipboard=clip).plot_next([]).lower()
    assert clip.copied == []


def test_plot_next_uses_set_course_when_available():
    clip = _Clip()
    plotter = RoutePlotter(clipboard=clip, set_course=lambda s: True)
    msg = plotter.plot_next([RouteWaypoint("Sol", 1)])
    assert clip.copied == []                              # set course in-game, no clipboard
    assert "course set for sol" in msg.lower()


def test_plot_next_falls_back_to_clipboard_when_set_course_fails():
    clip = _Clip()
    plotter = RoutePlotter(clipboard=clip, set_course=lambda s: (_ for _ in ()).throw(RuntimeError()))
    msg = plotter.plot_next([RouteWaypoint("Sol", 1)])
    assert clip.copied == ["Sol"] and "clipboard" in msg.lower()


def test_plot_next_clipboard_error_is_soft():
    def boom(_):
        raise RuntimeError("no clipboard")
    msg = RoutePlotter(clipboard=boom).plot_next([RouteWaypoint("Sol", 1)])
    assert "couldn't copy" in msg.lower()
