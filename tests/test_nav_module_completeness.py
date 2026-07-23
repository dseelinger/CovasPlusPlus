"""The taxonomy must recognise EVERY purchasable module, not a curated subset (offline, §9).

Regression guard for the "Advanced Docking Computer → Unknown" bug: the taxonomy is now
baked from EDCD/FDevIDs outfitting.csv, so these tests assert full coverage against that
committed source and that the specific modules the curated list skipped now resolve.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from covas.nav.modules import _ALIASES, TAXONOMY, Ambiguous, NeedAttrs, Resolved, Unknown, resolve

_CSV = Path(__file__).parent / "fixtures" / "fdevids_outfitting.csv"


def _edcd_rows() -> list[dict]:
    with _CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- the specific bug + the modules the curated subset skipped -----------------------------

@pytest.mark.parametrize("query, expected_name", [
    ("advanced docking computer", "Advanced Docking Computer"),  # the reported bug
    ("standard docking computer", "Standard Docking Computer"),
    ("docking computer", "Standard Docking Computer"),           # bare → the standard one
    ("supercruise assist", "Supercruise Assist"),
    ("guardian fsd booster", "Guardian FSD Booster"),
    ("xeno scanner", "Xeno Scanner"),
    ("caustic sink launcher", "Caustic Sink Launcher"),
])
def test_previously_unknown_modules_now_resolve(query, expected_name):
    r = resolve(query)
    # Resolved (single-size internals) or NeedAttrs (asks size) — but NEVER Unknown.
    assert not isinstance(r, Unknown), f"{query!r} still Unknown"
    name = getattr(r, "name", None) or getattr(r, "module", None)
    assert name == expected_name


# --- a sample from every category resolves (never Unknown) ---------------------------------

def test_a_sample_of_every_category_resolves():
    seen: dict[str, str] = {}
    for spec in TAXONOMY:
        seen.setdefault(spec.category, spec.name)
    assert set(seen) == {"weapon", "standard", "internal", "utility"}
    for category, name in seen.items():
        r = resolve(name)
        assert isinstance(r, (Resolved, NeedAttrs)), f"{category} sample {name!r} -> {r}"


# --- completeness: every EDCD module is represented ----------------------------------------

def test_every_edcd_name_is_in_taxonomy():
    """Every distinct module NAME in the canonical CSV has a taxonomy row (kept in sync by
    scripts/gen_module_taxonomy.py). Guards against silent drift / a stale generated table."""
    edcd_names = {r["name"] for r in _edcd_rows()}
    taxonomy_names = {s.name for s in TAXONOMY}
    missing = edcd_names - taxonomy_names
    assert not missing, f"{len(missing)} EDCD modules missing from taxonomy: {sorted(missing)[:10]}"


def test_every_edcd_symbol_maps_to_a_resolvable_module():
    """Every purchasable symbol's module resolves by its exact name — no symbol falls through
    to Unknown/Ambiguous."""
    names = {r["name"] for r in _edcd_rows()}
    bad = [n for n in names if isinstance(resolve(n), (Unknown, Ambiguous))]
    assert not bad, f"names not cleanly resolvable: {sorted(bad)[:10]}"


# --- the alias overlay must reference real modules (else aliases silently vanish) ----------

def test_no_dead_alias_keys():
    names = {s.name for s in TAXONOMY}
    dead = [k for k in _ALIASES if k not in names]
    assert not dead, f"_ALIASES keys with no matching module: {dead}"


# --- live extras (newly-released modules folded in, parity with ships) ----------------------

def test_extra_names_make_a_new_module_resolvable():
    """A module the bundle doesn't know but Spansh does (from ModuleIndex) resolves exactly by
    name — with no known size/mount, so it searches unqualified — while the bundle keeps working."""
    extras = ("Neutron Pulse Cannon", "Fictional Reactor")
    r = resolve("Neutron Pulse Cannon", extra_names=extras)
    assert isinstance(r, Resolved) and r.name == "Neutron Pulse Cannon"
    assert r.size is None and r.mount is None          # name-only search until the CSV catches up
    # bundled resolution is unaffected by the presence of extras
    assert resolve("advanced docking computer", extra_names=extras).name == "Advanced Docking Computer"


def test_extras_do_not_override_bundled_modules():
    """An extra that duplicates a bundled name/alias must not shadow the curated spec (which
    carries the real sizes/mounts)."""
    r = resolve("multi-cannon", "medium", "gimballed",
                extra_names=("Multi-Cannon", "multicannon"))
    assert isinstance(r, Resolved)
    assert r.name == "Multi-Cannon" and r.size == 2 and r.mount == "Gimbal"
