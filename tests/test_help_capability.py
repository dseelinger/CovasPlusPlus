"""Unit tests for the help subsystem (Search Prompt 1) — offline, deterministic.

Covers the registry help contract and all three HelpCapability modes:
  * the registry rejects incomplete help metadata at registration;
  * idle "what can you do" — empty-state, the 3-plus-tail cap, usage ranking;
  * topic detail + the unregistered-topic fallback (never echoes the bad name);
  * failure-recovery — the nearest VALID phrasing, validated against the registry;
  * deterministic rotation across a fixed call sequence (no randomness).

Everything is pure string assembly over a fake registry — no network, no LLM.
"""
from __future__ import annotations

import pytest

from covas.capabilities.base import CapabilityRegistry, HelpMeta, Slot, validate_help_meta
from covas.capabilities.help_capability import (
    _IDLE_EMPTY,
    _IDLE_FRAMES,
    _TOPIC_MISS,
    HelpCapability,
)

# --- fakes -----------------------------------------------------------------

class _Cap:
    """A minimal capability carrying help metadata (and optionally a vocabulary)."""

    def __init__(self, category, *, one_liner="It does a thing.", example="do a thing",
                 group="", slots=(), vocab=None, tool=None):
        self._meta = HelpMeta(category=category, one_liner=one_liner, example=example,
                              group=group, slots=tuple(slots))
        self._vocab = vocab
        self._tool = tool or f"tool_{category}"

    def tools(self):
        return [{"name": self._tool, "input_schema": {"type": "object", "properties": {}}}]

    def run_tool(self, name, inp):
        return f"{name} ran"

    def help_meta(self):
        return self._meta

    def help_vocabulary(self):
        return dict(self._vocab or {})


class _PlainCap:
    """A capability with NO help metadata — must register fine and stay out of help."""

    def tools(self):
        return [{"name": "plain", "input_schema": {"type": "object", "properties": {}}}]

    def run_tool(self, name, inp):
        return "plain ran"


class _BadHelpCap:
    """help_meta() missing a required field — registration must reject it."""

    def tools(self):
        return [{"name": "bad", "input_schema": {"type": "object", "properties": {}}}]

    def run_tool(self, name, inp):
        return "bad ran"

    def help_meta(self):
        return HelpMeta(category="broken", one_liner="", example="do it")


def _registry_with(*caps):
    reg = CapabilityRegistry()
    help_cap = HelpCapability(reg)
    reg.register(help_cap)
    for c in caps:
        reg.register(c)
    return reg, help_cap


def _idle(help_cap):
    return help_cap.run_tool("help", {})


# --- 1. registry rejects incomplete help metadata --------------------------

def test_validate_rejects_missing_top_level_field():
    with pytest.raises(ValueError):
        validate_help_meta(HelpMeta(category="x", one_liner="", example="say x"))


def test_validate_rejects_slot_missing_help_text():
    bad = HelpMeta(category="x", one_liner="does x", example="say x",
                   slots=(Slot(param="p", phrasings=("a p",), example="an x", help_text=""),))
    with pytest.raises(ValueError):
        validate_help_meta(bad)


def test_validate_rejects_slot_with_no_phrasings():
    bad = HelpMeta(category="x", one_liner="does x", example="say x",
                   slots=(Slot(param="p", phrasings=(), example="an x", help_text="h"),))
    with pytest.raises(ValueError):
        validate_help_meta(bad)


def test_registry_rejects_capability_with_incomplete_help_meta():
    reg = CapabilityRegistry()
    with pytest.raises(ValueError):
        reg.register(_BadHelpCap())


def test_registry_accepts_capability_without_help_meta():
    # Additive: a capability that carries no help metadata registers and runs fine.
    reg = CapabilityRegistry()
    reg.register(_PlainCap())
    assert reg.run_tool("plain", {}) == "plain ran"
    assert reg.help_entries() == []


def test_complete_help_meta_validates_and_registers():
    reg, _ = _registry_with(_Cap("outfitting"))
    assert reg.categories(exclude=None) == ["help", "outfitting"]


# --- 2. idle: empty-state, 3 + tail, ranking -------------------------------

def test_idle_empty_state_when_only_help_registered():
    # "0 caps" (help excludes itself): the empty-state must read correctly.
    reg, help_cap = _registry_with()
    out = _idle(help_cap)
    assert out in _IDLE_EMPTY
    # It must not fabricate a capability listing.
    assert "for " not in out.lower()


def test_idle_names_groups_once_capabilities_exist():
    # Same registry object, now with a capability: idle switches to the grouped listing and no
    # longer speaks the empty-state. Idle names the GROUP, not the capability's example.
    reg, help_cap = _registry_with()
    reg.register(_Cap("outfitting", group="navigation and search",
                      example="find the closest multi-cannon"))
    out = _idle(help_cap)
    assert out not in _IDLE_EMPTY
    assert "navigation and search" in out                    # the GROUP is named
    assert "find the closest multi-cannon" not in out        # the example is a drill-in, not here


def test_idle_lists_all_groups_without_examples():
    # Groups are the manageable overview tier — every distinct group is named, but no
    # capability example is read at the overview level (that would grow unbounded).
    caps = [_Cap(f"cat{i}", group=f"grp{i}", example=f"do thing {i}") for i in range(1, 6)]
    reg, help_cap = _registry_with(*caps)
    out = _idle(help_cap)
    for i in range(1, 6):
        assert f"grp{i}" in out
        assert f"do thing {i}" not in out


def test_idle_deduplicates_a_shared_group():
    # Several capabilities in one group collapse to a single group name in the overview.
    caps = [_Cap(f"cat{i}", group="navigation and search") for i in range(1, 4)]
    reg, help_cap = _registry_with(*caps)
    out = _idle(help_cap)
    overview = out.split(" Ask about")[0]        # the group list, before the drill-in example
    assert overview.count("navigation and search") == 1


def test_idle_ranks_by_usage():
    reg, help_cap = _registry_with(_Cap("alpha", tool="t_alpha"),
                                   _Cap("bravo", tool="t_bravo"))
    # Registration order first: alpha before bravo.
    assert reg.categories(exclude=help_cap) == ["alpha", "bravo"]
    # Use bravo's tool a few times -> it should rank ahead of alpha.
    for _ in range(3):
        reg.run_tool("t_bravo", {})
    assert reg.categories(exclude=help_cap) == ["bravo", "alpha"]


# --- 3. topic detail + unregistered-topic fallback -------------------------

def test_topic_detail_names_example_and_refinements():
    slots = [Slot(param="size", phrasings=("a size",), example="a large one",
                  help_text="say the size")]
    reg, help_cap = _registry_with(_Cap("outfitting", example="find the closest scoop",
                                        slots=slots))
    out = help_cap.run_tool("help", {"topic": "outfitting"})
    assert "outfitting" in out
    assert "find the closest scoop" in out
    assert "a size" in out  # the slot phrasing is offered


def test_group_topic_lists_members_three_plus_tail():
    # Asking about a GROUP lists its capabilities (with examples) — the ≤3+tail cap lives here
    # now, so a big group stays readable.
    caps = [_Cap(f"cat{i}", group="navigation and search", example=f"do thing {i}")
            for i in range(1, 6)]
    reg, help_cap = _registry_with(*caps)
    out = help_cap.run_tool("help", {"topic": "navigation and search"})
    for i in (1, 2, 3):
        assert f"cat{i}" in out and f"do thing {i}" in out
    assert "There are others too" in out
    assert "cat4" in out and "cat5" in out
    assert "do thing 4" not in out and "do thing 5" not in out


def test_group_topic_is_case_insensitive():
    caps = [_Cap(f"cat{i}", group="navigation and search") for i in range(1, 3)]
    reg, help_cap = _registry_with(*caps)
    out = help_cap.run_tool("help", {"topic": "Navigation And Search"})
    assert "cat1" in out and "cat2" in out


def test_singleton_group_topic_gives_capability_detail():
    # A group with one member drops straight to that capability's detail (no one-item list).
    reg, help_cap = _registry_with(_Cap("settings", group="settings",
                                        example="turn personality off"))
    out = help_cap.run_tool("help", {"topic": "settings"})
    assert "turn personality off" in out


def test_capability_topic_beats_group_when_names_would_overlap():
    # "outfitting" is a capability inside the "navigation and search" group; asking for the
    # capability by name gives its detail, not the group list.
    reg, help_cap = _registry_with(
        _Cap("outfitting", group="navigation and search", example="find the closest scoop"),
        _Cap("stations", group="navigation and search", example="find a station"))
    out = help_cap.run_tool("help", {"topic": "outfitting"})
    assert "find the closest scoop" in out
    assert "find a station" not in out       # not the whole group


def test_unregistered_topic_hits_fallback_without_raising_or_echoing_the_name():
    reg, help_cap = _registry_with(_Cap("outfitting"))
    out = help_cap.run_tool("help", {"topic": "teleportation"})
    assert out in _TOPIC_MISS
    # The unrecognized topic name must NOT be emitted (no implying it exists).
    assert "teleport" not in out.lower()


# --- 4. deterministic rotation ---------------------------------------------

def test_rotation_is_deterministic_across_a_fixed_call_sequence():
    reg, help_cap = _registry_with(_Cap("outfitting"))
    outs = [_idle(help_cap) for _ in range(4)]
    # Three distinct frames, then it wraps — a pure function of call order.
    assert outs[0] != outs[1] != outs[2]
    assert outs[0] != outs[2]
    assert outs[3] == outs[0]


def test_rotation_matches_the_frame_table():
    # Ungrouped cap -> its group falls back to the category, so the overview body is just
    # "outfitting". The rotating frame prefixes the (constant) drill-in invitation.
    reg, help_cap = _registry_with(_Cap("outfitting", example="find the closest scoop"))
    for i in range(len(_IDLE_FRAMES)):
        out = _idle(help_cap)
        assert out.startswith(_IDLE_FRAMES[i].format(body="outfitting"))
        assert "tell me about outfitting" in out


# --- 5. failure-recovery (the important mode) ------------------------------

def test_recovery_suggests_nearest_valid_module_from_the_registry():
    reg, help_cap = _registry_with(
        _Cap("outfitting", vocab={"module": ["Power Distributor", "Multi-Cannon",
                                             "Fuel Scoop"]}))
    out = help_cap.run_tool("help", {"unresolved": "power distributer",
                                     "expected": "module"})
    assert "Power Distributor" in out          # the validated correction
    assert "power distributer" in out          # echoes what the Commander said
    assert "as a module" in out
    assert "did you mean" in out.lower()


def test_recovery_suggestion_is_always_a_real_registry_value():
    vocab_modules = ["Power Distributor", "Multi-Cannon", "Fuel Scoop"]
    reg, help_cap = _registry_with(_Cap("outfitting", vocab={"module": vocab_modules}))
    out = help_cap.run_tool("help", {"unresolved": "multiple cannon", "expected": "module"})
    # Whatever it suggests must be one of the registry's canonical values.
    suggested = out.split("mean ")[-1].rstrip("?").strip()
    assert suggested in vocab_modules


def test_recovery_with_no_close_match_never_invents_a_name():
    reg, help_cap = _registry_with(_Cap("outfitting", vocab={"module": ["Multi-Cannon"]}))
    out = help_cap.run_tool("help", {"unresolved": "xyzzy plover", "expected": "module"})
    assert "xyzzy plover" in out          # echoes the term...
    assert "Multi-Cannon" not in out      # ...but suggests nothing bogus
    assert "did you mean" not in out.lower()


def test_recovery_never_recites_the_capability_list():
    reg, help_cap = _registry_with(
        _Cap("outfitting", vocab={"module": ["Multi-Cannon"]}),
        _Cap("stations"),
        _Cap("systems"),
    )
    out = help_cap.run_tool("help", {"unresolved": "power distributer",
                                     "expected": "module"})
    assert "stations" not in out and "systems" not in out


def test_recovery_against_the_real_outfitting_capability():
    # Exercise the actual retrofit, not just a stub: the outfitting capability contributes a
    # real module vocabulary, so recovery resolves a mishear against it.
    from covas.capabilities.find_closest_capability import FindClosestCapability, NavConfig
    reg = CapabilityRegistry()
    help_cap = HelpCapability(reg)
    reg.register(help_cap)
    reg.register(FindClosestCapability(NavConfig(enabled=True),
                                       get_current_system=lambda: "Sol"))
    out = help_cap.run_tool("help", {"unresolved": "power distributer",
                                     "expected": "module"})
    assert "Power Distributor" in out


# --- help registers itself -------------------------------------------------

def test_help_registers_itself_so_what_can_you_do_always_answers():
    reg, help_cap = _registry_with()   # nothing but help
    assert "help" in reg.categories()
    # And the tool is dispatchable through the registry.
    assert isinstance(reg.run_tool("help", {}), str)
