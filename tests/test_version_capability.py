"""Unit tests for the 'what version are you?' capability (I7; offline, DESIGN §9).

The tool is trivial by design — it reports the single-source-of-truth version string — so
what's worth pinning is: the reported version comes from `covas/__version__.py` (not a
hardcoded copy that could drift), the spoken line names it, the help metadata satisfies the
registry contract, and there is NO update-checking tool (updates are UI-only,
INSTALLER_DESIGN.md decision #5).
"""
from __future__ import annotations

from covas import __version__ as version_mod
from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.version_capability import VersionCapability

_TOOL = "report_version"


def test_reports_the_single_source_of_truth_version():
    # No injection: the default must read covas/__version__.py so it can't drift from the
    # string the build stamps.
    out = VersionCapability().run_tool(_TOOL, {})
    assert version_mod.__version__ in out


def test_spoken_line_names_the_injected_version():
    out = VersionCapability(version="9.9.9").run_tool(_TOOL, {})
    assert out == "I'm running COVAS++ version 9.9.9."


def test_logs_the_version_lookup():
    logged: list[str] = []
    VersionCapability(version="1.2.3", log=logged.append).run_tool(_TOOL, {})
    assert any("1.2.3" in m for m in logged)


def test_unknown_tool_is_soft():
    assert "Unknown tool" in VersionCapability().run_tool("upgrade_yourself", {})


def test_advertises_only_report_version_no_update_tool():
    # Updates stay UI-only — the capability must NOT expose an update-check/-install tool by
    # voice (INSTALLER_DESIGN.md decision #5).
    names = {t["name"] for t in VersionCapability().tools()}
    assert names == {_TOOL}
    assert not any("update" in n.lower() for n in names)


def test_help_metadata_is_complete_and_registers():
    cap = VersionCapability()
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)                                    # would raise on incomplete metadata
    assert "version" in reg.categories()
    assert reg.run_tool(_TOOL, {}).startswith("I'm running COVAS++ version")
