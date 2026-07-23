"""Unit tests for the bundled Odyssey on-foot engineering data (#73; offline, DESIGN §9).

Exercises the resolvers (suit / weapon / modification / engineer), the shared grade-upgrade
recipe pattern, the modification->engineer join, and a few integrity assertions over the
COMMITTED tables so a broken edit is caught. No network.
"""
from __future__ import annotations

from covas.ed import odyssey_engineering as ody

# --- resolvers --------------------------------------------------------------------------

def test_find_suit_by_name_keyword_and_alias():
    assert ody.find_suit("Maverick").name == "Maverick Suit"
    assert ody.find_suit("upgrade my dominator").name == "Dominator Suit"
    assert ody.find_suit("exploration suit").name == "Artemis Suit"
    assert ody.find_suit("flight suit") is None          # Flight Suit isn't engineerable
    assert ody.find_suit("warp suit") is None             # never guesses


def test_find_weapon_by_name_alias_and_family_token():
    assert ody.find_weapon("Karma AR-50").name == "Karma AR-50"
    assert ody.find_weapon("the oppressor").name == "Manticore Oppressor"
    assert ody.find_weapon("tk aphelion").name == "TK Aphelion"
    assert ody.find_weapon("laser pistol").name == "TK Zenith"
    assert ody.find_weapon("banana blaster") is None


def test_find_modification_exact_and_fuzzy():
    assert ody.find_modification("Greater Range").name == "Greater Range"
    assert ody.find_modification("more backpack space").name == "Extra Backpack Capacity"
    assert ody.find_modification("clip size").name == "Magazine Size"      # alias
    assert ody.find_modification("recoil").name == "Stability"            # alias
    assert ody.find_modification("teleportation") is None


def test_find_engineer_by_token():
    assert ody.find_engineer("Domino Green").name == "Domino Green"
    assert ody.find_engineer("ferrari").name == "Hero Ferrari"
    assert ody.find_engineer("beck").name == "Wellington Beck"
    assert ody.find_engineer("Nobody McNobody") is None


# --- grade-upgrade recipe ---------------------------------------------------------------

def test_suit_grade_step_counts_follow_the_shared_pattern():
    mav = ody.find_suit("Maverick")
    g5 = mav.grade_step(5)
    mats = dict(g5.materials)
    # trio at 5, components at 12 for grade 5
    assert mats["Suit Schematic"] == 5
    assert mats["Health Monitor"] == 5
    assert mats["Manufacturing Instructions"] == 5
    assert mats["Carbon Fibre Plating"] == 12
    assert mats["Graphene"] == 12
    # grade 1 is the base — no recipe
    assert mav.grade_step(1) is None
    # grade 3 uses the mid counts (2 / 5)
    g3 = dict(mav.grade_step(3).materials)
    assert g3["Suit Schematic"] == 2 and g3["Carbon Fibre Plating"] == 5


def test_weapon_families_carry_the_right_class_materials():
    karma = dict(ody.find_weapon("Karma AR-50").grade_step(5).materials)
    assert "Compression-Liquefied Gas" in karma and "Tungsten Carbide" in karma
    tk = dict(ody.find_weapon("TK Zenith").grade_step(5).materials)
    assert "Ionised Gas" in tk and "Optical Fibre" in tk
    manti = dict(ody.find_weapon("Manticore Oppressor").grade_step(5).materials)
    assert "Chemical Superbase" in manti and "Microelectrode" in manti


def test_out_of_range_grade_returns_none():
    assert ody.find_suit("Artemis").grade_step(9) is None
    assert ody.find_weapon("TK Eclipse").grade_step(0) is None


# --- modification -> engineers join -----------------------------------------------------

def test_engineers_for_modification_bubble_first():
    engs = ody.engineers_for_modification("Greater Range")
    names = [e.name for e in engs]
    assert "Domino Green" in names and "Wellington Beck" in names  # bubble offerers
    assert "Rosa Dayette" in names                                  # colonia offerer
    # bubble engineers are listed before colonia ones
    regions = [e.region for e in engs]
    assert regions == sorted(regions, key=lambda r: 0 if r == "bubble" else 1)


def test_engineers_for_unknown_modification_is_empty():
    assert ody.engineers_for_modification("Time Travel") == []


# --- committed-table integrity ----------------------------------------------------------

def test_thirteen_engineers_with_journal_names_and_valid_referrals():
    assert len(ody.ENGINEERS) == 13
    names = {e.name for e in ody.ENGINEERS}
    for e in ody.ENGINEERS:
        assert e.system and e.settlement and e.access and e.unlock
        assert e.region in ("bubble", "colonia")
        # a referral target must be a real engineer in the table
        if e.refers_to is not None:
            assert e.refers_to in names


def test_every_offered_modification_exists_in_the_catalogue():
    catalogue = {m.name for m in ody.MODIFICATIONS}
    for e in ody.ENGINEERS:
        for m in (*e.suit_mods, *e.weapon_mods):
            assert m in catalogue, f"{e.name} offers unknown modification {m!r}"


def test_every_catalogue_modification_is_offered_by_someone():
    offered = set()
    for e in ody.ENGINEERS:
        offered.update(e.suit_mods)
        offered.update(e.weapon_mods)
    for m in ody.MODIFICATIONS:
        assert m.name in offered, f"catalogue modification {m.name!r} has no engineer"


def test_every_recipe_material_has_a_source_hint():
    for item in (*ody.SUITS, *ody.WEAPONS):
        for mat, _n in item.grade_step(5).materials:
            assert mat in ody.MATERIAL_SOURCES, f"no source hint for {mat!r}"
