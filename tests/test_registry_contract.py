"""Registry contract tests (Search Prompt 2) — offline, deterministic.

Two jobs:

  1. Make the "every capability carries COMPLETE help metadata" policy enforce itself
     structurally. `help_meta_problems` is the single definition of complete; this file
     asserts it flags an incomplete capability and passes a complete one, that the registry
     REFUSES to register an incomplete one, and — the guard every future category inherits —
     that the REAL registry the app builds carries no contract violations. Add a category
     with a slot missing its help_text and this suite goes red, instead of the omission
     living on in prose.
  2. The pure read helpers help consumes (categories / slots / examples / Spansh field
     mapping) return correct data over a fixture registry.

Everything is pure string/dataclass assembly over fakes — no network, no LLM, no audio.
"""
from __future__ import annotations

import pytest

from covas.capabilities.base import (CapabilityRegistry, HelpMeta, Slot,
                                     help_meta_problems, validate_help_meta)


# --- fakes -----------------------------------------------------------------

class _Cap:
    """A minimal capability carrying help metadata."""

    def __init__(self, category, *, one_liner="It does a thing.", example="do a thing",
                 group="", slots=(), tool=None):
        self._meta = HelpMeta(category=category, one_liner=one_liner, example=example,
                              group=group, slots=tuple(slots))
        self._tool = tool or f"tool_{category}"

    def tools(self):
        return [{"name": self._tool, "input_schema": {"type": "object", "properties": {}}}]

    def run_tool(self, name, inp):
        return f"{name} ran"

    def help_meta(self):
        return self._meta


def _outfitting_slots():
    return (
        Slot(param="module", phrasings=("the module", "the module name"),
             example="find a fuel scoop", help_text="Name the module you want."),
        Slot(param="size", phrasings=("a size", "a class"),
             example="a large one", help_text="Say the size for modules that come in several."),
    )


def _complete_cap():
    return _Cap("outfitting", one_liner="I find the closest station selling a module.",
                example="find the closest multi-cannon", slots=_outfitting_slots())


def _fixture_registry():
    """Two complete categories in a known registration order (no usage -> that order holds)."""
    reg = CapabilityRegistry()
    reg.register(_complete_cap())
    reg.register(_Cap("systems", one_liner="I search star systems.",
                      example="find a system with a high population", tool="t_systems"))
    return reg


# --- 1a. the contract flags incomplete metadata ----------------------------

def test_contract_flags_meta_missing_one_liner():
    problems = help_meta_problems(HelpMeta(category="x", one_liner="", example="say x"))
    assert any("one_liner" in p for p in problems)


def test_contract_flags_meta_missing_example():
    problems = help_meta_problems(HelpMeta(category="x", one_liner="does x", example=" "))
    assert any("example" in p for p in problems)


def test_contract_flags_slot_missing_help_text():
    meta = HelpMeta(category="x", one_liner="does x", example="say x",
                    slots=(Slot(param="p", phrasings=("a p",), example="an x", help_text=""),))
    problems = help_meta_problems(meta)
    assert any("help_text" in p or "slot #0" in p for p in problems)


def test_contract_flags_slot_with_no_phrasings():
    meta = HelpMeta(category="x", one_liner="does x", example="say x",
                    slots=(Slot(param="p", phrasings=(), example="an x", help_text="h"),))
    assert any("phrasings" in p for p in help_meta_problems(meta))


def test_contract_flags_non_help_meta_object():
    assert help_meta_problems("not a HelpMeta")  # non-empty -> flagged, no raise


# --- 1b. the contract passes complete metadata -----------------------------

def test_contract_passes_a_complete_capability():
    assert help_meta_problems(_complete_cap().help_meta()) == []


def test_validate_returns_a_complete_meta_unchanged():
    meta = _complete_cap().help_meta()
    assert validate_help_meta(meta) is meta


# --- 1c. the registry enforces it structurally -----------------------------

def test_registry_refuses_to_register_incomplete_capability():
    reg = CapabilityRegistry()
    with pytest.raises(ValueError):
        reg.register(_Cap("broken", one_liner=""))


def test_registry_refuses_capability_with_incomplete_slot():
    bad = _Cap("outfitting",
               slots=(Slot(param="module", phrasings=(), example="a scoop", help_text="h"),))
    with pytest.raises(ValueError):
        CapabilityRegistry().register(bad)


def test_contract_violations_empty_for_a_complete_registry():
    assert _fixture_registry().contract_violations() == []


# --- 1d. the guard every future category inherits: the REAL registry --------

def test_real_capabilities_satisfy_the_contract():
    # Build the actual help + outfitting capabilities the app registers (offline args) and
    # assert the registry carries no contract violations. A future category registered with
    # incomplete metadata breaks this (and register() would already have refused it).
    from covas.capabilities.help_capability import HelpCapability
    from covas.capabilities.find_closest_capability import (FindClosestCapability,
                                                            NavConfig)
    reg = CapabilityRegistry()
    reg.register(HelpCapability(reg))
    reg.register(FindClosestCapability(NavConfig(enabled=True),
                                       get_current_system=lambda: "Sol"))

    assert reg.contract_violations() == []
    # And every category it exposes is individually complete.
    for cat in reg.categories():
        assert help_meta_problems(reg.help_entry_for(cat)) == []


# --- 1e. group projection (the help hierarchy) -----------------------------

def _grouped_registry():
    reg = CapabilityRegistry()
    reg.register(_Cap("outfitting", group="navigation and search", tool="t_out"))
    reg.register(_Cap("stations", group="navigation and search", tool="t_sta"))
    reg.register(_Cap("settings", group="settings", tool="t_set"))
    reg.register(_Cap("carriers", tool="t_car"))          # ungrouped -> singleton group
    return reg


def test_groups_are_distinct_and_ordered():
    # navigation and search first (two members), then settings, then the ungrouped singleton
    # (falls back to its own category name).
    assert _grouped_registry().groups() == ["navigation and search", "settings", "carriers"]


def test_help_entries_in_group_returns_members():
    cats = [m.category for m in _grouped_registry().help_entries_in_group("navigation and search")]
    assert cats == ["outfitting", "stations"]


def test_help_entries_in_group_is_case_insensitive():
    reg = _grouped_registry()
    assert len(reg.help_entries_in_group("SETTINGS")) == 1


def test_help_entries_in_group_unknown_is_empty():
    assert _grouped_registry().help_entries_in_group("teleportation") == []


def test_group_for_resolves_canonical_name_or_none():
    reg = _grouped_registry()
    assert reg.group_for("navigation AND search") == "navigation and search"
    assert reg.group_for("nope") is None


def test_ungrouped_capability_is_its_own_group():
    assert _grouped_registry().help_entries_in_group("carriers")[0].category == "carriers"


def test_real_registry_groups_cover_every_capability():
    # Build the real app registry (offline) and assert every capability lands in a group and
    # every group resolves back — the guard that a new capability can't silently escape the
    # grouped "what can you do" overview.
    from covas.capabilities.help_capability import HelpCapability
    from covas.capabilities.find_closest_capability import (FindClosestCapability, NavConfig)
    reg = CapabilityRegistry()
    help_cap = HelpCapability(reg)
    reg.register(help_cap)
    reg.register(FindClosestCapability(NavConfig(enabled=True), get_current_system=lambda: "Sol"))
    groups = reg.groups(exclude=help_cap)
    assert groups                                   # at least one group
    # every listed capability belongs to exactly one resolvable group
    for cat in reg.categories(exclude=help_cap):
        meta = reg.help_entry_for(cat, exclude=help_cap)
        from covas.capabilities.base import group_of
        assert reg.group_for(group_of(meta), exclude=help_cap) is not None


# --- 2. read helpers over a fixture registry -------------------------------

def test_categories_lists_registered_categories_in_order():
    assert _fixture_registry().categories() == ["outfitting", "systems"]


def test_examples_pairs_each_category_with_its_example():
    assert _fixture_registry().examples() == [
        ("outfitting", "find the closest multi-cannon"),
        ("systems", "find a system with a high population"),
    ]


def test_slots_for_returns_declared_slots_in_order():
    slots = _fixture_registry().slots_for("outfitting")
    assert [s.param for s in slots] == ["module", "size"]
    assert slots[0].help_text == "Name the module you want."


def test_slots_for_is_case_insensitive():
    assert [s.param for s in _fixture_registry().slots_for("OUTFITTING")] == ["module", "size"]


def test_slots_for_unknown_category_is_empty():
    assert _fixture_registry().slots_for("teleportation") == ()


def test_slots_for_category_without_slots_is_empty():
    assert _fixture_registry().slots_for("systems") == ()


# --- 3. Spansh field mapping: slot.param is the Spansh parameter name -------

def test_spansh_params_for_maps_slots_to_param_names():
    assert _fixture_registry().spansh_params_for("outfitting") == ["module", "size"]


def test_spansh_params_for_unknown_category_is_empty():
    assert _fixture_registry().spansh_params_for("nope") == []


def test_slot_for_param_resolves_a_field_back_to_its_slot():
    slot = _fixture_registry().slot_for_param("outfitting", "SIZE")
    assert slot is not None and slot.param == "size"
    assert "a size" in slot.phrasings


def test_slot_for_param_unknown_field_is_none():
    assert _fixture_registry().slot_for_param("outfitting", "hyperdrive") is None
