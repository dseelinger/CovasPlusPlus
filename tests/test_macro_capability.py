"""End-to-end unit tests for the custom-macro capability (issue #50, §9) — offline + hermetic.

A recording fake executor, a fake Status feed, a synchronous spawner, and a fake clock mean the
whole author -> validate -> persist -> run path (and a bus-triggered run) is exercised with NO
real key presses, NO network, and NO real time. Asserts the anti-hallucination refusal, the
benign/consequential run split, the combat guard, trigger auto-run + arm-and-speak, the shared
hard abort, and the trigger debounce.
"""
from __future__ import annotations

import threading

from covas.capabilities.macro_capability import MacroCapability, MacroConfig
from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import Macro
from covas.macros.store import MacroStore


# ---- fakes ----------------------------------------------------------------

class RecordingExecutor:
    def __init__(self) -> None:
        self.ops: list[tuple] = []

    def press(self, binding) -> None:
        self.ops.append(("press", binding.key))

    def hold(self, binding, seconds) -> None:
        self.ops.append(("hold", binding.key, seconds))

    def release(self, binding) -> None:
        self.ops.append(("release", binding.key))

    def release_all(self) -> None:
        self.ops.append(("release_all",))


# A hermetic action registry (not the shipped catalog) so the tests are self-contained.
_GEAR = Macro(name="landing_gear", tool="toggle_landing_gear", action="LandingGearToggle",
              arm_phrase="toggle the landing gear", done_phrase="Gear toggled.",
              modes=frozenset({"mainship"}), confirm_required=True)
_THROTTLE = Macro(name="throttle_zero", tool="set_throttle_zero", action="SetSpeedZero",
                  arm_phrase="set throttle to zero", done_phrase="Throttle at zero.",
                  modes=frozenset({"mainship", "fighter"}), confirm_required=False)
_ACTIONS = {m.name: m for m in (_GEAR, _THROTTLE)}
_ALLOW = frozenset({"landing_gear", "throttle_zero"})
_BINDS = {
    "SetSpeedZero": KeyBinding(action="SetSpeedZero", key="Key_0"),
    "LandingGearToggle": KeyBinding(action="LandingGearToggle", key="Key_L"),
}
_SAFE = {"in_danger": False, "being_interdicted": False, "game_mode": "mainship",
         "docked": False, "landing_gear": False}


def _cap(tmp_path, *, status=None, speak=None, spawn=None, abort=None, require_confirmation=True):
    store = MacroStore(tmp_path / "macros.jsonl")
    execu = RecordingExecutor()
    snap = {"v": dict(status if status is not None else _SAFE)}
    cap = MacroCapability(
        store=store,
        config=MacroConfig(enabled=True, require_confirmation=require_confirmation),
        binds=_BINDS, executor=execu,
        allowlist=lambda: _ALLOW,
        actions=_ACTIONS,
        status_snapshot=lambda: snap["v"],
        abort_event=abort or threading.Event(),
        speak=speak,
        spawn=spawn or (lambda fn: fn()),   # synchronous — no thread in tests
        clock=lambda: _cap._t,              # fake monotonic clock (advanced by tests)
        sleep=lambda s: None,
        log=lambda m: None,
    )
    return cap, store, execu, snap


_cap._t = 0.0  # module-level fake clock the capability reads via the lambda above


def _create(cap, name="M", steps=None, trigger="", confirm=True):
    return cap.run_tool("create_macro", {
        "name": name, "trigger": trigger, "confirm": confirm,
        "steps": steps if steps is not None else [{"type": "action", "action": "throttle_zero"}],
    })


# ---- authoring ------------------------------------------------------------

def test_create_valid_macro_persists(tmp_path):
    cap, store, _, _ = _cap(tmp_path)
    out = _create(cap, name="Slow", steps=[{"type": "action", "action": "throttle_zero"}])
    assert "Slow" in out
    assert store.get("Slow") is not None


def test_create_unknown_action_is_refused_and_saves_nothing(tmp_path):
    cap, store, _, _ = _cap(tmp_path)
    out = _create(cap, name="Bad", steps=[{"type": "action", "action": "launch_torpedoes"}])
    assert "launch_torpedoes" in out                 # templated refusal
    assert store.all() == []                          # NOTHING persisted


def test_create_non_allowlisted_action_is_refused(tmp_path):
    cap, store, _, _ = _cap(tmp_path)
    # 'landing_gear' is a known action but drop it from the allowlist for this cap.
    cap._allowlist = lambda: frozenset({"throttle_zero"})
    out = _create(cap, name="Bad", steps=[{"type": "action", "action": "landing_gear"}])
    assert "allowlist" in out.lower()
    assert store.all() == []


# ---- running: benign vs consequential -------------------------------------

def test_run_benign_macro_executes_immediately(tmp_path):
    cap, _, execu, _ = _cap(tmp_path)
    _create(cap, name="Slow", steps=[{"type": "action", "action": "throttle_zero"}], confirm=False)
    out = cap.run_tool("run_macro", {"name": "Slow"})
    assert ("press", "Key_0") in execu.ops
    assert "Slow" in out                              # custom macro reports its own name


def test_run_consequential_macro_arms_then_confirms(tmp_path):
    cap, _, execu, _ = _cap(tmp_path)
    _create(cap, name="Gear", steps=[{"type": "action", "action": "landing_gear"}])
    cap.new_turn()
    armed = cap.run_tool("run_macro", {"name": "Gear"})
    assert "armed" in armed.lower()
    assert execu.ops == []                            # NOT fired on arm
    # same-turn confirm is refused
    assert "separate" in cap.run_tool("confirm_macro", {}).lower()
    # a genuine, separate confirmation runs it
    cap.new_turn()
    done = cap.run_tool("confirm_macro", {})
    assert ("press", "Key_L") in execu.ops
    assert "Gear" in done


def test_run_missing_macro(tmp_path):
    cap, _, _, _ = _cap(tmp_path)
    assert "don't have a macro" in cap.run_tool("run_macro", {"name": "ghost"}).lower()


# ---- combat guard ---------------------------------------------------------

def test_run_refused_in_combat(tmp_path):
    cap, _, execu, snap = _cap(tmp_path)
    _create(cap, name="Slow", steps=[{"type": "action", "action": "throttle_zero"}], confirm=False)
    snap["v"] = {**_SAFE, "in_danger": True}
    out = cap.run_tool("run_macro", {"name": "Slow"})
    assert "combat" in out.lower() or "danger" in out.lower()
    assert execu.ops == []                            # guard refused before any press


def test_run_refused_when_status_unavailable(tmp_path):
    cap, store, execu, _ = _cap(tmp_path)
    cap._status = lambda: None                        # no telemetry -> can't prove safe
    _create(cap, name="Slow", steps=[{"type": "action", "action": "throttle_zero"}], confirm=False)
    out = cap.run_tool("run_macro", {"name": "Slow"})
    assert "status" in out.lower() or "monitoring" in out.lower()
    assert execu.ops == []


# ---- unbound key degrades to a spoken message -----------------------------

def test_unbound_action_reports_bind_it_ingame(tmp_path):
    cap, _, execu, _ = _cap(tmp_path)
    cap._binds = {}                                   # nothing bound
    _create(cap, name="Slow", steps=[{"type": "action", "action": "throttle_zero"}], confirm=False)
    out = cap.run_tool("run_macro", {"name": "Slow"})
    assert "bind" in out.lower()
    assert execu.ops == []


# ---- trigger binding ------------------------------------------------------

def test_triggered_benign_macro_autoruns_on_bus_event(tmp_path):
    spoken: list[str] = []
    cap, _, execu, _ = _cap(tmp_path, speak=spoken.append)
    _create(cap, name="OnDock", trigger="docked", confirm=False,
            steps=[{"type": "action", "action": "throttle_zero"}])
    cap.on_event({"type": "ed_event", "event": "Docked"})
    assert ("press", "Key_0") in execu.ops            # auto-ran behind the guard
    assert any("OnDock" in s for s in spoken)         # spoke the macro's outcome


def test_triggered_consequential_macro_arms_and_speaks_not_runs(tmp_path):
    spoken: list[str] = []
    cap, _, execu, _ = _cap(tmp_path, speak=spoken.append)
    _create(cap, name="GearDock", trigger="docking_granted",
            steps=[{"type": "action", "action": "landing_gear"}])
    cap.on_event({"type": "ed_event", "event": "DockingGranted"})
    assert execu.ops == []                            # did NOT fire itself
    assert any("confirm" in s.lower() for s in spoken)
    # the Commander's spoken confirm then runs it
    cap.new_turn()
    cap.run_tool("confirm_macro", {})
    assert ("press", "Key_L") in execu.ops


def test_trigger_debounces_double_event(tmp_path):
    _cap._t = 100.0
    cap, _, execu, _ = _cap(tmp_path)
    _create(cap, name="OnDock", trigger="docked", confirm=False,
            steps=[{"type": "action", "action": "throttle_zero"}])
    # The journal 'Docked' and the Status 'Docked' transition both map to the 'docked' trigger and
    # arrive within a second — must run the macro only ONCE.
    cap.on_event({"type": "ed_event", "event": "Docked"})
    cap.on_event({"type": "ed_event", "event": "Docked"})
    assert [o for o in execu.ops if o[0] == "press"] == [("press", "Key_0")]


def test_unrelated_event_does_not_fire(tmp_path):
    cap, _, execu, _ = _cap(tmp_path)
    _create(cap, name="OnDock", trigger="docked", confirm=False,
            steps=[{"type": "action", "action": "throttle_zero"}])
    cap.on_event({"type": "ed_event", "event": "FSDJump"})    # not this macro's trigger
    assert execu.ops == []


# ---- hard abort -----------------------------------------------------------

def test_abort_clears_pending_and_releases(tmp_path):
    abort = threading.Event()
    cap, _, execu, _ = _cap(tmp_path, abort=abort)
    _create(cap, name="Gear", steps=[{"type": "action", "action": "landing_gear"}])
    cap.new_turn()
    cap.run_tool("run_macro", {"name": "Gear"})       # armed
    out = cap.run_tool("abort_macros", {})
    assert abort.is_set()                             # loop-level abort raised
    assert ("release_all",) in execu.ops             # keys released
    assert "abort" in out.lower()
    # the armed macro is gone: a later confirm finds nothing
    cap.new_turn()
    assert "nothing to confirm" in cap.run_tool("confirm_macro", {}).lower()


# ---- listing / deleting ---------------------------------------------------

def test_create_tool_schema_action_enum(tmp_path):
    cap, _, _, _ = _cap(tmp_path)
    create = next(t for t in cap.tools() if t["name"] == "create_macro")
    action = create["input_schema"]["properties"]["steps"]["items"]["properties"]["action"]
    assert set(action["enum"]) == _ALLOW            # nudged toward allowlisted actions
    # An empty allowlist must NOT emit an empty enum (some providers reject it).
    cap._allowlist = lambda: frozenset()
    create = next(t for t in cap.tools() if t["name"] == "create_macro")
    action = create["input_schema"]["properties"]["steps"]["items"]["properties"]["action"]
    assert "enum" not in action


def test_list_and_delete(tmp_path):
    cap, store, _, _ = _cap(tmp_path)
    _create(cap, name="Alpha", steps=[{"type": "action", "action": "throttle_zero"}])
    assert "Alpha" in cap.run_tool("list_macros", {})
    assert "Deleted" in cap.run_tool("delete_macro", {"name": "Alpha"})
    assert store.get("Alpha") is None
    assert "don't have" in cap.run_tool("delete_macro", {"name": "Alpha"}).lower()
