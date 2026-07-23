"""Frozen-surface snapshot for the twelve search/nav capabilities (issue #111).

The search/nav family is being collapsed from twelve near-identical capability modules into a
spec-driven generic + a declarative table (issue #111). The one hard constraint is that the
LLM-visible and help-visible surface stays **byte-for-byte identical** through the refactor:
the model's tool schemas drive tool-choice behaviour (and prompt caching keys off the exact
`tools()` order/bytes), and the help subsystem projects `help_meta()` verbatim.

This test is that safety rail. It builds all twelve capabilities in the SAME order `bootstrap`
registers them and captures, per capability, the exact `tools()` JSON, `help_meta()` output, and
`help_vocabulary()` (an extra guard — the issue mandates the first two). It serialises them to a
canonical JSON string and compares against a committed golden. The golden was generated from the
PRE-collapse code and must not change: any drift in a tool name, description, input schema, help
category/slot, or recovery vocabulary fails here.

It runs in bare `pytest` — offline, no network (every seam is a dummy; `tools()`/`help_meta()`/
`help_vocabulary()` are pure, they never touch the injected http/clipboard).
"""
from __future__ import annotations

import dataclasses
import difflib
import json
from pathlib import Path

_GOLDEN = Path(__file__).parent / "fixtures" / "search_family_surface.golden.json"


class _NoHttp:
    """A stand-in for the Spansh poster. The surface methods never call it; this just keeps the
    constructors from building a real `RequestsHttp` (and proves nothing here does network I/O)."""


def _noop_copy(_text: str) -> None:
    pass


def _current_system() -> str:
    return "Sol"


def build_family_surface() -> list[dict]:
    """The twelve search/nav capabilities, in bootstrap registration order, each rendered to its
    frozen surface. This is the ONE place that tracks the collapse: as capabilities move into
    `search_family`, only the imports/constructors here change — the golden stays frozen, so a
    changed byte is a real surface regression, not test drift.

    Order mirrors `bootstrap.MANIFEST`: find_closest (nav) -> find_closest_ship (ship_nav) ->
    system_search -> [station, minor_faction, signal, misc] (build_searches) -> body ->
    route_plan -> neutron_plan -> riches_plan -> mining_helper. That order IS the `tools()` order
    the provider sees, so it is part of the frozen contract.
    """
    # Imports are local so a construction problem surfaces as a test error, not a collection error.
    from covas.capabilities._search_support import SearchConfig
    from covas.capabilities.find_closest_capability import (
        FindClosestCapability,
        FindClosestShipCapability,
        NavConfig,
    )
    from covas.capabilities.mining_helper_capability import (
        MiningHelperCapability,
        MiningHelperConfig,
    )
    from covas.capabilities.route_plan_capability import (
        NeutronPlanCapability,
        NeutronPlanConfig,
        RichesPlanCapability,
        RichesPlanConfig,
        RoutePlanCapability,
        RoutePlanConfig,
    )
    from covas.capabilities.search_family import (
        FACTION_STATES_CATEGORY,
        MINOR_FACTIONS,
        SIGNALS,
        STATIONS,
        BodySearchCapability,
        SpecSearchCapability,
        SystemSearchCapability,
    )

    http, clip = _NoHttp(), _noop_copy
    common = dict(http=http, get_current_system=_current_system, clipboard=clip)
    search_common = dict(http=http, get_current_system=_current_system, clipboard=clip)

    ordered: list[tuple[str, object]] = [
        ("find_closest_module", FindClosestCapability(NavConfig(), **common)),
        ("find_closest_ship", FindClosestShipCapability(NavConfig(), **common)),
        ("search_star_systems",
         SystemSearchCapability(SearchConfig(), **common)),
        ("search_stations", SpecSearchCapability(STATIONS, SearchConfig(), **search_common)),
        ("search_minor_factions",
         SpecSearchCapability(MINOR_FACTIONS, SearchConfig(), **search_common)),
        ("search_signals", SpecSearchCapability(SIGNALS, SearchConfig(), **search_common)),
        ("search_faction_states",
         SpecSearchCapability(FACTION_STATES_CATEGORY, SearchConfig(), **search_common)),
        ("search_bodies", BodySearchCapability(SearchConfig(), **common)),
        ("plan_trade_route", RoutePlanCapability(RoutePlanConfig(), **common)),
        ("plot_neutron_route", NeutronPlanCapability(NeutronPlanConfig(), **common)),
        ("plan_riches_route", RichesPlanCapability(RichesPlanConfig(), **common)),
        ("plan_mining_session", MiningHelperCapability(MiningHelperConfig(), **common)),
    ]

    surface: list[dict] = []
    for label, cap in ordered:
        meta_fn = getattr(cap, "help_meta", None)
        vocab_fn = getattr(cap, "help_vocabulary", None)
        surface.append({
            "capability": label,
            "tools": cap.tools(),
            "help_meta": dataclasses.asdict(meta_fn()) if meta_fn is not None else None,
            "help_vocabulary": vocab_fn() if vocab_fn is not None else None,
        })
    return surface


def serialize_surface(surface: list[dict]) -> str:
    """Canonical serialisation for byte-identity: 2-space indent, keys in construction order (NOT
    sorted, so a reordered schema key is also caught), non-ASCII escaped so the golden is pure
    ASCII and immune to platform encoding. json.dumps always emits '\\n' newlines."""
    return json.dumps(surface, indent=2, ensure_ascii=True, sort_keys=False)


# The frozen tool-name order (build order), asserted separately so a dropped/reordered capability
# fails with a clear message even before the big diff.
_EXPECTED_TOOL_ORDER = [
    "find_closest_module", "find_closest_ship", "search_star_systems", "search_stations",
    "search_minor_factions", "search_signals", "search_faction_states", "search_bodies",
    "plan_trade_route", "plot_neutron_route", "plan_riches_route", "plan_mining_session",
]


def test_family_registration_order_and_count() -> None:
    """Exactly twelve capabilities, each advertising its one tool, in the frozen build order —
    the order the provider sees and prompt caching keys off."""
    surface = build_family_surface()
    assert len(surface) == 12
    names = [t["name"] for entry in surface for t in entry["tools"]]
    assert names == _EXPECTED_TOOL_ORDER


def test_search_family_surface_byte_identical() -> None:
    """The whole family's `tools()` + `help_meta()` (+ `help_vocabulary()`) is byte-for-byte the
    committed golden. This is the frozen-surface contract for the issue #111 collapse."""
    actual = serialize_surface(build_family_surface())
    golden = _GOLDEN.read_text(encoding="utf-8").replace("\r\n", "\n")
    if actual != golden:
        diff = "\n".join(difflib.unified_diff(
            golden.splitlines(), actual.splitlines(),
            fromfile="golden", tofile="actual", lineterm=""))
        # Cap the diff so a wholesale mismatch stays readable in the failure output.
        snippet = "\n".join(diff.splitlines()[:60])
        raise AssertionError(
            "search/nav tool surface drifted from the frozen golden "
            f"({_GOLDEN.name}). First differences:\n{snippet}")
