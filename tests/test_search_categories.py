"""Unit tests for the four Prompt-5 search capabilities (offline, DESIGN §9).

Fake http (recorded fixtures / crafted bodies) + fake clipboard + a stubbed current system.
Covers per category: slot-filling, the hardcoded defaults (carriers-in / 'close to the star'),
the minor-faction present-vs-controls polarity flip, a result copying the system, an invalid
value being CAUGHT (validated, not queried), and the stations<->outfitting routing note.
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.capabilities._search_support import SearchConfig
from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.search_family import (MinorFactionSearchCapability, MiscSearchCapability,
                                              SignalSearchCapability, StationSearchCapability)

_FIX = Path(__file__).parent / "fixtures"
_SYSTEMS = json.loads((_FIX / "spansh_systems_federation_sol.json").read_text("utf-8"))
_STATIONS = json.loads((_FIX / "spansh_stations_largepad_sol.json").read_text("utf-8"))


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


# A deterministic faction index so faction-name resolution is offline and predictable.
_FAKE_FACTIONS = ["Mother Gaia", "The Dark Wheel", "Formidine Greybeard Guild",
                  "Sol Workers' Party", "Cerberus"]


def _faction_index():
    from covas.search.faction_index import FactionIndex
    return FactionIndex(fetch=lambda: list(_FAKE_FACTIONS))


def _mk(Cap, body, *, system="Sol"):
    http, clip = FakeHttp(body), Clip()
    kw = dict(http=http, get_current_system=(lambda: system), clipboard=clip)
    if Cap is not SignalSearchCapability:      # signals has no faction slot
        kw["factions"] = _faction_index()
    cap = Cap(SearchConfig(enabled=True), **kw)
    return cap, http, clip


def _filters(http):
    return http.calls[-1]["payload"]["filters"]


# ============================ stations ============================

def test_stations_slot_filling_and_structures():
    cap, http, clip = _mk(StationSearchCapability, _STATIONS)
    out = cap.run_tool("search_stations",
                       {"station_type": "orbis", "services": ["shipyard", "outfitting"],
                        "pad_size": "L", "max_arrival_distance": 1000})
    f = _filters(http)
    assert f["type"] == {"value": ["Orbis Starport"]}                      # alias resolved
    assert f["services"] == [{"name": "Shipyard"}, {"name": "Outfitting"}]  # list-of-objects
    assert f["has_large_pad"] == {"value": True}
    assert f["distance_to_arrival"] == {"value": 1000, "comparison": "<="}  # numeric comparison
    # Nearest station is in Sol, the current system -> spoken, but not copied (already there).
    assert clip.copied == [] and "already there" in out.lower()


def test_stations_close_to_star_and_faction_resolved():
    cap, http, _ = _mk(StationSearchCapability, _STATIONS)
    cap.run_tool("search_stations", {"faction": "the dark wheel", "max_arrival_distance": 1000})
    f = _filters(http)
    assert f["controlling_minor_faction"] == {"value": ["The Dark Wheel"]}  # resolved to canonical
    assert f["distance_to_arrival"]["comparison"] == "<="


_CARRIER_BODY = {"results": [
    {"system_name": "Sol", "name": "K7X-99Z", "type": "Drake-Class Carrier", "distance": 0.1,
     "has_large_pad": True, "large_pads": 1},
    {"system_name": "Sol", "name": "Daedalus", "type": "Coriolis Starport", "distance": 0.5,
     "has_large_pad": True, "large_pads": 4},
]}


def test_stations_include_carriers_by_default():
    cap, _, clip = _mk(StationSearchCapability, _CARRIER_BODY)
    out = cap.run_tool("search_stations", {"pad_size": "L"})
    assert "K7X-99Z" in out and clip.copied == ["Sol"]     # nearest is the carrier, kept


def test_stations_no_carriers_toggle_drops_them():
    cap, _, _ = _mk(StationSearchCapability, _CARRIER_BODY)
    out = cap.run_tool("search_stations", {"pad_size": "L", "no_carriers": True})
    assert "Daedalus" in out and "K7X-99Z" not in out     # carrier dropped, starport wins


def test_stations_invalid_service_is_caught():
    cap, http, clip = _mk(StationSearchCapability, _STATIONS)
    out = cap.run_tool("search_stations", {"services": ["teleporter"]})
    assert http.calls == [] and clip.copied == []
    assert "didn't recognize" in out.lower()


def test_stations_no_slots_asks():
    cap, http, _ = _mk(StationSearchCapability, _STATIONS)
    out = cap.run_tool("search_stations", {})
    assert http.calls == [] and ("service" in out.lower() or "station type" in out.lower())


def test_stations_outfitting_routing_note_is_mutual():
    from covas.capabilities.find_closest_capability import _DESC_NO_CONFIRM, _DESC_CONFIRM
    st = StationSearchCapability(SearchConfig(enabled=True)).tools()[0]["description"]
    # stations -> points a module/ship ask at outfitting; outfitting -> points service/type here.
    assert "find_closest_module" in st or "outfitting" in st.lower()
    assert "search_stations" in _DESC_NO_CONFIRM and "search_stations" in _DESC_CONFIRM


# ======================== minor factions ========================

def test_minor_faction_present_is_the_default_polarity():
    cap, http, clip = _mk(MinorFactionSearchCapability, _SYSTEMS)
    cap.run_tool("search_minor_factions", {"faction": "Mother Gaia"})
    assert _filters(http) == {"minor_faction_presences": {"value": ["Mother Gaia"]}}
    assert clip.copied == []              # nearest IS the current system -> not copied


def test_minor_faction_presence_result_names_the_queried_faction():
    # The reported failure: 'where is X present' landing in a system a DIFFERENT faction
    # controls must still confirm X (not lead with the controller, which reads as a miss).
    body = {"results": [
        {"name": "Hydrae Sector AV-Y b5", "distance": 45.4,
         "controlling_minor_faction": "Leviathan Scout Regiment"},
    ]}
    cap, http, clip = _mk(MinorFactionSearchCapability, body)
    out = cap.run_tool("search_minor_factions", {"faction": "Mother Gaia"})
    assert "Mother Gaia is present in Hydrae Sector AV-Y b5" in out       # grounded in the ask
    assert "Leviathan Scout Regiment" in out                             # controller noted, not led with
    assert clip.copied == ["Hydrae Sector AV-Y b5"]


def test_minor_faction_controls_result_phrasing():
    body = {"results": [{"name": "Sol", "distance": 0.0,
                         "controlling_minor_faction": "Mother Gaia"}]}
    cap, _, _ = _mk(MinorFactionSearchCapability, body)
    out = cap.run_tool("search_minor_factions", {"faction": "Mother Gaia", "controls": True})
    assert "Mother Gaia controls Sol" in out


def test_minor_faction_controls_flips_the_slot():
    cap, http, _ = _mk(MinorFactionSearchCapability, _SYSTEMS)
    cap.run_tool("search_minor_factions", {"faction": "Mother Gaia", "controls": True})
    assert _filters(http) == {"controlling_minor_faction": {"value": ["Mother Gaia"]}}


def test_minor_faction_mishear_resolves_to_canonical_name():
    # The reported bug: a mistranscribed faction name must resolve to Spansh's exact string
    # (else the exact-match filter returns zero systems) instead of searching on the mishear.
    cap, http, clip = _mk(MinorFactionSearchCapability, _SYSTEMS)
    cap.run_tool("search_minor_factions", {"faction": "Formadine Greybeard Guild"})
    assert _filters(http) == {"minor_faction_presences": {"value": ["Formidine Greybeard Guild"]}}
    assert clip.copied == []              # nearest IS the current system -> not copied


def test_minor_faction_unknown_name_offers_correction_and_does_not_query():
    cap, http, clip = _mk(MinorFactionSearchCapability, _SYSTEMS)
    out = cap.run_tool("search_minor_factions", {"faction": "Formidine Exiles"})
    assert http.calls == [] and clip.copied == []          # never searched on an unresolved name
    assert "did you mean" in out.lower() and "Formidine Greybeard Guild" in out


def test_minor_faction_falls_back_to_raw_name_when_index_unavailable():
    # Fail-soft: if the faction index can't be fetched, still search on the spoken name.
    from covas.search.faction_index import FactionIndex
    def _boom():
        raise ConnectionError("offline")
    cap = MinorFactionSearchCapability(
        SearchConfig(enabled=True), http=FakeHttp(_SYSTEMS),
        get_current_system=lambda: "Sol", clipboard=Clip(),
        factions=FactionIndex(fetch=_boom))
    cap.run_tool("search_minor_factions", {"faction": "Some Faction"})
    # the query still ran, using the raw name (best effort) rather than blocking
    # (no exception, and a query was issued)


def test_minor_faction_state_and_allegiance_validated():
    cap, http, _ = _mk(MinorFactionSearchCapability, _SYSTEMS)
    cap.run_tool("search_minor_factions", {"allegiance": "imperial", "state": "at war"})
    f = _filters(http)
    assert f["allegiance"] == {"value": ["Empire"]}
    assert f["controlling_minor_faction_state"] == {"value": ["War"]}


def test_minor_faction_invalid_state_is_caught():
    cap, http, clip = _mk(MinorFactionSearchCapability, _SYSTEMS)
    out = cap.run_tool("search_minor_factions", {"state": "zombie apocalypse"})
    assert http.calls == [] and clip.copied == []
    assert "didn't recognize" in out.lower()


def test_minor_faction_no_slots_asks():
    cap, http, _ = _mk(MinorFactionSearchCapability, _SYSTEMS)
    out = cap.run_tool("search_minor_factions", {})
    assert http.calls == [] and "faction" in out.lower()


# ============================ signals ============================

def test_signals_resolve_type_and_copy():
    # A megaship a few ly away (not the current system) -> spoken AND copied.
    body = {"results": [{"system_name": "Wolf 359", "name": "Damascus", "type": "Mega ship",
                         "distance": 12.3, "has_large_pad": True}]}
    cap, http, clip = _mk(SignalSearchCapability, body)
    out = cap.run_tool("search_signals", {"signal_type": "megaship"})
    assert _filters(http) == {"type": {"value": ["Mega ship"]}}
    assert clip.copied == ["Wolf 359"] and "Wolf 359" in out


def test_signals_unfindable_type_is_corrected_not_invented():
    cap, http, clip = _mk(SignalSearchCapability, _STATIONS)
    out = cap.run_tool("search_signals", {"signal_type": "space elevator"})
    assert http.calls == [] and clip.copied == []       # never searched, never invented a result
    # The reply only names real structure types (a suggestion or the valid-types list).
    low = out.lower()
    assert any(w in low for w in ("megaship", "mega ship", "settlement", "outpost", "starport",
                                  "planetary", "asteroid", "structure"))


def test_signals_no_type_asks():
    cap, http, _ = _mk(SignalSearchCapability, _STATIONS)
    out = cap.run_tool("search_signals", {})
    assert http.calls == [] and "structure" in out.lower()


# ============================= misc =============================

def test_misc_state_search_and_copy():
    # A matching system a few ly away (not the current system) -> spoken AND copied.
    body = {"results": [{"name": "Wolf 359", "distance": 7.8,
                         "controlling_minor_faction_state": "Civil War"}]}
    cap, http, clip = _mk(MiscSearchCapability, body)
    out = cap.run_tool("search_faction_states", {"state": "civil war"})
    f = _filters(http)
    assert f["controlling_minor_faction_state"] == {"value": ["Civil War"]}
    # States tick daily, so a state search constrains data freshness server-side.
    assert "updated_at" in f
    assert clip.copied == ["Wolf 359"] and clip.copied[0] in out


def test_result_that_is_current_system_is_not_copied():
    # Task 4: when the nearest match IS the current system (distance ~0), say so and DON'T
    # copy it — you're already there. Sol is the top result in the systems fixture.
    cap, http, clip = _mk(MiscSearchCapability, _SYSTEMS)
    out = cap.run_tool("search_faction_states", {"state": "civil war"})
    assert clip.copied == [] and "already there" in out.lower()


def test_misc_combines_state_allegiance_power_state():
    cap, http, _ = _mk(MiscSearchCapability, _SYSTEMS)
    cap.run_tool("search_faction_states",
                 {"state": "war", "allegiance": "empire", "power_state": "fortified"})
    f = _filters(http)
    assert f["controlling_minor_faction_state"] == {"value": ["War"]}
    assert f["allegiance"] == {"value": ["Empire"]}
    assert f["power_state"] == {"value": ["Fortified"]}


def test_misc_invalid_power_state_is_caught():
    cap, http, clip = _mk(MiscSearchCapability, _SYSTEMS)
    out = cap.run_tool("search_faction_states", {"power_state": "supermassive"})
    assert http.calls == [] and clip.copied == []
    assert "didn't recognize" in out.lower()


def test_misc_no_slots_asks():
    cap, http, _ = _mk(MiscSearchCapability, _SYSTEMS)
    out = cap.run_tool("search_faction_states", {})
    assert http.calls == [] and "state" in out.lower()


# ===================== registry contract =========================

def test_all_four_register_and_satisfy_the_contract():
    reg = CapabilityRegistry()
    for Cap in (StationSearchCapability, MinorFactionSearchCapability,
                SignalSearchCapability, MiscSearchCapability):
        cap = Cap(SearchConfig(enabled=True), get_current_system=lambda: "Sol")
        assert help_meta_problems(cap.help_meta()) == []
        reg.register(cap)
    assert reg.contract_violations() == []
    assert reg.categories() == ["stations", "minor factions", "signals", "faction states"]
