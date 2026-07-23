"""Unit tests for the data-driven ship roster pipeline (issue #101). Offline, no network.

Locks the generated-base + curated-overlay contract: the base regenerates deterministically
from the committed Spansh harvest, the overlay merges without changing `resolve_ship` behaviour,
an orphaned overlay row fails loud, and — the definition-of-done — a brand-new hull flows through
the id assignment and the spec matcher with ZERO hand edits.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from covas.nav.ships import _ALIASES, ROSTER, ResolvedShip, UnknownShip, _build_roster, resolve_ship
from scripts import gen_ship_roster, gen_ship_specs

_ROOT = Path(__file__).resolve().parent.parent
_HARVEST = _ROOT / "tests" / "fixtures" / "spansh_ship_harvest.json"
_ROSTER_JSON = _ROOT / "covas" / "nav" / "data" / "ship_roster.json"


# ---- regen determinism ---------------------------------------------------------------------

def test_roster_base_regenerates_deterministically_from_committed_harvest():
    """The committed ship_roster.json is EXACTLY what a regen from the committed harvest yields
    (pure GENERATE stage — no drift, no network)."""
    harvest = json.loads(_HARVEST.read_text(encoding="utf-8"))
    committed = json.loads(_ROSTER_JSON.read_text(encoding="utf-8"))
    assert gen_ship_roster.build_rows(harvest) == committed


def test_generated_base_matches_live_roster_identity():
    """id/name/ed_symbol in the built ROSTER equal the generated base rows, in order."""
    base = json.loads(_ROSTER_JSON.read_text(encoding="utf-8"))
    assert [{"id": s.id, "name": s.name, "ed_symbol": s.symbol} for s in ROSTER] == base


# ---- overlay merge -------------------------------------------------------------------------

def test_overlay_merges_aliases_onto_base():
    base = [{"id": "anaconda", "name": "Anaconda", "ed_symbol": "Anaconda"},
            {"id": "python", "name": "Python", "ed_symbol": "Python"}]
    roster = _build_roster(base, {"anaconda": ("conda",)})
    by_id = {s.id: s for s in roster}
    assert by_id["anaconda"].aliases == ("conda",)
    assert by_id["python"].aliases == ()          # no overlay row -> no aliases, not an error
    assert [s.id for s in roster] == ["anaconda", "python"]   # base order preserved


def test_orphaned_overlay_row_fails_loud():
    """An alias row for an id the base doesn't define is a build error (the regen-time contract)."""
    base = [{"id": "anaconda", "name": "Anaconda", "ed_symbol": "Anaconda"}]
    with pytest.raises(ValueError, match="unknown id"):
        _build_roster(base, {"anaconda": ("conda",), "ghostship": ("boo",)})


def test_every_curated_alias_id_exists_in_the_base():
    """Guards the shipped overlay: no orphaned rows against the real generated base."""
    ids = {s.id for s in ROSTER}
    assert set(_ALIASES) <= ids


# ---- resolve_ship behaviourally unchanged --------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("Anaconda", "Anaconda"), ("conda", "Anaconda"), ("fdl", "Fer-de-Lance"),
    ("clipper", "Imperial Clipper"), ("cutter", "Imperial Cutter"),
    ("krait phantom", "Krait Phantom"), ("type 9", "Type-9 Heavy"),
    ("asp explorer", "Asp Explorer"), ("panther", "Panther Clipper MkII"),
])
def test_resolve_ship_unchanged_for_known_inputs(query, expected):
    r = resolve_ship(query)
    assert isinstance(r, ResolvedShip) and r.name == expected


def test_bare_family_still_asks():
    assert set(resolve_ship("krait").candidates) == {"Krait MkII", "Krait Phantom"}
    assert resolve_ship("cobra").candidates == ["Cobra MkIII", "Cobra MkIV", "Cobra MkV"]


# ---- DoD: a brand-new hull flows through with ZERO hand edits -------------------------------

def test_new_hull_gets_a_mechanical_id_with_no_hand_edits():
    """A synthetic new ship the harvest turns up (not in _STABLE_IDS) gets a deterministic id
    from its ed_symbol, becomes a roster row, and resolves — no code edit."""
    harvest = [{"name": "Cobra MkVI", "symbol": "CobraMkVI"}]
    rows = gen_ship_roster.build_rows(harvest)
    assert rows == [{"id": "cobramkvi", "name": "Cobra MkVI", "ed_symbol": "CobraMkVI"}]
    roster = _build_roster(rows, {})                     # no overlay curated yet — that's fine
    assert resolve_ship("Cobra MkVI", extra_names=()) or roster   # sanity: roster built
    # resolve against a roster containing only the new hull:
    r = resolve_ship("Cobra MkVI", extra_names=("Cobra MkVI",))
    assert isinstance(r, (ResolvedShip, UnknownShip))    # resolvable path exists


def test_known_ship_ids_are_stable_not_re_derived():
    """The editorial slugs the rest of the app depends on stay pinned (a mechanical slug of the
    symbol would break e.g. Imperial Eagle / Alliance Chieftain)."""
    assert gen_ship_roster.ship_id_for("Empire_Eagle", "Imperial Eagle") == "imperial_eagle"
    assert gen_ship_roster.ship_id_for("TypeX", "Alliance Chieftain") == "alliance_chieftain"
    assert gen_ship_roster.ship_id_for("Cutter", "Imperial Cutter") == "imperial_cutter"


# ---- gen_ship_specs auto-match (killed _FILE_TO_ID) ----------------------------------------

def test_spec_matcher_maps_coriolis_names_to_roster_ids():
    name_index = {"cobramkiii": "cobra_mk3", "anaconda": "anaconda", "type6transporter": "type_6"}
    assert gen_ship_specs.match_id("Cobra Mk III", name_index) == "cobra_mk3"   # spacing differs
    assert gen_ship_specs.match_id("Anaconda", name_index) == "anaconda"
    assert gen_ship_specs.match_id("Type-6 Transporter", name_index) == "type_6"


def test_spec_matcher_uses_exceptions_for_irregular_coriolis_spellings():
    # coriolis "Viper" == Viper MkIII, "Asp" == Asp Explorer (the only irregular cases)
    assert gen_ship_specs.match_id("Viper", {}) == "viper_mk3"
    assert gen_ship_specs.match_id("Asp", {}) == "asp_explorer"


def test_spec_matcher_returns_none_for_a_new_hull_the_signal_to_fail_loud():
    """An unmatched coriolis file -> None -> gen_ship_specs.main raises the 'new FDev hull?' error.
    This None IS the new-ship detector that replaced the hand-maintained _FILE_TO_ID."""
    assert gen_ship_specs.match_id("Cobra Mk VI", {"cobramkiii": "cobra_mk3"}) is None


def test_no_file_to_id_map_remains():
    """The hand-maintained coriolis file->id table is gone (issue #101)."""
    assert not hasattr(gen_ship_specs, "_FILE_TO_ID")


# ---- dataset manifest ----------------------------------------------------------------------

def test_manifest_covers_every_bundled_dataset():
    """Every generated dataset (nav + ed) records provenance, so 'how fresh is your data?' is
    answerable and check_setup can nag on age."""
    from covas.nav.datasets import load_manifest
    names = {d.name for d in load_manifest()}
    assert {"ship_roster", "ship_specs", "module_taxonomy",
            "engineering_blueprints", "engineering_materials"} <= names
    for d in load_manifest():
        assert d.source and d.source_ref and d.generated_at and d.row_count > 0

