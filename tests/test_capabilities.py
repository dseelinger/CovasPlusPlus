"""Unit tests for the capability registry + checklist capability (DESIGN §3.3).

Pure logic, offline. Covers registry tools() aggregation and run_tool() dispatch,
and the checklist capability's add/find/complete round trip against a temp file —
the same code path the LLM drives by voice.
"""
from __future__ import annotations

from covas.capabilities import Capability, CapabilityRegistry
from covas.capabilities.checklist_capability import (CHECKLIST_TOOLS,
                                                     ChecklistCapability)
from covas.checklist import Checklist


# --- a tiny stand-in capability for registry tests -------------------------

class _StubCap:
    def __init__(self, tool_names: list[str]) -> None:
        self._tools = [{"name": n, "input_schema": {"type": "object", "properties": {}}}
                       for n in tool_names]

    def tools(self) -> list[dict]:
        return self._tools

    def run_tool(self, name: str, inp: dict) -> str:
        return f"{name} ran"


def test_stub_satisfies_capability_protocol():
    assert isinstance(_StubCap(["a"]), Capability)


def test_registry_aggregates_tools_in_order():
    reg = CapabilityRegistry([_StubCap(["a", "b"]), _StubCap(["c"])])
    assert [t["name"] for t in reg.tools()] == ["a", "b", "c"]


def test_registry_dispatches_to_owning_capability():
    reg = CapabilityRegistry([_StubCap(["a"]), _StubCap(["b"])])
    assert reg.run_tool("b", {}) == "b ran"


def test_registry_unknown_tool_is_soft_error():
    reg = CapabilityRegistry([_StubCap(["a"])])
    assert reg.run_tool("nope", {}) == "Unknown tool: nope"


def test_registry_register_adds_capability():
    reg = CapabilityRegistry()
    reg.register(_StubCap(["x"]))
    assert reg.run_tool("x", {}) == "x ran"


def test_system_context_none_when_no_capability_provides_it():
    reg = CapabilityRegistry([_StubCap(["a"])])
    assert reg.system_context() is None


# --- checklist capability behaves like the old app._run_tool ---------------

def _cap(tmp_path) -> ChecklistCapability:
    f = tmp_path / "checklist.md"
    f.write_text("- [ ] Scoop fuel\n- [ ] Jump to Sol\n", encoding="utf-8")
    return ChecklistCapability(Checklist(str(f)))


def test_checklist_capability_advertises_the_tools(tmp_path):
    assert _cap(tmp_path).tools() is CHECKLIST_TOOLS


def test_checklist_add_find_complete_round_trip(tmp_path):
    cap = _cap(tmp_path)

    added = cap.run_tool("add_objective", {"text": "Buy limpets"})
    assert "Buy limpets" in added

    found = cap.run_tool("find_objectives", {"query": "limpets"})
    assert "Buy limpets" in found and "pending" in found

    done = cap.run_tool("set_objective", {"query": "limpets", "completed": True})
    assert "completed" in done

    # Marking it complete is visible on the model.
    items = {t: d for _n, d, t in cap.checklist.items()}
    assert items["Buy limpets"] is True


def test_checklist_get_next_returns_first_pending(tmp_path):
    cap = _cap(tmp_path)
    out = cap.run_tool("get_next_objectives", {})
    assert "Scoop fuel" in out


def test_checklist_tool_errors_are_caught(tmp_path):
    # A bad arg type must degrade to a soft "Tool error:" string, not raise.
    cap = _cap(tmp_path)
    out = cap.run_tool("set_objective", {"number": "not-a-number", "completed": True})
    assert out.startswith("Tool error:")


# --- next-objective anti-hallucination (fix/checklist-next-hallucination) ------------------

def _cap3(tmp_path) -> ChecklistCapability:
    """A three-item fixture so 'the next one' is a concrete, checkable line."""
    f = tmp_path / "checklist.md"
    f.write_text("- [ ] Scoop fuel\n- [ ] Jump to Sol\n- [ ] Sell cargo\n", encoding="utf-8")
    return ChecklistCapability(Checklist(str(f)))


def test_completing_item_returns_the_real_next_pending(tmp_path):
    """Completing an item hands back the ACTUAL next objective from the list, so the model
    relays truth instead of inventing a 'next up'."""
    cap = _cap3(tmp_path)
    out = cap.run_tool("set_objective", {"number": 1, "completed": True})
    assert "#1 'Scoop fuel' is now completed" in out
    # The real next line is #2 'Jump to Sol' — and it must be quoted verbatim from the file.
    assert "Jump to Sol" in out and "#2" in out
    _, _, next_text = cap.checklist.items()[1]
    assert next_text in out                      # reported next == checklist, not a guess
    assert "Sell cargo" not in out               # only the immediate next, not later items


def test_completing_middle_item_reports_next_still_pending(tmp_path):
    """With #1 already done, completing #2 surfaces #3 (first pending in file order)."""
    cap = _cap3(tmp_path)
    cap.run_tool("set_objective", {"number": 1, "completed": True})
    out = cap.run_tool("set_objective", {"number": 2, "completed": True})
    assert "Sell cargo" in out and "#3" in out


def test_completing_last_item_says_all_complete_no_invented_next(tmp_path):
    """Completing the final pending item reports all-complete — never a fabricated next."""
    cap = _cap3(tmp_path)
    for n in (1, 2, 3):
        out = cap.run_tool("set_objective", {"number": n, "completed": True})
    assert "all 3 objectives are complete" in out.lower()
    assert "next pending" not in out.lower()     # nothing invented after the last item


def test_reopening_item_does_not_append_a_next(tmp_path):
    """Reopening is not a completion, so the 'next pending' framing must not appear."""
    cap = _cap3(tmp_path)
    out = cap.run_tool("set_objective", {"number": 2, "completed": False})
    assert "reopened" in out
    assert "next pending" not in out.lower()
