"""Unit tests for the bundled engineering library (#66; offline, DESIGN §9).

Exercises blueprint resolution (name vs module-only), grade parsing, and the shortfall math
against a small hand-built inventory — plus a couple of assertions that the COMMITTED bundled
tables load and carry the expected data, so a broken regeneration is caught. No network.
"""
from __future__ import annotations

from types import MappingProxyType

from covas.ed.blueprints import BlueprintLibrary, cap_for_grade, parse_grade
from covas.ed.materials import MaterialsSnapshot

# A tiny, self-contained library so the resolver/shortfall logic is tested independent of the
# (separately asserted) bundled data.
_MATS = {
    "iron": {"name": "Iron", "category": "Raw", "grade": 1, "source": "src-iron"},
    "arsenic": {"name": "Arsenic", "category": "Raw", "grade": 2, "source": "src-arsenic"},
    "chemicalmanipulators": {"name": "Chemical Manipulators", "category": "Manufactured",
                             "grade": 4, "source": "src-chem"},
}
_BPS = {
    "FSD_LongRange": {"name": "Increased range", "module": "Frame shift drive",
                      "aliases": ["Frame shift drive", "FSD"],
                      "grades": {"1": [{"m": "iron", "n": 1}],
                                 "5": [{"m": "arsenic", "n": 1},
                                       {"m": "chemicalmanipulators", "n": 5}]}},
    "FSD_FastBoot": {"name": "Faster boot sequence", "module": "Frame shift drive",
                     "aliases": ["Frame shift drive", "FSD"],
                     "grades": {"5": [{"m": "iron", "n": 8}]}},
    "Engine_Dirty": {"name": "Dirty", "module": "Thrusters",
                     "aliases": ["Thrusters", "Engines"],
                     "grades": {"5": [{"m": "arsenic", "n": 1}]}},
}


def _lib() -> BlueprintLibrary:
    return BlueprintLibrary(_BPS, _MATS)


def _inv(**counts) -> MaterialsSnapshot:
    return MaterialsSnapshot(counts=MappingProxyType(dict(counts)))


# --- grade parsing ---------------------------------------------------------------------------

def test_parse_grade_reads_digits_words_and_defaults():
    assert parse_grade("grade 5 FSD") == 5
    assert parse_grade("g3 dirty drive") == 3
    assert parse_grade("grade five power plant") == 5
    assert parse_grade("increased range") == 5          # default
    assert parse_grade("increased range", default=1) == 1
    assert parse_grade("grade 9 nonsense") == 5          # out-of-range digit ignored -> default


# --- resolution ------------------------------------------------------------------------------

def test_named_blueprint_resolves_uniquely():
    top = _lib().resolve("increased range")[0]
    assert top.key == "FSD_LongRange"


def test_module_only_request_ties_its_blueprints():
    scored = _lib().resolve_scored("grade 5 FSD")
    top = scored[0][0]
    tied = [bp.key for s, bp in scored if s == top]
    assert set(tied) == {"FSD_LongRange", "FSD_FastBoot"}   # ambiguous -> caller disambiguates


def test_blueprints_for_module_lists_by_alias():
    keys = {bp.key for bp in _lib().blueprints_for_module("thrusters")}
    assert keys == {"Engine_Dirty"}


def test_unknown_request_returns_empty():
    assert _lib().resolve("flux capacitor") == []


# --- shortfall math --------------------------------------------------------------------------

def test_line_items_compute_missing_against_inventory():
    lib = _lib()
    bp = lib.blueprint("FSD_LongRange")
    inv = _inv(arsenic=12)          # have arsenic, none of the manipulators
    items = lib.line_items(bp, 5, inv)
    by_sym = {li.info.symbol: li for li in items}
    assert by_sym["arsenic"].missing is False
    assert by_sym["chemicalmanipulators"].missing is True
    assert by_sym["chemicalmanipulators"].need == 5
    assert by_sym["chemicalmanipulators"].have == 0
    assert by_sym["chemicalmanipulators"].short == 5
    assert by_sym["chemicalmanipulators"].info.source == "src-chem"


def test_line_items_with_no_snapshot_reads_everything_missing():
    lib = _lib()
    items = lib.line_items(lib.blueprint("FSD_LongRange"), 5, None)
    assert all(li.have == 0 and li.missing for li in items)


def test_recipe_falls_back_to_nearest_lower_grade():
    # FSD_FastBoot only defines grade 5; asking for 3 falls back to its grade-5 recipe.
    bp = _lib().blueprint("FSD_FastBoot")
    assert bp.recipe(3) == (("iron", 8),)
    assert bp.max_grade == 5


# --- material resolution + grade caps (#132) -------------------------------------------------

def test_resolve_material_matches_by_name_and_symbol():
    lib = _lib()
    assert lib.resolve_material("chemical manipulators").symbol == "chemicalmanipulators"
    assert lib.resolve_material("arsenic").symbol == "arsenic"
    assert lib.resolve_material("iron").symbol == "iron"


def test_resolve_material_no_match_returns_none():
    assert _lib().resolve_material("flux capacitor") is None


def test_materials_by_category_filters_and_sorts_by_grade_then_name():
    lib = _lib()
    raws = lib.materials_by_category("raw")            # case-insensitive
    assert [m.symbol for m in raws] == ["iron", "arsenic"]   # grade 1 before grade 2
    assert [m.symbol for m in lib.materials_by_category("Manufactured")] == ["chemicalmanipulators"]
    assert lib.materials_by_category("encoded") == []        # none in this tiny fixture


def test_cap_for_grade_is_the_fixed_ed_table():
    assert cap_for_grade(1) == 300
    assert cap_for_grade(5) == 100
    assert cap_for_grade(0) is None
    assert cap_for_grade(None) is None


# --- committed bundled data ------------------------------------------------------------------

def test_bundled_tables_load_and_carry_fsd_recipe():
    lib = BlueprintLibrary.from_bundled()
    bp = lib.blueprint("FSD_LongRange")
    assert bp is not None and bp.name.lower() == "increased range"
    syms = {sym for sym, _n in bp.recipe(5)}
    assert {"arsenic", "chemicalmanipulators", "dataminedwake"} == syms
    # every recipe material resolves to real metadata (no orphan symbols)
    for sym in syms:
        info = lib.material(sym)
        assert info is not None and info.category in {"Raw", "Manufactured", "Encoded"}
        assert info.source
