"""Unit tests for the ship -> landing-pad-size table (issue #117), offline/pure."""
from __future__ import annotations

from covas.ed import ship_pad_size
from covas.ed.ships import ship_pad_size as ship_pad_size_direct


def test_large_pad_ships():
    assert ship_pad_size("anaconda") == "L"
    assert ship_pad_size("federation_corvette") == "L"
    assert ship_pad_size("cutter") == "L"
    assert ship_pad_size("type9") == "L"
    assert ship_pad_size("type9_military") == "L"       # Type-10 Defender
    assert ship_pad_size("belugaliner") == "L"


def test_medium_pad_ships():
    assert ship_pad_size("python") == "M"
    assert ship_pad_size("asp") == "M"                  # Asp Explorer
    assert ship_pad_size("krait_mkii") == "M"
    assert ship_pad_size("independant_trader") == "M"   # Keelback
    assert ship_pad_size("vulture") == "M"


def test_small_pad_ships():
    assert ship_pad_size("sidewinder") == "S"
    assert ship_pad_size("eagle") == "S"
    assert ship_pad_size("viper") == "S"                # Viper MkIII
    assert ship_pad_size("cobramkiii") == "S"
    assert ship_pad_size("empire_courier") == "S"       # Imperial Courier


def test_unknown_symbol_is_none():
    assert ship_pad_size("some_brand_new_hull") is None
    assert ship_pad_size(None) is None
    assert ship_pad_size("") is None


def test_case_insensitive_lookup():
    assert ship_pad_size("Anaconda") == "L"
    assert ship_pad_size("ANACONDA") == "L"
    assert ship_pad_size("  anaconda  ") == "L"


def test_exported_from_ed_package_matches_module():
    # covas.ed re-exports the same function (bootstrap/app wire the package-level name).
    assert ship_pad_size is ship_pad_size_direct
