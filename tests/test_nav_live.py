"""Opt-in integration tests for the nav feature (DESIGN §9).

Excluded from the default run — these touch the real (free) Spansh API and the real Windows
clipboard. Run deliberately with:  pytest -m "integration and local"
"""
from __future__ import annotations

import sys

import pytest

from covas.nav import (RequestsHttp, copy, find_closest_module, find_closest_ship, resolve,
                       resolve_ship)
from covas.nav.clipboard import ClipboardError


@pytest.mark.integration
@pytest.mark.local
def test_live_spansh_finds_a_multicannon_near_sol():
    """One real Spansh query: nearest medium fixed multi-cannon from Sol. Proves the request
    shape + parsing against the live API (a canary if Spansh changes its response)."""
    r = resolve("Multi-Cannon", "medium", "fixed")
    result = find_closest_module(r, "Sol", RequestsHttp(), pad_size="L")
    assert result.system and result.station
    assert result.distance_ly >= 0.0
    assert result.pad in ("S", "M", "L")


@pytest.mark.integration
@pytest.mark.local
def test_live_spansh_finds_an_anaconda_near_sol():
    """One real Spansh query: nearest station selling an Anaconda from Sol. Proves the `ships`
    filter shape + parsing against the live API (a canary if Spansh changes its response)."""
    r = resolve_ship("Anaconda")
    result = find_closest_ship(r, "Sol", RequestsHttp(), pad_size="L")
    assert result.system and result.station
    assert result.distance_ly >= 0.0
    assert result.pad in ("S", "M", "L")
    assert result.extra.get("ship_price", 1) > 0            # price read from the ships list


@pytest.mark.integration
@pytest.mark.local
def test_live_ship_index_harvest_covers_the_bundle():
    """The live roster harvest returns a healthy ship list — the canary for a Frontier release:
    if this ever surfaces names the bundle is missing, add them to `nav/ships.py`."""
    from covas.nav.ship_index import fetch_ship_names
    from covas.nav.ships import SHIP_NAMES
    names = set(fetch_ship_names())
    assert len(names) >= 30                                 # a full hub shipyard lists the roster
    missing = set(SHIP_NAMES) - names
    # A couple of hulls may be transiently out of stock; flag a large gap as real drift.
    assert len(missing) <= 3, f"bundle names not seen live (roster drift?): {sorted(missing)}"


@pytest.mark.integration
@pytest.mark.local
@pytest.mark.skipif(sys.platform != "win32", reason="clip.exe is Windows-only")
def test_live_clipboard_roundtrip():
    """Copy a marker via clip.exe and read it back with PowerShell Get-Clipboard."""
    import subprocess
    marker = "COVAS_NAV_TEST_Sol"
    try:
        copy(marker)
    except ClipboardError as e:
        pytest.skip(f"clipboard unavailable: {e}")
    got = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    assert got == marker
