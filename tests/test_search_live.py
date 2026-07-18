"""Opt-in integration tests for the shared Spansh client (DESIGN §9).

Excluded from the default run — these hit the real (free) Spansh API. Run deliberately with:
    pytest -m "integration and local"

One live query per in-scope category proves the built payload is ACCEPTED by Spansh (a wrong
filter structure returns HTTP 400) and that the real response parses into typed records — the
canary if Spansh changes its request shape or field names. Each query is small (size=3) and
filtered near Sol, and it's fine for a filtered category to return zero rows: we assert the
request/parse round-trips, not that a particular system exists today.
"""
from __future__ import annotations

import pytest

from covas.search import (RequestsHttp, StationRecord, SystemRecord, build_query, category,
                          execute_search, parse_results)

pytestmark = [pytest.mark.integration, pytest.mark.local]

# category -> a valid, minimal slot set to exercise its query builder live.
_LIVE_SLOTS = {
    "stations": {"has_large_pad": "L", "services": ["Shipyard"], "distance_to_arrival": {"max": 1000}},
    "star_systems": {"allegiance": "Federation", "population": {"min": 1_000_000_000}},
    "minor_factions": {"minor_faction_presences": "Mother Gaia"},
    "signals": {"type": "Coriolis Starport"},
    "misc": {"controlling_minor_faction_state": "War"},
}
_RECORD = {"station": StationRecord, "system": SystemRecord}


@pytest.mark.parametrize("cat_key", sorted(_LIVE_SLOTS))
def test_live_query_per_category(cat_key):
    spec = category(cat_key)
    payload = build_query(spec, _LIVE_SLOTS[cat_key], "Sol", size=3)
    results = execute_search(spec.endpoint, payload, RequestsHttp(),
                             reference_system="Sol", subject=spec.subject,
                             lookup_name=spec.lookup_name)
    records = parse_results(spec, results)              # must not raise on a real response
    assert isinstance(records, list)
    for r in records:
        assert isinstance(r, _RECORD[spec.result_kind])
        assert r.distance_ly >= 0.0


def test_live_outfitting_still_round_trips():
    """Outfitting keeps its bespoke path (nav/closest.py) on top of the shared transport."""
    from covas.nav import RequestsHttp as NavHttp, find_closest_module, resolve
    r = resolve("Multi-Cannon", "medium", "fixed")
    result = find_closest_module(r, "Sol", NavHttp(), pad_size="L")
    assert result.system and result.station and result.pad in ("S", "M", "L")


def test_live_faction_index_resolves_a_mistranscription():
    """The reported-bug canary: a real faction name Whisper mangled ('Formadine' for
    'Formidine') must resolve to Spansh's exact string via the live faction index."""
    from covas.search.faction_index import FactionIndex
    idx = FactionIndex()
    assert idx.loaded                                    # fetched the canonical list
    assert idx.resolve("Formadine Greybeard Guild") == "Formidine Greybeard Guild"


def test_live_minor_faction_capability_finds_a_mistranscribed_faction():
    """End-to-end: the exact failure from the bug report now returns a real system."""
    from covas.capabilities._search_support import SearchConfig
    from covas.capabilities.search_family import MINOR_FACTIONS, SpecSearchCapability
    from covas.search.faction_index import FactionIndex
    copied: list[str] = []
    cap = SpecSearchCapability(
        MINOR_FACTIONS, SearchConfig(enabled=True), http=RequestsHttp(),
        get_current_system=lambda: "Sol", clipboard=copied.append, factions=FactionIndex())
    out = cap.run_tool("search_minor_factions", {"faction": "Formadine Greybeard Guild"})
    assert copied and copied[0] in out                   # found a system + copied it
    assert "couldn't find" not in out.lower()


def test_live_star_system_capability_round_trips():
    """The star-systems capability end-to-end against real Spansh: a spoken slot -> canonical
    value -> live query -> parsed result -> clipboard. The canary if Spansh's systems response
    or vocabulary shifts."""
    from covas.capabilities._search_support import SearchConfig
    from covas.capabilities.search_family import SystemSearchCapability
    copied: list[str] = []
    cap = SystemSearchCapability(
        SearchConfig(enabled=True), http=RequestsHttp(),
        get_current_system=lambda: "Sol", clipboard=copied.append)
    out = cap.run_tool("search_star_systems", {"allegiance": "imperial", "security": "High"})
    assert copied and copied[0] in out          # nearest system name spoken + copied
    assert "clipboard" in out.lower()
