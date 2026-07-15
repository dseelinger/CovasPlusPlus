"""Unit tests for NeutronPlanCapability (#43) — offline, hermetic.

A fake Http replays the async job/poll, the clipboard records, and `sleep` is a no-op. Covers the
happy path (speak total jumps + first waypoint, plot next), the missing-jump-range prompt, the
no-destination prompt, the no-start prompt, a fail-soft Spansh error, and empty results.
"""
from __future__ import annotations

from covas.capabilities.neutron_plan_capability import (NeutronPlanCapability, NeutronPlanConfig)
from covas.search.routes import RoutePlotter

# Galaxy plotter result shape (confirmed live): system_jumps[] of {system, jumps}, jumps cumulative.
_OK_RESULT = {"system_jumps": [
    {"system": "Sol", "jumps": 0},
    {"system": "Jackson's Lighthouse", "jumps": 3},
    {"system": "Colonia", "jumps": 8}]}


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


def _cap(http, *, system="Sol", clip=None):
    clip = clip or _Clip()
    cap = NeutronPlanCapability(
        NeutronPlanConfig(enabled=True),
        http=http,
        get_current_system=lambda: system,
        plotter=RoutePlotter(clipboard=clip),
        sleep=lambda _s: None)
    return cap, clip


_ARGS = {"to_system": "Colonia", "jump_range": 55}


def test_tool_and_help_exposed():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": _OK_RESULT})))
    tools = cap.tools()
    assert [t["name"] for t in tools] == ["plot_neutron_route"]
    assert tools[0]["input_schema"]["required"] == ["to_system"]
    assert cap.help_meta().category == "neutron routes"


def test_happy_path_speaks_summary_and_plots_first():
    http = _FakeHttp(post=(202, {"job": "j"}), gets=[(200, {"status": "ok", "result": _OK_RESULT})])
    cap, clip = _cap(http)
    msg = cap.run_tool("plot_neutron_route", dict(_ARGS))
    assert "Colonia" in msg and "8 jumps" in msg and "3 waypoints" in msg
    assert "Sol" in msg                                  # first waypoint spoken
    assert clip.copied == ["Sol"]                        # first waypoint handed to the galaxy map
    assert "clipboard" in msg.lower()


def test_efficiency_default_and_override_ride_the_request():
    # Default efficiency (60) from config when unspecified.
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap, _ = _cap(http)
    cap.run_tool("plot_neutron_route", dict(_ARGS))
    assert "efficiency=60" in http.posts[0]
    # Explicit override, clamped into 1-100.
    http2 = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap2, _ = _cap(http2)
    cap2.run_tool("plot_neutron_route", dict(_ARGS, efficiency=100))
    assert "efficiency=100" in http2.posts[0]


def test_prompts_for_missing_jump_range():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": _OK_RESULT})))
    msg = cap.run_tool("plot_neutron_route", {"to_system": "Colonia"})
    assert "jump range" in msg.lower()


def test_prompts_for_missing_destination():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": _OK_RESULT})))
    msg = cap.run_tool("plot_neutron_route", {"jump_range": 55})
    assert "destination" in msg.lower() or "where do you want" in msg.lower()


def test_prompts_for_start_when_location_unknown():
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap = NeutronPlanCapability(NeutronPlanConfig(enabled=True), http=http,
                                get_current_system=lambda: None, sleep=lambda _s: None)
    msg = cap.run_tool("plot_neutron_route", dict(_ARGS))
    assert "start from" in msg.lower() or "don't know where you are" in msg.lower()


def test_spansh_error_is_soft():
    cap, _ = _cap(_FakeHttp(post=(400, {"error": "bad"})))
    msg = cap.run_tool("plot_neutron_route", dict(_ARGS))
    assert "double-check" in msg.lower() or "couldn't plot" in msg.lower()


def test_no_route_found_message():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": {"system_jumps": []}})))
    msg = cap.run_tool("plot_neutron_route", dict(_ARGS))
    assert "couldn't plot a neutron route" in msg.lower()
