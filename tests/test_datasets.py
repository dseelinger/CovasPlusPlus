"""Unit tests for the dataset-manifest reader (covas/nav/datasets.py; offline, DESIGN §9).

Focus on fail-soft parsing (issue #164): valid JSON of the WRONG shape (a top-level array, a
non-dict row, a non-numeric row_count) must degrade to an empty/partial result rather than raising
AttributeError/TypeError out of `load_manifest`.
"""
from __future__ import annotations

import json

import pytest

from covas.nav import datasets


@pytest.fixture
def manifest_at(tmp_path, monkeypatch):
    """Point load_manifest at a tmp manifest and clear its lru_cache around each use."""
    def _write(obj) -> None:
        p = tmp_path / "datasets_manifest.json"
        p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")
        monkeypatch.setattr(datasets, "_MANIFEST", p)
        datasets.load_manifest.cache_clear()
    yield _write
    datasets.load_manifest.cache_clear()


def test_well_formed_manifest_parses_sorted(manifest_at):
    manifest_at({
        "ship_specs": {"source": "coriolis", "source_ref": "abc", "generated_at": "2026-01-01",
                       "row_count": 42},
        "ship_roster": {"source": "edcd", "source_ref": "def", "generated_at": "2026-02-02",
                        "row_count": 7},
    })
    rows = datasets.load_manifest()
    assert [r.name for r in rows] == ["ship_roster", "ship_specs"]   # sorted by name
    assert rows[1].row_count == 42


def test_top_level_array_is_fail_soft_empty(manifest_at):
    # A JSON array has no .items(); the OLD code raised AttributeError outside the guard.
    manifest_at(["ship_specs", "ship_roster"])
    assert datasets.load_manifest() == ()


def test_top_level_scalar_is_fail_soft_empty(manifest_at):
    manifest_at(123)
    assert datasets.load_manifest() == ()


def test_non_dict_row_is_skipped_rest_survive(manifest_at):
    manifest_at({
        "good": {"source": "s", "source_ref": "r", "generated_at": "2026-01-01", "row_count": 3},
        "bad": "not-a-dict-row",   # skipped, not fatal
    })
    rows = datasets.load_manifest()
    assert [r.name for r in rows] == ["good"]


def test_non_numeric_row_count_coerces_to_zero(manifest_at):
    # int("lots") would raise ValueError outside the guard; now the row survives with row_count 0.
    manifest_at({
        "ship_specs": {"source": "s", "source_ref": "r", "generated_at": "2026-01-01",
                       "row_count": "lots"},
    })
    rows = datasets.load_manifest()
    assert len(rows) == 1 and rows[0].row_count == 0


def test_malformed_json_is_fail_soft_empty(manifest_at):
    manifest_at("{ this is not valid json")
    assert datasets.load_manifest() == ()


def test_missing_file_is_fail_soft_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(datasets, "_MANIFEST", tmp_path / "does_not_exist.json")
    datasets.load_manifest.cache_clear()
    try:
        assert datasets.load_manifest() == ()
    finally:
        datasets.load_manifest.cache_clear()
