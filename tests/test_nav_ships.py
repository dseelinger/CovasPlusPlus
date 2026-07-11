"""Unit tests for the offline ship roster + resolve_ship() (DESIGN §9).

Pure, offline, free — no network. Locks in the exact-Spansh-name mapping (the ships filter is
case-sensitive exact-match, so a canonical-name drift here would silently return zero
stations), the genuine-family disambiguation, mishear handling, and the Unknown fallback.
"""
from __future__ import annotations

from covas.nav.ships import (AmbiguousShip, ResolvedShip, ROSTER, SHIP_NAMES, UnknownShip,
                            resolve_ship)


# --- exact / canonical -------------------------------------------------------------------

def test_exact_name_resolves_to_canonical_spansh_string():
    r = resolve_ship("Anaconda")
    assert isinstance(r, ResolvedShip)
    assert r.name == "Anaconda" and r.label == "Anaconda"


def test_canonical_names_are_the_exact_spansh_forms():
    """Guards the case-sensitive names the live filter requires (no 'Krait Mk II' spaces)."""
    assert "Krait MkII" in SHIP_NAMES
    assert "Cobra MkIII" in SHIP_NAMES and "Viper MkIII" in SHIP_NAMES
    assert "Type-9 Heavy" in SHIP_NAMES and "Fer-de-Lance" in SHIP_NAMES
    assert "Imperial Clipper" in SHIP_NAMES
    # nothing accidentally spelled the human way that Spansh rejects
    assert "Krait Mk II" not in SHIP_NAMES and "Anaconda Mk2" not in SHIP_NAMES


def test_roster_ids_and_names_unique():
    assert len({s.id for s in ROSTER}) == len(ROSTER)
    assert len({s.name for s in ROSTER}) == len(ROSTER)


# --- mishears / aliases ------------------------------------------------------------------

def test_mishears_and_short_names_resolve():
    assert resolve_ship("conda").name == "Anaconda"
    assert resolve_ship("fdl").name == "Fer-de-Lance"
    assert resolve_ship("fer de lance").name == "Fer-de-Lance"
    assert resolve_ship("clipper").name == "Imperial Clipper"       # ED parlance
    assert resolve_ship("cutter").name == "Imperial Cutter"
    assert resolve_ship("corvette").name == "Federal Corvette"
    assert resolve_ship("chieftain").name == "Alliance Chieftain"


def test_discriminated_family_members_resolve():
    assert resolve_ship("krait phantom").name == "Krait Phantom"
    assert resolve_ship("krait mk2").name == "Krait MkII"
    assert resolve_ship("cobra mk4").name == "Cobra MkIV"
    assert resolve_ship("type 9").name == "Type-9 Heavy"
    assert resolve_ship("type nine").name == "Type-9 Heavy"
    assert resolve_ship("asp explorer").name == "Asp Explorer"


# --- genuine families ASK, never guess ---------------------------------------------------

def test_bare_krait_is_ambiguous():
    r = resolve_ship("krait")
    assert isinstance(r, AmbiguousShip)
    assert set(r.candidates) == {"Krait MkII", "Krait Phantom"}


def test_bare_cobra_offers_all_marks():
    r = resolve_ship("cobra")
    assert isinstance(r, AmbiguousShip)
    assert r.candidates == ["Cobra MkIII", "Cobra MkIV", "Cobra MkV"]


def test_bare_viper_asp_diamondback_type_are_ambiguous():
    assert set(resolve_ship("viper").candidates) == {"Viper MkIII", "Viper MkIV"}
    assert set(resolve_ship("asp").candidates) == {"Asp Explorer", "Asp Scout"}
    assert set(resolve_ship("diamondback").candidates) == {"Diamondback Explorer",
                                                           "Diamondback Scout"}
    types = resolve_ship("type")
    assert isinstance(types, AmbiguousShip)
    assert types.candidates[0] == "Type-6 Transporter"
    assert "Type-9 Heavy" in types.candidates and len(types.candidates) == 6


# --- unknown -----------------------------------------------------------------------------

def test_unknown_offers_suggestions_not_invention():
    r = resolve_ship("flux capacitor")
    assert isinstance(r, UnknownShip)
    assert r.suggestions                                   # a few real ships, never invented
    assert all(s in SHIP_NAMES for s in r.suggestions)


def test_empty_query_is_unknown():
    r = resolve_ship("")
    assert isinstance(r, UnknownShip)
    assert r.suggestions


# --- live extras (newly-released hulls folded in) ----------------------------------------

def test_extra_names_make_a_new_hull_resolvable():
    """A hull the bundle doesn't know but Spansh does (from ShipIndex) resolves exactly, while
    the bundled roster keeps working."""
    extras = ("Fictional Destroyer", "Cobra MkVI")
    r = resolve_ship("Fictional Destroyer", extra_names=extras)
    assert isinstance(r, ResolvedShip) and r.name == "Fictional Destroyer"
    # unknown without the extras -> proving the extras are what enabled it
    assert isinstance(resolve_ship("Fictional Destroyer"), UnknownShip)
    # bundled resolution is unaffected by the presence of extras
    assert resolve_ship("anaconda", extra_names=extras).name == "Anaconda"


def test_extras_do_not_override_bundled_names_or_aliases():
    """An extra that duplicates a bundled name/alias must not shadow the curated spec."""
    r = resolve_ship("conda", extra_names=("Anaconda", "conda"))
    assert isinstance(r, ResolvedShip) and r.name == "Anaconda"
