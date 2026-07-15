"""Unit tests for the StoredCapability (issue #67; offline, DESIGN §9).

Drives the two tools against the recorded StoredShips/StoredModules fixtures through stubbed
snapshot getters + a fake clipboard — no journal directory, no network. Locks the spoken
shapes: located ship/module with the journal's own transfer quote, the here / in-transit
paths, the clipboard handoff + N3 already-there rule, the overview rundowns, the validated
unknown fallback, the no-data-yet path, and help-metadata completeness.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.stored_capability import StoredCapability
from covas.ed.stored import parse_stored_modules, parse_stored_ships

_FIX = Path(__file__).parent / "fixtures"


def _ships():
    return parse_stored_ships(json.loads((_FIX / "journal_stored_ships.json").read_text("utf-8")))


def _modules():
    return parse_stored_modules(
        json.loads((_FIX / "journal_stored_modules.json").read_text("utf-8")))


class _Clip:
    def __init__(self):
        self.copied: list[str] = []

    def __call__(self, text: str) -> None:
        self.copied.append(text)


def _cap(*, ships="fixture", modules="fixture", current="Shinrarta Dezhra", clip=None):
    return StoredCapability(
        get_stored_ships=lambda: (_ships() if ships == "fixture" else ships),
        get_stored_modules=lambda: (_modules() if modules == "fixture" else modules),
        get_current_system=lambda: current,
        clipboard=clip or _Clip(),
    )


# --- no data yet ------------------------------------------------------------------------------

def test_no_stored_ships_yet_says_so():
    out = _cap(ships=None).run_tool("find_stored_ship", {})
    assert "dock at a station with a shipyard" in out.lower()


def test_no_stored_modules_yet_says_so():
    out = _cap(modules=None).run_tool("find_stored_module", {})
    assert "outfitting" in out.lower()


# --- ships: located, with the journal's own transfer quote ------------------------------------

def test_find_remote_ship_speaks_location_and_transfer_and_copies():
    clip = _Clip()
    out = _cap(clip=clip).run_tool("find_stored_ship", {"ship": "Corvette"})
    assert "Federal Corvette" in out
    assert "Sol" in out
    assert "12.2 million credits" in out           # journal TransferPrice, spoken
    assert "26 minutes" in out                     # 1560s -> 26 minutes
    assert clip.copied == ["Sol"]                  # galaxy-map handoff


def test_find_ship_here_needs_no_transfer_and_no_copy():
    clip = _Clip()
    out = _cap(clip=clip).run_tool("find_stored_ship", {"ship": "Cutter"})
    assert "no transfer needed" in out.lower()
    assert clip.copied == []


def test_ship_in_transit_reported():
    out = _cap().run_tool("find_stored_ship", {"ship": "Krait"})
    assert "in transit" in out.lower()


def test_already_in_ships_system_does_not_copy():
    clip = _Clip()
    out = _cap(current="Sol", clip=clip).run_tool("find_stored_ship", {"ship": "Corvette"})
    assert "already there" in out.lower() or "current system" in out.lower()
    assert clip.copied == []


def test_long_transfer_time_reads_in_hours():
    out = _cap().run_tool("find_stored_ship", {"ship": "Anaconda"})
    assert "Colonia" in out
    assert "24 hours" in out                       # 86400s


def test_unknown_ship_lists_what_is_stored():
    out = _cap().run_tool("find_stored_ship", {"ship": "Sidewinder"})
    assert "don't see" in out.lower()
    assert "Imperial Cutter" in out                # honest fallback names real stored ships


def test_ships_overview_counts_here_and_elsewhere():
    out = _cap().run_tool("find_stored_ship", {})
    assert "6 ships in storage" in out
    assert "Imperial Cutter" in out
    assert "Sol" in out and "Colonia" in out


# --- modules ----------------------------------------------------------------------------------

def test_find_remote_module_speaks_transfer_and_copies():
    clip = _Clip()
    out = _cap(clip=clip).run_tool("find_stored_module", {"module": "power plant"})
    assert "Sol" in out
    assert "540,000 credits" in out                # journal TransferCost
    assert clip.copied == ["Sol"]


def test_find_module_here_needs_no_transfer():
    out = _cap().run_tool("find_stored_module", {"module": "fuel scoop"})
    assert "no transfer needed" in out.lower()


def test_duplicate_modules_summarized_and_single_system_copied():
    clip = _Clip()
    out = _cap(clip=clip).run_tool("find_stored_module", {"module": "multi-cannon"})
    assert "2 matching" in out
    assert "LHS 3447" in out
    assert clip.copied == ["LHS 3447"]             # both in one system -> handoff still offered


def test_module_alias_fsd_resolves_symbol():
    # "FSD" -> hyperdrive symbol; the only hyperdrive here is in transit.
    out = _cap().run_tool("find_stored_module", {"module": "FSD"})
    assert "in transit" in out.lower()


def test_unknown_module_lists_what_is_stored():
    out = _cap().run_tool("find_stored_module", {"module": "beam laser"})
    assert "don't see" in out.lower()


def test_modules_overview_groups_here_elsewhere_and_transit():
    out = _cap().run_tool("find_stored_module", {})
    assert "6 modules in storage" in out
    assert "in transit" in out.lower()
    assert "Sol" in out


# --- fail soft + registry contract ------------------------------------------------------------

def test_tool_error_is_spoken_not_raised():
    def boom():
        raise RuntimeError("kaboom")
    cap = StoredCapability(get_stored_ships=boom, get_stored_modules=lambda: None,
                           get_current_system=lambda: None, clipboard=_Clip())
    out = cap.run_tool("find_stored_ship", {})
    assert "error" in out.lower()


def test_help_meta_is_complete_and_registers():
    cap = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)                              # would raise on incomplete help metadata
    names = {t["name"] for t in reg.tools()}
    assert names == {"find_stored_ship", "find_stored_module"}


@pytest.mark.parametrize("tool", ["find_stored_ship", "find_stored_module"])
def test_freshness_clause_present_on_remote_answers(tool):
    arg = {"ship": "Corvette"} if tool == "find_stored_ship" else {"module": "power plant"}
    out = _cap().run_tool(tool, arg)
    assert "as of your last dock" in out.lower()
