"""Unit tests for RichesPlanCapability (#42) — offline, hermetic.

A fake Http replays the async job/poll, the clipboard records, and `sleep` is a no-op. Covers the
happy path (speak first system + plot it), the missing-jump-range and no-start prompts, a fail-soft
Spansh error, and the no-route message.
"""
from __future__ import annotations

from covas.capabilities.route_plan_capability import RichesPlanCapability, RichesPlanConfig
from covas.search.routes import RoutePlotter

_OK_RESULT = {"systems": [
    {"system": "Hypoe Flyi HW-W e1-7966", "jumps": 0, "value": 3120000,
     "bodies": [{"name": "A 1", "subtype": "Earth-like world", "value": 2200000},
                {"name": "A 2", "subtype": "Ammonia world", "value": 920000}]},
    {"system": "Byoomao MI-S e4-5423", "jumps": 2, "value": 640000,
     "bodies": [{"name": "B 3", "subtype": "Water world", "value": 640000}]}]}


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
    cap = RichesPlanCapability(
        RichesPlanConfig(enabled=True),
        http=http,
        get_current_system=lambda: system,
        plotter=RoutePlotter(clipboard=clip),
        sleep=lambda _s: None)
    return cap, clip


_ARGS = {"jump_range": 40}


def test_tool_and_help_exposed():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": _OK_RESULT})))
    tools = cap.tools()
    assert [t["name"] for t in tools] == ["plan_riches_route"]
    assert tools[0]["input_schema"]["required"] == ["jump_range"]
    assert cap.help_meta().category == "Road to Riches"


def test_happy_path_speaks_first_system_and_plots_it():
    http = _FakeHttp(post=(202, {"job": "j"}), gets=[(200, {"status": "ok", "result": _OK_RESULT})])
    cap, clip = _cap(http)
    msg = cap.run_tool("plan_riches_route", dict(_ARGS))
    assert "Hypoe Flyi HW-W e1-7966" in msg and "2 bodies" in msg
    assert "3,120,000" in msg and "2 systems" in msg          # first value + system count
    assert clip.copied == ["Hypoe Flyi HW-W e1-7966"]         # first system handed to galaxy map
    assert "clipboard" in msg.lower()


def test_prompts_for_missing_jump_range():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": _OK_RESULT})))
    msg = cap.run_tool("plan_riches_route", {})
    assert "jump range" in msg.lower()


def test_prompts_for_start_when_no_current_system():
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap = RichesPlanCapability(RichesPlanConfig(enabled=True), http=http,
                               get_current_system=lambda: None, sleep=lambda _s: None)
    msg = cap.run_tool("plan_riches_route", dict(_ARGS))
    assert "starting system" in msg.lower() or "where to start" in msg.lower()


def test_uses_given_from_system_over_current():
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap, _ = _cap(http, system="Sol")
    cap.run_tool("plan_riches_route", {"jump_range": 40, "from_system": "Colonia"})
    assert "reference_system=Colonia" in http.posts[0]


def test_non_numeric_slot_gets_friendly_reprompt_not_raw_error():
    # A present-but-non-numeric jump range must reprompt in plain language, not fall through to
    # the fail-soft guard and speak a raw ValueError. Nothing is submitted to Spansh.
    http = _FakeHttp(post=(200, {"result": _OK_RESULT}))
    cap, clip = _cap(http)
    msg = cap.run_tool("plan_riches_route", {"jump_range": "very fast"})
    assert "number" in msg.lower() and "jump range" in msg.lower()
    assert "error" not in msg.lower() and "valueerror" not in msg.lower()
    assert http.posts == [] and clip.copied == []


def test_spansh_error_is_soft():
    cap, _ = _cap(_FakeHttp(post=(400, {"error": "bad"})))
    msg = cap.run_tool("plan_riches_route", dict(_ARGS))
    assert "couldn't plot" in msg.lower() or "double-check" in msg.lower()


def test_no_route_found_message():
    cap, _ = _cap(_FakeHttp(post=(200, {"result": {"systems": []}})))
    msg = cap.run_tool("plan_riches_route", dict(_ARGS))
    assert "didn't find" in msg.lower()
