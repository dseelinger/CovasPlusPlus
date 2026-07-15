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
