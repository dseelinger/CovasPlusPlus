"""Unit tests for LocationCarrierCapability (N3) — offline, free (DESIGN §9).

Injected current-system getter, carrier state, galaxy lookup, and clipboard, so nothing
touches the network or the real clipboard. Covers the three tools plus the N3 "already there
-> don't copy" rule (a current-system answer must NOT call the clipboard).
"""
from __future__ import annotations

from covas.capabilities.base import help_meta_problems
from covas.capabilities.location_capability import LocationCarrierCapability
from covas.nav import CarrierInfo


class Clip:
    def __init__(self):
        self.copied = []

    def __call__(self, text):
        self.copied.append(text)


def _cap(*, system="Sol", fleet=None, squad_name=None, clip=None):
    clip = clip or Clip()
    cap = LocationCarrierCapability(
        get_current_system=lambda: system,
        clipboard=clip,
        get_fleet_carrier=lambda: fleet,
        get_squadron_name=lambda: squad_name,
    )
    return cap, clip


# --- copy current system ---------------------------------------------------

def test_copy_current_system_copies():
    cap, clip = _cap(system="Deciat")
    out = cap.run_tool("copy_current_system", {})
    assert "Deciat" in out and clip.copied == ["Deciat"]


def test_copy_current_system_unknown():
    cap, clip = _cap(system=None)
    out = cap.run_tool("copy_current_system", {})
    assert clip.copied == [] and "don't know your current system" in out.lower()


# --- fleet carrier ---------------------------------------------------------

def test_fleet_carrier_reports_and_copies():
    info = CarrierInfo("Nomad's Rest", "K7X-B0X", "Colonia", None)
    cap, clip = _cap(system="Sol", fleet=info)
    out = cap.run_tool("where_is_fleet_carrier", {})
    assert "Nomad's Rest" in out and "K7X-B0X" in out and "Colonia" in out
    assert clip.copied == ["Colonia"]


def test_fleet_carrier_pending_jump_noted():
    # carrier is elsewhere-with-a-pending-jump; current system is Deciat so it copies
    cap, clip = _cap(system="Deciat", fleet=CarrierInfo("N", "K7X-B0X", "Sol", "Colonia"))
    out = cap.run_tool("where_is_fleet_carrier", {})
    assert "scheduled to jump to Colonia" in out
    assert clip.copied == ["Sol"]


def test_fleet_carrier_already_there_skips_clipboard():
    # N3: the carrier is in the Commander's current system -> say so, DON'T copy.
    info = CarrierInfo("Nomad's Rest", "K7X-B0X", "Sol", None)
    cap, clip = _cap(system="Sol", fleet=info)
    out = cap.run_tool("where_is_fleet_carrier", {})
    assert clip.copied == []                      # fake clipboard asserts NO call
    assert "current system" in out.lower()


def test_fleet_carrier_none_when_no_carrier():
    cap, clip = _cap(fleet=None)
    out = cap.run_tool("where_is_fleet_carrier", {})
    assert clip.copied == [] and "haven't seen a fleet carrier" in out.lower()


def test_fleet_carrier_known_but_no_system():
    info = CarrierInfo("Nomad's Rest", "K7X-B0X", None, None)
    cap, clip = _cap(fleet=info)
    out = cap.run_tool("where_is_fleet_carrier", {})
    assert clip.copied == [] and "don't have its current system" in out.lower()


# --- squadron carrier (in-game pointer only) -------------------------------

def test_squadron_carrier_points_to_in_game_and_never_copies():
    # Squadron carriers aren't queryable remotely -> direct the Commander in-game, no clipboard.
    cap, clip = _cap(system="Sol", squad_name="The Dark Wheel")
    out = cap.run_tool("where_is_squadron_carrier", {})
    assert clip.copied == []
    assert "in-game" in out.lower() or "carrier management" in out.lower()
    assert "The Dark Wheel" in out                     # personalized with the detected squadron


def test_squadron_carrier_works_without_a_known_squadron():
    cap, clip = _cap(squad_name=None)
    out = cap.run_tool("where_is_squadron_carrier", {})
    assert clip.copied == [] and "squadron" in out.lower()


# --- registration / contract ----------------------------------------------

def test_help_meta_is_complete_and_tools_advertised():
    cap, _ = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    names = {t["name"] for t in cap.tools()}
    assert names == {"copy_current_system", "where_is_fleet_carrier", "where_is_squadron_carrier"}
