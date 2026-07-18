"""Voice-polish / refinement / error-help wiring (Search Prompt 6) — offline, DESIGN §9.

Covers the three Prompt-6 tasks at the unit level:
  1. a refinement RE-QUERIES (asserts the fake-http call count grows and the new payload carries
     the accumulated constraints) — it never filters a cached result set;
  2. the failure-recovery line echoes what WAS caught and offers a real correction, and the
     HelpCapability error mode recovers the new categories' vocabulary via the registry, never
     emitting the unresolved term as if it were valid;
  3. every search tool's description carries the verbal-cancel instruction (cancel is an
     LLM-recognized intent — the model drops the request and never calls the tool, so no query
     runs and nothing is copied).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from covas.capabilities._search_support import SearchConfig, recovery
from covas.capabilities.base import CapabilityRegistry
from covas.capabilities.help_capability import HelpCapability
from covas.capabilities.find_closest_capability import FindClosestCapability, NavConfig
from covas.capabilities.minor_faction_search_capability import MinorFactionSearchCapability
from covas.capabilities.misc_search_capability import MiscSearchCapability
from covas.capabilities.signal_search_capability import SignalSearchCapability
from covas.capabilities.station_search_capability import StationSearchCapability
from covas.capabilities.system_search_capability import SystemSearchCapability, SystemSearchConfig
from covas.search.factions import FACTION_STATES
from covas.search.stations import STATION_TYPES

_SYSTEMS = json.loads((Path(__file__).parent / "fixtures" /
                       "spansh_systems_federation_sol.json").read_text("utf-8"))
# BGS states tick daily, so run_query_fresh drops fixture rows older than BGS_MAX_AGE_DAYS and
# issues a second (stale-fallback) query — which would inflate the expected call count as the
# fixture's fixed dates age out. Stamp the rows fresh relative to now so the freshness path holds
# and each refinement stays a single query.
_NOW_STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")
for _r in _SYSTEMS.get("results", []):
    if "updated_at" in _r:
        _r["updated_at"] = _NOW_STAMP


class FakeHttp:
    def __init__(self, body) -> None:
        self._body = body
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "payload": payload})
        return 200, self._body


class Clip:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def __call__(self, text: str) -> None:
        self.copied.append(text)


def _faction_index():
    from covas.search.faction_index import FactionIndex
    return FactionIndex(fetch=lambda: ["Mother Gaia", "The Dark Wheel"])


# ============================ 1. refinement RE-QUERIES ============================

def test_refinement_requeries_and_accumulates_constraints():
    http, clip = FakeHttp(_SYSTEMS), Clip()
    cap = MiscSearchCapability(SearchConfig(enabled=True), http=http,
                               get_current_system=lambda: "Sol", clipboard=clip,
                               factions=_faction_index())
    # Turn 1: one constraint.
    cap.run_tool("search_faction_states", {"state": "war"})
    # Turn 2: the Commander adds another — the model re-calls with BOTH slots.
    cap.run_tool("search_faction_states", {"state": "war", "allegiance": "empire"})
    assert len(http.calls) == 2                                   # re-queried, not cache-filtered
    f2 = http.calls[1]["payload"]["filters"]
    assert f2["controlling_minor_faction_state"] == {"value": ["War"]}
    assert f2["allegiance"] == {"value": ["Empire"]}             # the added constraint is in the query


def test_every_refinement_hits_the_network_again():
    # Three refining calls -> three queries (a new constraint can change which result is nearest,
    # so a cached set can't be reused).
    http = FakeHttp(_SYSTEMS)
    cap = StationSearchCapability(SearchConfig(enabled=True), http=http,
                                  get_current_system=lambda: "Sol", clipboard=Clip(),
                                  factions=_faction_index())
    cap.run_tool("search_stations", {"services": ["shipyard"]})
    cap.run_tool("search_stations", {"services": ["shipyard"], "pad_size": "L"})
    cap.run_tool("search_stations", {"services": ["shipyard"], "pad_size": "L",
                                     "max_arrival_distance": 1000})
    assert len(http.calls) == 3


# ==================== 2a. recovery echoes what was caught =========================

def test_recovery_helper_echoes_caught_and_suggests():
    line = recovery("zombie", "faction state", "War", caught=["Empire allegiance"])
    assert "Empire allegiance" in line          # what was understood is not thrown away
    assert "War" in line and "zombie" in line   # names the miss + the real correction
    assert "did you mean" in line.lower()


def test_recovery_helper_without_caught_or_suggestion():
    line = recovery("blorp", "allegiance")
    assert line.startswith("I didn't recognize") and "blorp" in line


def test_capability_recovery_echoes_a_prior_valid_slot():
    # allegiance resolves, THEN state fails -> the reply keeps the allegiance and corrects state.
    http = FakeHttp(_SYSTEMS)
    cap = MinorFactionSearchCapability(SearchConfig(enabled=True), http=http,
                                       get_current_system=lambda: "Sol", clipboard=Clip(),
                                       factions=_faction_index())
    out = cap.run_tool("search_minor_factions", {"allegiance": "empire", "state": "zombie"})
    assert http.calls == []                     # a single bad slot blocks the query
    assert "Empire" in out                      # but the caught allegiance is echoed back
    assert "faction state" in out.lower()


# =============== 2b. HelpCapability error mode covers the new vocab ===============

def _registry_with_search():
    reg = CapabilityRegistry()
    reg.register(HelpCapability(reg))
    reg.register(MinorFactionSearchCapability(SearchConfig(enabled=True),
                                              get_current_system=lambda: "Sol",
                                              factions=_faction_index()))
    reg.register(SignalSearchCapability(SearchConfig(enabled=True),
                                        get_current_system=lambda: "Sol"))
    return reg


def test_help_error_mode_recovers_a_faction_state():
    reg = _registry_with_search()
    help_cap = reg._caps[0]                      # the HelpCapability registered first
    out = help_cap.run_tool("help", {"unresolved": "warr", "expected": "faction state"})
    assert "War" in out                          # suggested a real faction state from the registry
    assert any(s in out for s in FACTION_STATES)


def test_help_error_mode_recovers_a_structure_type():
    reg = _registry_with_search()
    help_cap = reg._caps[0]
    out = help_cap.run_tool("help", {"unresolved": "megaship", "expected": "structure type"})
    assert "Mega ship" in out and any(t in out for t in STATION_TYPES)


def test_help_error_mode_never_emits_the_unresolved_term_as_valid():
    reg = _registry_with_search()
    help_cap = reg._caps[0]
    out = help_cap.run_tool("help", {"unresolved": "teleporter", "expected": "structure type"})
    # It may quote 'teleporter' as UNrecognized, but must never present it as a real value:
    # the only capitalized real values it names come from the vocabulary.
    assert "teleporter" not in out or "didn't recognize" in out.lower() or \
           "couldn't" in out.lower()


# ======================= 3. verbal-cancel discipline =============================

def test_all_search_tools_carry_the_cancel_instruction():
    caps = [
        FindClosestCapability(NavConfig(enabled=True), get_current_system=lambda: "Sol"),
        SystemSearchCapability(SystemSearchConfig(enabled=True), get_current_system=lambda: "Sol"),
        StationSearchCapability(SearchConfig(enabled=True), get_current_system=lambda: "Sol"),
        MinorFactionSearchCapability(SearchConfig(enabled=True), get_current_system=lambda: "Sol"),
        SignalSearchCapability(SearchConfig(enabled=True), get_current_system=lambda: "Sol"),
        MiscSearchCapability(SearchConfig(enabled=True), get_current_system=lambda: "Sol"),
    ]
    for cap in caps:
        desc = cap.tools()[0]["description"].lower()
        assert "cancel" in desc or "never mind" in desc, f"{type(cap).__name__} lacks cancel note"
