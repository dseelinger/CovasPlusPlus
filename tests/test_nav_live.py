"""Opt-in integration tests for the nav feature (DESIGN §9).

Excluded from the default run — these touch the real (free) Spansh API and the real Windows
clipboard. Run deliberately with:  pytest -m "integration and local"
"""
from __future__ import annotations

import sys

import pytest

from covas.nav import RequestsHttp, copy, find_closest_module, resolve
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
