"""Unit tests for EDContext + the ED-context capability (DESIGN §5, §9).

Covers the thread-safe context object (update/snapshot/summary/fuel derivation) and the
capability's read tools + system_context(), plus that it satisfies the Capability protocol
and dispatches through the registry. All offline.
"""
from __future__ import annotations

from covas.capabilities import Capability, CapabilityRegistry
from covas.capabilities.ed_context_capability import ED_CONTEXT_TOOLS, EDContextCapability
from covas.ed import EDContext

# --- EDContext -------------------------------------------------------------

def test_update_rejects_unknown_field():
    ctx = EDContext()
    try:
        ctx.update(nonsense=1)
    except KeyError:
        pass
    else:
        raise AssertionError("update() should reject unknown fields (fail loud)")


def test_snapshot_includes_derived_fuel_pct():
    ctx = EDContext()
    ctx.update(fuel_main=16.0, fuel_capacity=32.0)
    assert ctx.snapshot()["fuel_pct"] == 50.0


def test_fuel_pct_none_without_capacity():
    ctx = EDContext()
    ctx.update(fuel_main=16.0)
    assert ctx.fuel_pct() is None
    assert ctx.snapshot()["fuel_pct"] is None


def test_fuel_pct_clamped():
    ctx = EDContext()
    ctx.update(fuel_main=40.0, fuel_capacity=32.0)   # scooped over nominal
    assert ctx.snapshot()["fuel_pct"] == 100.0


def test_summary_none_when_empty():
    assert EDContext().summary() is None


def test_summary_reads_naturally():
    ctx = EDContext()
    ctx.update(system="Sol", docked=True, station="Abraham Lincoln",
               ship="Anaconda", ship_name="Void Runner",
               fuel_main=24.0, fuel_capacity=32.0, cargo=12.0)
    text = ctx.summary()
    assert "Sol" in text and "Abraham Lincoln" in text
    assert "Anaconda" in text and "Void Runner" in text
    assert "75%" in text and "12t" in text


def test_summary_flags_supercruise():
    ctx = EDContext()
    ctx.update(system="Sol", supercruise=True)
    assert "supercruise" in ctx.summary()


# --- capability wiring -----------------------------------------------------

def test_capability_satisfies_protocol():
    assert isinstance(EDContextCapability(EDContext()), Capability)


def test_capability_advertises_read_tools():
    cap = EDContextCapability(EDContext())
    assert cap.tools() is ED_CONTEXT_TOOLS
    assert {t["name"] for t in cap.tools()} == {"where_am_i", "ship_status", "recent_events"}


def test_registry_dispatches_to_ed_capability():
    ctx = EDContext()
    ctx.update(system="Sol")
    reg = CapabilityRegistry([EDContextCapability(ctx)])
    assert "Sol" in reg.run_tool("where_am_i", {})


def test_system_context_flows_through_registry():
    ctx = EDContext()
    ctx.update(system="Sol", docked=True, station="Abraham Lincoln")
    reg = CapabilityRegistry([EDContextCapability(ctx)])
    assert "Sol" in reg.system_context()


def test_system_context_none_when_nothing_known():
    reg = CapabilityRegistry([EDContextCapability(EDContext())])
    assert reg.system_context() is None


# --- read tools ------------------------------------------------------------

def test_where_am_i_unknown_before_telemetry():
    cap = EDContextCapability(EDContext())
    assert "unknown" in cap.run_tool("where_am_i", {}).lower()


def test_where_am_i_docked():
    ctx = EDContext()
    ctx.update(system="Sol", docked=True, station="Abraham Lincoln")
    out = EDContextCapability(ctx).run_tool("where_am_i", {})
    assert "Sol" in out and "Abraham Lincoln" in out


def test_where_am_i_open_space():
    ctx = EDContext()
    ctx.update(system="Sol")
    out = EDContextCapability(ctx).run_tool("where_am_i", {})
    assert "open space" in out.lower()


def test_ship_status_reports_fuel_and_flags():
    ctx = EDContext()
    ctx.update(ship="Anaconda", ship_name="Void Runner", fuel_main=8.0,
               fuel_capacity=32.0, low_fuel=True, cargo=4.0, supercruise=True)
    out = EDContextCapability(ctx).run_tool("ship_status", {})
    assert "Anaconda" in out and "Void Runner" in out
    assert "25%" in out and "LOW" in out
    assert "4t" in out and "supercruise" in out


def test_ship_status_unknown_before_telemetry():
    out = EDContextCapability(EDContext()).run_tool("ship_status", {})
    assert "unknown" in out.lower()


def test_unknown_tool_is_soft_error():
    assert EDContextCapability(EDContext()).run_tool("nope", {}) == "Unknown tool: nope"


# --- recent-events feed ----------------------------------------------------

def test_recent_summary_none_when_empty():
    assert EDContext().recent_summary() is None


def test_record_and_recent_summary_with_times():
    ctx = EDContext()
    ctx.record("FSDJump", "Jumped to Sol", "2026-07-08T12:05:00Z")
    ctx.record("Docked", "Docked at Abraham Lincoln", "2026-07-08T12:07:00Z")
    out = ctx.recent_summary()
    assert out.startswith("Recent events:")
    assert "Jumped to Sol (12:05)" in out
    assert "Docked at Abraham Lincoln (12:07)" in out


def test_recent_is_bounded_and_ordered():
    ctx = EDContext(recent_maxlen=3)
    for i in range(5):
        ctx.record("Ping", f"event {i}")
    descs = [e["desc"] for e in ctx.recent()]
    assert descs == ["event 2", "event 3", "event 4"]     # oldest dropped, order kept


def test_recent_summary_without_timestamp():
    ctx = EDContext()
    ctx.record("Ping", "did a thing")
    assert ctx.recent_summary() == "Recent events: did a thing."


# --- context_block (what gets injected) ------------------------------------

def test_context_block_none_when_nothing_known():
    assert EDContext().context_block() is None


def test_context_block_includes_status_and_log():
    ctx = EDContext()
    ctx.update(system="Sol", docked=True, station="Abraham Lincoln")
    ctx.record("FSDJump", "Jumped to Sol", "2026-07-08T12:05:00Z")
    block = ctx.context_block(include_log=True)
    assert block.startswith("(Live game telemetry for reference")
    assert "Sol" in block and "Abraham Lincoln" in block
    assert "Jumped to Sol" in block
    assert block.endswith(")")


def test_context_block_omits_log_when_not_wanted():
    ctx = EDContext()
    ctx.update(system="Sol")
    ctx.record("FSDJump", "Jumped to Sol", "2026-07-08T12:05:00Z")
    block = ctx.context_block(include_log=False)
    assert "Sol" in block and "Recent events" not in block


def test_recent_events_tool():
    ctx = EDContext()
    ctx.record("MissionCompleted", "Completed mission: Deliver widgets", "2026-07-08T12:10:00Z")
    out = EDContextCapability(ctx).run_tool("recent_events", {"count": 5})
    assert "Deliver widgets" in out


def test_recent_events_tool_empty():
    out = EDContextCapability(EDContext()).run_tool("recent_events", {})
    assert "No recent events" in out
