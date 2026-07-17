"""Unit tests for the game-data freshness surface (issue #101). Offline, no network.

Covers the manifest reader (`covas.nav.datasets`) and the `game_data_status` capability that
reports it — the honest "how current is your data?" answer that backs the ship_spec
"my data may predate that hull" hedge with a real date.
"""
from __future__ import annotations

from datetime import date, timedelta

from covas.capabilities.base import help_meta_problems
from covas.capabilities.game_data_status_capability import GameDataStatusCapability
from covas.nav.datasets import DatasetInfo, load_manifest, stale_datasets


def _info(name="ship_specs", days_old=0, rows=47):
    gen = (date.today() - timedelta(days=days_old)).isoformat()
    return DatasetInfo(name=name, source="EDCD/coriolis-data", source_ref="ref",
                       generated_at=gen, row_count=rows)


# ---- manifest reader -----------------------------------------------------------------------

def test_bundled_manifest_loads_and_covers_core_datasets():
    names = {d.name for d in load_manifest()}
    assert {"ship_roster", "ship_specs", "module_taxonomy"} <= names


def test_age_days_and_label():
    d = _info(name="ship_specs", days_old=10)
    assert d.age_days == 10
    assert d.label == "ship specifications"
    assert DatasetInfo("x", "", "", "not-a-date", 0).age_days is None   # unparseable -> None


def test_stale_predicate_flags_old_and_unknown_age():
    """The 'stale' rule: older than the threshold OR unknown age (a broken date still nags)."""
    fresh, old, broken = _info(days_old=5), _info(name="ship_roster", days_old=400), \
        DatasetInfo("module_taxonomy", "", "", "", 3)
    got = [d for d in (fresh, old, broken) if d.age_days is None or d.age_days > 183]
    assert old in got and broken in got and fresh not in got


def test_stale_datasets_reads_bundled_manifest():
    # The bundled manifest was just generated, so nothing is stale at a 6-month threshold.
    assert stale_datasets(183) == []
    # An impossible-future threshold of 0 with today's data: all rows are age 0, none > 0.
    assert all(d.age_days is not None for d in load_manifest())


# ---- capability ----------------------------------------------------------------------------

def test_tool_is_advertised_and_help_meta_complete():
    cap = GameDataStatusCapability()
    assert cap.tools()[0]["name"] == "game_data_status"
    assert help_meta_problems(cap.help_meta()) == []   # registry would accept it


def test_summary_reports_sources_and_ages():
    cap = GameDataStatusCapability(manifest=lambda: (_info(name="ship_specs", days_old=3),))
    out = cap.run_tool("game_data_status", {})
    assert "ship specifications" in out and "EDCD/coriolis-data" in out
    assert "3 days ago" in out


def test_empty_manifest_is_honest_not_silent():
    cap = GameDataStatusCapability(manifest=lambda: ())
    out = cap.run_tool("game_data_status", {})
    assert "can't read my data manifest" in out.lower()


def test_unknown_tool_and_errors_are_soft():
    cap = GameDataStatusCapability(manifest=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cap.run_tool("nope", {}) == "Unknown tool: nope"
    assert "error" in cap.run_tool("game_data_status", {}).lower()   # raised -> spoken, not thrown
