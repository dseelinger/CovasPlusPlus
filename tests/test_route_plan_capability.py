"""Unit tests for RoutePlanCapability (#41) — offline, hermetic.

A fake Http replays the async job/poll, the clipboard records, and `sleep` is a no-op. Covers the
happy path (speak top hop + plot next), the missing-number and no-start prompts, a fail-soft Spansh
error, and the freshness caveat.
"""
from __future__ import annotations

from covas.capabilities.route_plan_capability import RoutePlanCapability, RoutePlanConfig
from covas.search.routes import RoutePlotter

_OK_RESULT = {"hops": [
    {"source": {"system": "Sol", "station": "Galileo"},
     "destination": {"system": "Alioth", "station": "Golden Gate"},
     "commodity": "Palladium", "buy_price": 51000, "sell_price": 58200,
     "profit": 7200, "total_profit": 5184000, "updated_at": "2999-01-01 00:00:00+00"}]}

# A two-hop loop (fresh far-future timestamps so the summary caveat stays quiet).
_LOOP_RESULT = {"hops": [
    {"source": {"system": "Shinrarta Dezhra", "station": "Jameson Memorial"},
     "destination": {"system": "Sol", "station": "Galileo"},
     "commodity": "Palladium", "buy_price": 51000, "sell_price": 58200,
     "profit": 7200, "total_profit": 5184000, "updated_at": "2999-01-01 00:00:00+00"},
    {"source": {"system": "Sol", "station": "Galileo"},
     "destination": {"system": "Alioth", "station": "Golden Gate"},
     "commodity": "Progenitor Cells", "buy_price": 6800, "sell_price": 9100,
     "profit": 2300, "total_profit": 1656000, "updated_at": "2999-01-01 00:00:00+00"}]}


class _FakeHttp:
    def __init__(self, *, post, gets=()):
        self._post, self._gets = post, list(gets)
        self.posts = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.posts.append(url)
        return self._post

    def get_json(self, url, params=None, *, headers=None, timeout=20.0):
        return self._gets.pop(0)


class _Clip:
    def __init__(self): self.copied = []
    def __call__(self, t): self.copied.append(t)


def _cap(http, *, station="Galileo", system="Sol", clip=None):
    clip = clip or _Clip()
    cap = RoutePlanCapability(
        RoutePlanConfig(enabled=True),
        http=http,
        get_current_system=lambda: system,
        get_current_station=lambda: station,
        plotter=RoutePlotter(clipboard=clip),
        sleep=lambda _s: None)
    return cap, clip


_ARGS = {"capital": 100_000_000, "max_cargo": 720, "jump_range": 30}


def test_tool_and_help_exposed():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": _OK_RESULT})))
    assert [t["name"] for t in cap.tools()] == ["plan_trade_route"]
    assert cap.help_meta().category == "trade routes"


def test_happy_path_speaks_top_hop_and_plots_next():
    http = _FakeHttp(post=(202, {"job": "j"}), gets=[(200, {"status": "ok", "result": _OK_RESULT})])
    cap, clip = _cap(http)
    msg = cap.run_tool("plan_trade_route", dict(_ARGS))
    assert "Palladium" in msg and "Golden Gate" in msg and "7,200" in msg
    assert clip.copied == ["Alioth"]                 # next destination handed to the galaxy map
    assert "clipboard" in msg.lower()


def test_prompts_for_missing_numbers():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": _OK_RESULT})))
    msg = cap.run_tool("plan_trade_route", {"capital": 100})     # cargo + range missing
    assert "cargo" in msg.lower() and "jump range" in msg.lower()


def test_prompts_for_start_when_not_docked():
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap = RoutePlanCapability(RoutePlanConfig(enabled=True), http=http,
                              get_current_system=lambda: None, get_current_station=lambda: None,
                              sleep=lambda _s: None)
    msg = cap.run_tool("plan_trade_route", dict(_ARGS))
    assert "dock" in msg.lower() or "starting point" in msg.lower()


def test_spansh_error_is_soft():
    cap, _ = _cap(_FakeHttp(post=(400, {"error": "bad"})))
    msg = cap.run_tool("plan_trade_route", dict(_ARGS))
    assert "couldn't plot" in msg.lower() or "double-check" in msg.lower()


def test_no_route_found_message():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": {"hops": []}})))
    msg = cap.run_tool("plan_trade_route", dict(_ARGS))
    assert "didn't find a profitable route" in msg.lower()


def test_stale_prices_add_caveat():
    stale = {"hops": [dict(_OK_RESULT["hops"][0], updated_at="2000-01-01 00:00:00+00")]}
    cap, _ = _cap(_FakeHttp(post=(200, {"result": stale})))
    msg = cap.run_tool("plan_trade_route", dict(_ARGS))
    assert "days old" in msg.lower()


def test_multi_hop_reads_full_loop_and_round_trip_total():
    http = _FakeHttp(post=(200, {"result": _LOOP_RESULT}))
    cap, clip = _cap(http)
    msg = cap.run_tool("plan_trade_route", dict(_ARGS))
    assert "2 hops" in msg                                # loop length spoken
    assert "Palladium" in msg and "Progenitor Cells" in msg   # BOTH legs, not just the top hop
    assert "Then buy" in msg                              # sequenced readout
    assert "6,840,000" in msg                             # round-trip total (5,184,000 + 1,656,000)
    assert clip.copied == ["Sol"]                         # first destination handed to the map


def test_request_carries_new_options():
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap, _ = _cap(http)
    cap.run_tool("plan_trade_route", dict(_ARGS, max_arrival_distance=1500, allow_planetary=True,
                                          avoid_loops=False, requires_large_pad=True, max_hops=6))
    url = http.posts[0]
    assert "max_system_distance=1500" in url and "allow_planetary=true" in url
    assert "unique=false" in url                          # avoid_loops off -> unique false
    assert "requires_large_pad=true" in url and "max_hops=6" in url


def test_max_price_age_override_flows_to_request():
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap, _ = _cap(http)
    cap.run_tool("plan_trade_route", dict(_ARGS, max_price_age_days=5))
    assert f"max_price_age={5 * 86400}" in http.posts[0]  # days -> seconds (LIVE-VERIFY unit)


def test_per_hop_stale_tag_without_wholesale_caveat():
    # One stale leg + one fresh leg: the leg gets a per-hop age tag, the summary caveat stays quiet.
    mixed = {"hops": [dict(_LOOP_RESULT["hops"][0], updated_at="2000-01-01 00:00:00+00"),
                      _LOOP_RESULT["hops"][1]]}
    cap, _ = _cap(_FakeHttp(post=(200, {"result": mixed})))
    msg = cap.run_tool("plan_trade_route", dict(_ARGS))
    assert "(price ~" in msg                              # per-leg staleness flagged
    assert "heads up" not in msg.lower()                  # but not the wholesale-loop caveat
