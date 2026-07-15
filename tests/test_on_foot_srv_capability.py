"""Unit tests for the on-foot / SRV read-tool capability (#54, DESIGN §5, §9). Offline.

Covers the protocol conformance, tool advertisement, and the three read tools' answers from a
live EDContext — on-foot vitals, SRV hull/cargo, and exobiology sample progress.
"""
from __future__ import annotations

from covas.capabilities import Capability, CapabilityRegistry
from covas.capabilities.on_foot_srv_capability import (ON_FOOT_SRV_TOOLS,
                                                       OnFootSrvCapability)
from covas.ed import EDContext


def test_capability_satisfies_protocol():
    assert isinstance(OnFootSrvCapability(EDContext()), Capability)


def test_advertises_read_tools():
    cap = OnFootSrvCapability(EDContext())
    assert cap.tools() is ON_FOOT_SRV_TOOLS
    assert {t["name"] for t in cap.tools()} == {
        "on_foot_status", "srv_status", "bio_scan_progress"}


def test_registry_dispatches():
    ctx = EDContext()
    ctx.update(oxygen=0.8)
    reg = CapabilityRegistry([OnFootSrvCapability(ctx)])
    assert "80%" in reg.run_tool("on_foot_status", {})


# --- on_foot_status --------------------------------------------------------

def test_on_foot_status_reports_vitals_and_low_flag():
    ctx = EDContext()
    ctx.update(oxygen=0.15, health=1.0, temperature=300.0, gravity=0.17)
    out = OnFootSrvCapability(ctx).run_tool("on_foot_status", {})
    assert "15%" in out and "LOW" in out
    assert "100%" in out and "300K" in out and "0.17g" in out


def test_on_foot_status_unknown_before_telemetry():
    out = OnFootSrvCapability(EDContext()).run_tool("on_foot_status", {})
    assert "unknown" in out.lower()


# --- srv_status ------------------------------------------------------------

def test_srv_status_reports_hull_and_cargo():
    ctx = EDContext()
    ctx.update(srv_hull=0.2, cargo=4.0)
    out = OnFootSrvCapability(ctx).run_tool("srv_status", {})
    assert "20%" in out and "LOW" in out and "4t" in out


def test_srv_status_unknown_before_telemetry():
    out = OnFootSrvCapability(EDContext()).run_tool("srv_status", {})
    assert "unknown" in out.lower()


# --- bio_scan_progress -----------------------------------------------------

def test_bio_scan_progress_reports_remaining():
    ctx = EDContext()
    ctx.set_bio_scan("Bacterium", samples=2)
    out = OnFootSrvCapability(ctx).run_tool("bio_scan_progress", {})
    assert "Bacterium" in out and "2 of 3" in out and "1 more" in out


def test_bio_scan_progress_complete():
    ctx = EDContext()
    ctx.set_bio_scan("Bacterium", samples=3)
    out = OnFootSrvCapability(ctx).run_tool("bio_scan_progress", {})
    assert "complete" in out.lower()


def test_bio_scan_progress_none_in_progress():
    out = OnFootSrvCapability(EDContext()).run_tool("bio_scan_progress", {})
    assert "no exobiology sample" in out.lower()


def test_unknown_tool_is_soft_error():
    assert OnFootSrvCapability(EDContext()).run_tool("nope", {}) == "Unknown tool: nope"
