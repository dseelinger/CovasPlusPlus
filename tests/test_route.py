"""Unit tests for route callouts (N4) — offline, free (DESIGN §9).

Pure route tracking (scoopable classification, jumps-remaining math, progress, replot,
completion) plus the capability's callout cadence and gating over crafted bus events with a
fake speak/mute. No journal, no audio.
"""
from __future__ import annotations

import pytest

from covas.capabilities.route_capability import RouteCalloutCapability, RouteConfig
from covas.ed.route import RouteTracker, is_scoopable


# --- scoopable classification (KGBFOAM) ------------------------------------

@pytest.mark.parametrize("cls", list("KGBFOAM"))
def test_scoopable_classes(cls):
    assert is_scoopable(cls) is True


@pytest.mark.parametrize("cls", ["L", "T", "Y", "D", "DA", "DC", "N", "H", "W", "C", "S",
                                 "AeBe", "TTS", "", None])
def test_non_scoopable_classes(cls):
    assert is_scoopable(cls) is False


def test_scoopable_is_case_insensitive_and_leading_letter():
    assert is_scoopable("k") is True
    assert is_scoopable("M_RedGiant") is True     # giants share the class letter, scoopable


# --- RouteTracker ----------------------------------------------------------

def _route(*pairs) -> dict:
    return {"event": "NavRoute",
            "Route": [{"StarSystem": s, "StarClass": c} for s, c in pairs]}


def test_tracker_loads_and_counts_jumps():
    t = RouteTracker()
    t.load(_route(("Sol", "G"), ("A", "K"), ("B", "M"), ("Dest", "K")))
    assert t.active and t.destination == "Dest"
    assert t.jumps_remaining() == 3 and t.jumps_made == 0


def test_tracker_advances_on_jump():
    t = RouteTracker()
    t.load(_route(("Sol", "G"), ("A", "K"), ("B", "M"), ("Dest", "K")))
    t.on_jump("A")
    assert t.jumps_made == 1 and t.jumps_remaining() == 2
    t.on_jump("Dest")
    assert t.jumps_remaining() == 0


def test_tracker_off_route_jump_leaves_progress():
    t = RouteTracker()
    t.load(_route(("Sol", "G"), ("A", "K"), ("Dest", "K")))
    t.on_jump("A")
    t.on_jump("Somewhere Else")           # not on the route
    assert t.jumps_made == 1              # unchanged (a replot would follow)


def test_tracker_replot_resets():
    t = RouteTracker()
    t.load(_route(("Sol", "G"), ("A", "K"), ("Dest", "K")))
    t.on_jump("A")
    t.load(_route(("A", "K"), ("C", "M"), ("D", "K"), ("NewDest", "G")))   # replot
    assert t.destination == "NewDest" and t.jumps_made == 0 and t.jumps_remaining() == 3


def test_tracker_inactive_without_route():
    t = RouteTracker()
    assert not t.active and t.jumps_remaining() is None and t.destination is None
    t.load({"Route": [{"StarSystem": "Solo", "StarClass": "G"}]})    # origin only, no jumps
    assert not t.active


def test_tracker_step_for_returns_class():
    t = RouteTracker()
    t.load(_route(("Sol", "G"), ("Neut", "N")))
    assert t.step_for("Neut").star_class == "N"
    assert t.step_for("Nowhere") is None


# --- capability: callouts + gating -----------------------------------------

class _Cap:
    """Builds a capability with a recording fake speak + toggleable mute."""
    def __init__(self, route, *, cfg=None, muted=False, speak_ok=True):
        self.spoken: list[str] = []
        self.muted = muted
        self.speak_ok = speak_ok
        self.cap = RouteCalloutCapability(
            cfg or RouteConfig(enabled=True, every_n=2),
            speak_line=self._speak,
            load_navroute=lambda: route,
            is_muted=lambda: self.muted)
        self.cap.prime()

    def _speak(self, text):
        if self.muted or not self.speak_ok:   # not spoken (muted / Commander had the floor)
            return False
        self.spoken.append(text)
        return True

    def ev(self, **k):
        k["type"] = "ed_event"
        self.cap.on_event(k)


def _long_route(n=7):
    # S0 origin ... S{n-1} dest; S3 is a non-scoopable neutron star, rest are K
    pairs = [(f"S{i}", "N" if i == 3 else "K") for i in range(n)]
    return {"Route": [{"StarSystem": s, "StarClass": c} for s, c in pairs]}


def test_scoopable_callout_on_target():
    c = _Cap(_long_route())
    c.ev(event="FSDTarget", Name="S1")               # K -> scoopable
    assert c.spoken == ["Next star's scoopable."]


def test_non_scoopable_callout_on_target():
    c = _Cap(_long_route())
    c.ev(event="FSDTarget", Name="S3")               # N (neutron) -> not scoopable
    assert len(c.spoken) == 1 and "isn't scoopable" in c.spoken[0]


def test_target_uses_event_class_when_not_on_route():
    c = _Cap(_long_route())
    c.ev(event="FSDTarget", Name="Detour", StarClass="M")   # off-route, class from event
    assert c.spoken == ["Next star's scoopable."]


def test_repeat_target_not_re_announced():
    c = _Cap(_long_route())
    c.ev(event="FSDTarget", Name="S1")
    c.ev(event="FSDTarget", Name="S1")               # same target again
    assert len(c.spoken) == 1


def test_jumps_remaining_every_nth():
    c = _Cap(_long_route())                          # every_n=2
    c.ev(event="FSDJump", StarSystem="S1")           # made=1 -> silent
    c.ev(event="FSDJump", StarSystem="S2")           # made=2 -> announce (remaining 4)
    assert c.spoken == ["4 jumps remaining to S6."]
    c.ev(event="FSDJump", StarSystem="S3")           # made=3 -> silent
    c.ev(event="FSDJump", StarSystem="S4")           # made=4 -> announce (remaining 2)
    assert c.spoken[-1] == "2 jumps remaining to S6."


def test_singular_jump_phrasing():
    c = _Cap(_long_route(), cfg=RouteConfig(enabled=True, every_n=1))
    c.ev(event="FSDJump", StarSystem="S1")
    c.ev(event="FSDJump", StarSystem="S2")
    c.ev(event="FSDJump", StarSystem="S3")
    c.ev(event="FSDJump", StarSystem="S4")
    c.ev(event="FSDJump", StarSystem="S5")           # made=5 -> remaining 1 -> "1 jump"
    assert c.spoken[-1] == "1 jump remaining to S6."


def test_arrival_callout_and_route_cleared():
    c = _Cap(_long_route(3))                         # S0,S1,S2(dest)
    c.ev(event="FSDJump", StarSystem="S1")
    c.ev(event="FSDJump", StarSystem="S2")           # arrived
    assert c.spoken[-1] == "Arrived at S2. Route complete."
    c.ev(event="FSDJump", StarSystem="S3")           # route cleared -> nothing more
    assert not c.cap._tracker.active


def test_mute_suppresses_callouts():
    c = _Cap(_long_route(), muted=True)
    c.ev(event="FSDTarget", Name="S1")
    c.ev(event="FSDJump", StarSystem="S2")
    assert c.spoken == []


def test_navrouteclear_stops_callouts():
    c = _Cap(_long_route())
    c.ev(event="NavRouteClear")
    c.ev(event="FSDJump", StarSystem="S2")           # no active route
    assert c.spoken == []


def test_per_callout_toggles():
    c = _Cap(_long_route(), cfg=RouteConfig(enabled=True, every_n=1,
                                            callout_scoopable=False))
    c.ev(event="FSDTarget", Name="S1")               # scoopable off
    assert c.spoken == []
    c.ev(event="FSDJump", StarSystem="S1")           # jumps-remaining still on
    assert c.spoken == ["5 jumps remaining to S6."]


def test_skipped_when_busy_does_not_crash_and_can_retry_target():
    # speak returns False (Commander had the floor). The scoopable target is marked announced,
    # so it won't spam; but the pipeline stays alive for later events.
    c = _Cap(_long_route(), speak_ok=False)
    c.ev(event="FSDTarget", Name="S1")
    c.ev(event="FSDJump", StarSystem="S2")           # made=2 -> tries jumps-remaining
    # both attempted (speak returned False) but nothing raised
    assert c.spoken == []
    assert c.cap._tracker.jumps_made == 2


def test_no_tools_and_ambient():
    c = _Cap(_long_route())
    assert c.cap.tools() == []
    assert not hasattr(c.cap, "help_meta")           # ambient: not advertised to the model


def test_config_from_cfg_defaults_and_clamps():
    d = RouteConfig.from_cfg({})
    assert d.enabled is False and d.every_n == 5
    clamped = RouteConfig.from_cfg({"route": {"enabled": True, "every_n": 0}})
    assert clamped.every_n == 1                      # every_n floored to 1
