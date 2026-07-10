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

from covas.capabilities.base import (CapabilityRegistry, HelpMeta, Slot,
                                     validate_help_meta)
from covas.capabilities.help_capability import (_IDLE_EMPTY, _IDLE_FRAMES,
                                                _TOPIC_MISS, HelpCapability)


# --- fakes -----------------------------------------------------------------

class _Cap:
    """A minimal capability carrying help metadata (and optionally a vocabulary)."""

    def __init__(self, category, *, one_liner="It does a thing.", example="do a thing",
                 slots=(), vocab=None, tool=None):
        self._meta = HelpMeta(category=category, one_liner=one_liner, example=example,
                              slots=tuple(slots))
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


def test_idle_empty_state_reads_right_once_capabilities_exist():
    # Same registry object, now with a capability: idle switches to the real listing and no
    # longer speaks the empty-state.
    reg, help_cap = _registry_with()
    reg.register(_Cap("outfitting", example="find the closest multi-cannon"))
    out = _idle(help_cap)
    assert out not in _IDLE_EMPTY
    assert "outfitting" in out and "find the closest multi-cannon" in out


def test_idle_lists_at_most_three_plus_tail():
    caps = [_Cap(f"cat{i}", example=f"do thing {i}") for i in range(1, 6)]  # 5 categories
    reg, help_cap = _registry_with(*caps)
    out = _idle(help_cap)
    # First three appear with their examples...
    for i in (1, 2, 3):
        assert f"cat{i}" in out and f"do thing {i}" in out
    # ...the rest only in the "there are others" tail (no example spoken for them).
    assert "There are others too" in out
    assert "cat4" in out and "cat5" in out
    assert "do thing 4" not in out and "do thing 5" not in out


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
    reg, help_cap = _registry_with(_Cap("outfitting", example="find the closest scoop"))
    body = 'for outfitting, say "find the closest scoop"'
    for i in range(len(_IDLE_FRAMES)):
        out = _idle(help_cap)
        assert out == _IDLE_FRAMES[i].format(body=body)


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
    from covas.capabilities.find_closest_capability import (FindClosestCapability,
                                                            NavConfig)
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
