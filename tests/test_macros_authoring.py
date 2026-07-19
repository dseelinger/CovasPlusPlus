"""Unit tests for custom-macro authoring internals (issue #50, §9) — pure + offline.

Covers the anti-hallucination compiler, the spec (de)serialization, the fail-soft JSONL store,
and the shared confirmation gate — all with no game, no IO beyond a tmp file, no real time.
"""
from __future__ import annotations

import pytest

from covas.keybinds.confirm import (CONFIRM_EXPIRED, CONFIRM_NONE_PENDING, CONFIRM_OK,
                                     CONFIRM_SAME_TURN, ConfirmGate)
from covas.keybinds.registry import Macro
from covas.keybinds.sequence import AWAIT_STATUS, PRESS, REQUIRE_STATUS, WAIT
from covas.macros.compile import CUSTOM_TOOL_PREFIX, MacroValidationError, compile_macro
from covas.macros.spec import (ACTION, AWAIT_STATUS as SPEC_AWAIT, REQUIRE_STATUS as SPEC_REQUIRE,
                               WAIT as SPEC_WAIT, MacroSpec, MacroStepSpec)
from covas.macros.store import MacroStore

# ---- a small hermetic action registry (no reliance on the shipped catalog) ----------------

_GEAR = Macro(name="landing_gear", tool="toggle_landing_gear", action="LandingGearToggle",
              arm_phrase="toggle the landing gear", done_phrase="Gear toggled.",
              modes=frozenset({"mainship"}), confirm_required=True)
_THROTTLE = Macro(name="throttle_zero", tool="set_throttle_zero", action="SetSpeedZero",
                  arm_phrase="set throttle to zero", done_phrase="Throttle zero.",
                  modes=frozenset({"mainship", "fighter"}), confirm_required=False)
_FOOT = Macro(name="on_foot_flashlight", tool="toggle_flashlight", action="HumanoidFlashlight",
              arm_phrase="toggle flashlight", done_phrase="Flashlight.",
              modes=frozenset({"on_foot"}), confirm_required=False)

_ACTIONS = {m.name: m for m in (_GEAR, _THROTTLE, _FOOT)}
_ALLOW = frozenset({"landing_gear", "throttle_zero", "on_foot_flashlight"})


def _spec(*steps, name="M", trigger="", confirm=True):
    return MacroSpec(name=name, steps=tuple(steps), trigger=trigger, confirm=confirm)


def _action(name):
    return MacroStepSpec(kind=ACTION, action=name)


# ---- compile: happy path --------------------------------------------------

def test_compile_flattens_actions_and_gates_into_steps():
    spec = _spec(
        MacroStepSpec(kind=SPEC_REQUIRE, status="docked", expect=False),
        _action("throttle_zero"),
        MacroStepSpec(kind=SPEC_WAIT, seconds=2.0),
        _action("landing_gear"),
        MacroStepSpec(kind=SPEC_AWAIT, status="landing_gear", expect=True, seconds=5.0),
    )
    macro = compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW)
    kinds = [(s.kind, s.action or s.status_key) for s in macro.steps]
    assert kinds == [
        (REQUIRE_STATUS, "docked"),
        (PRESS, "SetSpeedZero"),
        (WAIT, None),
        (PRESS, "LandingGearToggle"),
        (AWAIT_STATUS, "landing_gear"),
    ]
    # tool id is namespaced so it can't collide with a shipped macro tool
    assert macro.tool.startswith(CUSTOM_TOOL_PREFIX)


def test_compile_confirm_is_raised_by_a_consequential_step():
    # Author asked for confirm=False, but landing_gear is consequential -> effective confirm True.
    spec = _spec(_action("landing_gear"), confirm=False)
    assert compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW).confirm_required is True


def test_compile_benign_macro_can_stay_no_confirm():
    spec = _spec(_action("throttle_zero"), confirm=False)
    assert compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW).confirm_required is False


def test_compile_modes_are_intersection_of_steps():
    # throttle (ship|fighter) ∩ gear (ship) == {ship}
    spec = _spec(_action("throttle_zero"), _action("landing_gear"))
    assert compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW).modes == frozenset({"mainship"})


# ---- compile: the anti-hallucination refusals -----------------------------

def test_unknown_action_is_rejected_and_lists_real_options():
    spec = _spec(_action("fire_all_weapons"))
    with pytest.raises(MacroValidationError) as ei:
        compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW)
    msg = str(ei.value)
    assert "fire_all_weapons" in msg
    assert "landing_gear" in msg          # the error names REAL, allowlisted options


def test_non_allowlisted_action_is_rejected():
    spec = _spec(_action("landing_gear"))
    with pytest.raises(MacroValidationError) as ei:
        compile_macro(spec, actions=_ACTIONS, allowlist=frozenset({"throttle_zero"}))
    assert "allowlist" in str(ei.value).lower()


def test_unknown_trigger_is_rejected():
    spec = _spec(_action("throttle_zero"), trigger="when_i_feel_like_it")
    with pytest.raises(MacroValidationError) as ei:
        compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW)
    assert "trigger" in str(ei.value).lower()


def test_unknown_status_condition_is_rejected():
    spec = _spec(MacroStepSpec(kind=SPEC_REQUIRE, status="shields_full", expect=True),
                 _action("throttle_zero"))
    with pytest.raises(MacroValidationError) as ei:
        compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW)
    assert "shields_full" in str(ei.value)


def test_cross_mode_macro_is_rejected():
    # ship action + on-foot action can never both be valid -> empty mode intersection.
    spec = _spec(_action("throttle_zero"), _action("on_foot_flashlight"))
    with pytest.raises(MacroValidationError) as ei:
        compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW)
    assert "mode" in str(ei.value).lower()


def test_macro_with_no_action_is_rejected():
    spec = _spec(MacroStepSpec(kind=SPEC_WAIT, seconds=1.0))
    with pytest.raises(MacroValidationError):
        compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW)


def test_known_trigger_compiles():
    spec = _spec(_action("throttle_zero"), trigger="docking_granted")
    assert compile_macro(spec, actions=_ACTIONS, allowlist=_ALLOW).name == "M"


# ---- spec (de)serialization -----------------------------------------------

def test_spec_round_trips_through_dict():
    spec = _spec(
        _action("throttle_zero"),
        MacroStepSpec(kind=SPEC_AWAIT, status="docked", expect=True, seconds=3.0),
        name="Dock ASAP", trigger="docked", confirm=False)
    again = MacroSpec.from_dict(spec.to_dict())
    assert again.name == "Dock ASAP"
    assert again.trigger == "docked"
    assert again.confirm is False
    assert again.steps == spec.steps
    assert again.id == spec.id and again.when == spec.when


def test_spec_from_dict_rejects_nameless_or_empty():
    with pytest.raises(ValueError):
        MacroSpec.from_dict({"name": "", "steps": [{"kind": "action", "action": "x"}]})
    with pytest.raises(ValueError):
        MacroSpec.from_dict({"name": "x", "steps": []})


def test_from_dict_parses_string_expect_false_as_false(tmp_path):
    # A hand-edited "expect":"false" must NOT become True (bare bool("false") is True), which would
    # invert the precondition. #159: strings are parsed against the true/false vocabularies.
    step = MacroStepSpec.from_dict({"kind": "require_status", "status": "docked", "expect": "false"})
    assert step.expect is False
    for falsey in ("false", "False", "0", "no", "off"):
        s = MacroStepSpec.from_dict({"kind": "require_status", "status": "docked", "expect": falsey})
        assert s.expect is False, falsey
    for truthy in ("true", "1", "yes", "on"):
        s = MacroStepSpec.from_dict({"kind": "require_status", "status": "docked", "expect": truthy})
        assert s.expect is True, truthy
    # A real bool and the default both still work.
    assert MacroStepSpec.from_dict({"kind": "require_status", "status": "docked",
                                    "expect": False}).expect is False
    assert MacroStepSpec.from_dict({"kind": "require_status", "status": "docked"}).expect is True


def test_from_dict_parses_string_confirm_false_as_false():
    # The sibling `confirm` bool shared the same bug — a hand-edited "confirm":"false" must be False.
    spec = MacroSpec.from_dict({"name": "M", "confirm": "false",
                                "steps": [{"kind": "action", "action": "throttle_zero"}]})
    assert spec.confirm is False


# ---- store: fail-soft JSONL -----------------------------------------------

def test_store_add_get_delete(tmp_path):
    store = MacroStore(tmp_path / "macros.jsonl")
    store.add(_spec(_action("throttle_zero"), name="One"))
    store.add(_spec(_action("landing_gear"), name="Two"))
    assert {s.name for s in store.all()} == {"One", "Two"}
    assert store.get("one").name == "One"           # case-insensitive lookup
    assert store.delete("One") is True
    assert store.get("One") is None
    assert store.delete("nope") is False


def test_store_add_replaces_same_name(tmp_path):
    store = MacroStore(tmp_path / "macros.jsonl")
    store.add(_spec(_action("throttle_zero"), name="Dup"))
    store.add(_spec(_action("landing_gear"), name="dup"))   # same name, different case
    assert len(store.all()) == 1
    assert store.get("Dup").steps[0].action == "landing_gear"


def test_store_skips_a_corrupt_line(tmp_path):
    p = tmp_path / "macros.jsonl"
    good = MacroSpec(name="Good", steps=(_action("throttle_zero"),)).to_dict()
    import json
    p.write_text(json.dumps(good) + "\n" + "{not json\n" + '{"name":"","steps":[]}\n',
                 encoding="utf-8")
    specs = MacroStore(p).load()
    assert [s.name for s in specs] == ["Good"]        # the two bad lines are skipped, not fatal


def test_store_persists_across_instances(tmp_path):
    p = tmp_path / "macros.jsonl"
    MacroStore(p).add(_spec(_action("throttle_zero"), name="Persisted"))
    assert MacroStore(p).get("Persisted") is not None


def test_save_uses_a_unique_temp_file_not_a_fixed_one(tmp_path, monkeypatch):
    # #159: two concurrent saves must not interleave into one shared `.tmp`. Assert each save
    # allocates its OWN uniquely-named temp file (via tempfile.mkstemp), never a fixed `<name>.tmp`.
    import covas.macros.store as store_mod
    p = tmp_path / "macros.jsonl"
    store = MacroStore(p)
    store.add(_spec(_action("throttle_zero"), name="One"))   # first real save (warms the file)

    seen: list[str] = []
    real_mkstemp = store_mod.tempfile.mkstemp

    def spy_mkstemp(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(store_mod.tempfile, "mkstemp", spy_mkstemp)
    store.add(_spec(_action("landing_gear"), name="Two"))
    store.add(_spec(_action("throttle_zero"), name="Three"))
    assert len(seen) == 2                     # each save minted a temp file
    assert seen[0] != seen[1]                 # ...and they were DISTINCT (no fixed collision)
    fixed = str(p.with_suffix(p.suffix + ".tmp"))   # the OLD fixed temp name
    assert fixed not in seen                  # ...never the fixed path two savers could share
    # Final content is intact and complete (atomic replace left no torn file).
    assert {s.name for s in MacroStore(p).all()} == {"One", "Two", "Three"}
    assert list(tmp_path.glob("*.tmp")) == []  # no orphan temp files left behind


def test_concurrent_saves_leave_a_valid_file(tmp_path):
    # Two threads hammering add()/save() on the same store must never leave a corrupt/torn file:
    # unique temp + atomic replace means every load() parses cleanly, last-writer-wins.
    import threading
    p = tmp_path / "macros.jsonl"
    store = MacroStore(p)
    store.add(_spec(_action("throttle_zero"), name="Seed"))

    errors: list[Exception] = []

    def worker(tag: str) -> None:
        try:
            for i in range(25):
                store.save([_spec(_action("throttle_zero"), name=f"{tag}{i}")])
        except Exception as e:  # noqa: BLE001 — surface any thread fault to the assertion
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Whatever the interleaving, the file on disk is parseable (no half-written line) and non-empty.
    reloaded = MacroStore(p).load()
    assert len(reloaded) == 1
    assert list(tmp_path.glob("*.tmp")) == []


# ---- confirmation gate ----------------------------------------------------

def test_confirm_gate_requires_a_separate_turn():
    clock = [0.0]
    gate: ConfirmGate[str] = ConfirmGate(confirm_window=60.0, clock=lambda: clock[0])
    assert gate.confirm().status == CONFIRM_NONE_PENDING
    gate.arm("payload")
    assert gate.confirm().status == CONFIRM_SAME_TURN     # same utterance -> refused
    gate.new_turn()
    v = gate.confirm()
    assert v.status == CONFIRM_OK and v.payload == "payload"
    assert gate.confirm().status == CONFIRM_NONE_PENDING  # consumed


def test_confirm_gate_expires():
    clock = [0.0]
    gate: ConfirmGate[str] = ConfirmGate(confirm_window=10.0, clock=lambda: clock[0])
    gate.arm("p")
    gate.new_turn()
    clock[0] = 11.0
    assert gate.confirm().status == CONFIRM_EXPIRED


def test_confirm_gate_clear():
    gate: ConfirmGate[str] = ConfirmGate()
    gate.arm("p")
    gate.clear()
    gate.new_turn()
    assert gate.confirm().status == CONFIRM_NONE_PENDING
