"""Unit tests for the grounded ship-spec dataset + `ship_spec` tool (offline, DESIGN §9).

Three layers, all hermetic (no network, no journal, no clipboard):
  * dataset integrity — every canonical hull the resolver knows (bar the un-sourced Lynx)
    carries a spec, ids/names line up, and the derived numbers are self-consistent;
  * known-number spot checks — the recent hulls the issue calls out (Panther Clipper Mk II,
    Python Mk II, Type-8, Mandalay, Cobra Mk V, Corsair) plus a couple of classics resolve and
    report their real, bundled figures (the whole point: no training-cutoff hallucination);
  * capability dialog — resolved -> spec, ambiguous family -> ask which, unknown -> suggest,
    and a resolved-but-unsourced hull -> "no data, web search" instead of invented numbers.
"""
from __future__ import annotations

import covas.nav.ship_specs as ss
from covas.capabilities.ship_spec_capability import ShipSpecCapability
from covas.nav.ship_spec_data import SHIP_SPECS
from covas.nav.ships import ROSTER, _BY_ID


def _cap() -> ShipSpecCapability:
    return ShipSpecCapability()


def _run(cap: ShipSpecCapability, ship: str) -> str:
    return cap.run_tool("ship_spec", {"ship": ship})


# ---- dataset integrity --------------------------------------------------------------------

def test_every_roster_hull_has_a_spec_except_lynx():
    roster_ids = {s.id for s in ROSTER}
    spec_ids = set(SHIP_SPECS)
    # Lynx Highliner has no coriolis-data entry, so it is the only expected gap.
    assert roster_ids - spec_ids == {"lynx"}
    # No spec keyed to a hull the resolver doesn't know.
    assert spec_ids - roster_ids == set()


def test_spec_names_match_canonical_roster_names():
    # The bundled spec name must be the SAME string the resolver/roster use, so speech is
    # consistent app-wide (coriolis spells a few differently; the generator normalizes them).
    for sid, row in SHIP_SPECS.items():
        assert row["name"] == _BY_ID[sid].name


def test_get_spec_wraps_row_and_is_self_consistent():
    for sid in SHIP_SPECS:
        spec = ss.get_spec(sid)
        assert spec is not None and spec.id == sid
        assert len(spec.core) == 7                     # 7 core internals, fixed order
        assert spec.pad_size in (1, 2, 3)
        assert spec.fsd_size == spec.core[2]           # FSD is core slot #3
        # max_cargo is the sum of 2**size over cargo-capable optional slots (not military).
        expected = sum(2 ** sz for sz, kind in spec.optional if kind in ("", "cargo"))
        assert spec.max_cargo == expected


def test_get_spec_unknown_id_is_none():
    assert ss.get_spec("lynx") is None
    assert ss.get_spec("not_a_ship") is None


# ---- known numbers for recent + classic hulls ---------------------------------------------

def test_recent_hulls_report_real_bundled_numbers():
    # (id, manufacturer, pad_size, hull_mass, max_cargo, hardpoint_count, utilities)
    cases = [
        ("panther_clipper", "Zorgon Peterson", 3, 1200.0, 1046, 10, 6),
        ("python_mk2",      "Faulcon DeLacy",  2, 450.0,   96,   6,  6),
        ("type_8",          "Lakon",           2, 400.0,   406,  6,  4),
        ("mandalay",        "Zorgon Peterson", 2, 230.0,   154,  6,  4),
        ("cobra_mk5",       "Faulcon DeLacy",  1, 150.0,   110,  5,  4),
        ("corsair",         "Gutamaya",        2, 265.0,   318,  6,  4),
    ]
    for sid, manuf, pad, mass, cargo, n_hp, util in cases:
        spec = ss.get_spec(sid)
        assert spec is not None, sid
        assert spec.manufacturer == manuf
        assert spec.pad_size == pad
        assert spec.hull_mass == mass
        assert spec.max_cargo == cargo
        assert len(spec.hardpoints) == n_hp
        assert spec.utilities == util


def test_classic_hull_numbers():
    anaconda = ss.get_spec("anaconda")
    assert anaconda.manufacturer == "Faulcon DeLacy"
    assert anaconda.pad_size == 3 and anaconda.pad == "large"
    assert anaconda.hull_mass == 400.0
    hauler = ss.get_spec("hauler")
    assert hauler.pad_size == 1 and hauler.max_cargo == 26


# ---- capability dialog --------------------------------------------------------------------

def test_resolved_ship_returns_grounded_summary():
    out = _run(_cap(), "Type-8")
    assert "Type-8 Transporter" in out
    assert "Lakon" in out
    assert "406" in out          # max cargo, from the bundle
    assert "medium" in out       # medium landing pad


def test_recent_hull_resolves_from_nickname():
    # 'panther' -> Panther Clipper Mk II (an alias the roster carries); grounded numbers back.
    out = _run(_cap(), "panther")
    assert "Panther Clipper MkII" in out
    assert "Zorgon Peterson" in out
    assert "large" in out


def test_ambiguous_family_asks_which_and_gives_no_specs():
    out = _run(_cap(), "cobra")
    low = out.lower()
    assert "which" in low
    # It must NOT have answered with numbers for a family it was told to ask about.
    assert "hull mass" not in low
    assert "Cobra MkIII" in out and "Cobra MkV" in out


def test_unknown_ship_offers_suggestions_not_specs():
    out = _run(_cap(), "star destroyer")
    low = out.lower()
    assert "don't recognize" in low or "another way" in low
    assert "hull mass" not in low


def test_resolved_but_unsourced_hull_says_no_data_not_invented():
    # The Lynx Highliner resolves (it's in the roster) but has no bundled spec — the tool must
    # say so and offer web search, never confabulate numbers.
    out = _run(_cap(), "Lynx Highliner")
    low = out.lower()
    assert "no spec data" in low or "web-search" in low or "web search" in low
    assert "hull mass" not in low


def test_empty_ship_arg_asks_for_one():
    assert "which ship" in _run(_cap(), "  ").lower()


def test_unknown_tool_name_is_soft():
    assert "Unknown tool" in _cap().run_tool("not_a_tool", {})


def test_run_tool_is_fail_soft_on_bad_resolver():
    def boom(*a, **k):
        raise RuntimeError("resolver exploded")

    cap = ShipSpecCapability(resolve=boom)
    out = _run(cap, "Anaconda")
    assert "Ship spec error" in out          # spoken, not raised into the loop


def test_help_meta_is_complete():
    from covas.capabilities.base import help_meta_problems
    assert help_meta_problems(_cap().help_meta()) == []
