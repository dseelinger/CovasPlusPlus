"""Regression tests for issue #154 — a per-run reset must not defeat a shared hard abort.

The old design overloaded ONE `threading.Event` as both the global stop signal (`set()` on
abort) and a per-run reset (`clear()` at the start of every run). Because the Event was shared
between the keybind and custom-macro capabilities, and triggered macros run on their own daemon
threads, a benign macro STARTING concurrently with a running keybind sequence would `clear()`
the abort just `set()` for that sequence — the sequence's next poll then read False and re-pressed
its remaining keys after `release_all()` had already lifted them.

These tests pin the fix (`covas/keybinds/abort.AbortController`, per-run abort tokens): offline,
hermetic, no real threads/sleeps — the concurrent macro start is modelled deterministically by
firing it from inside the sequence's first keypress, exactly where the old race lived.
"""
from __future__ import annotations

from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig
from covas.capabilities.macro_capability import MacroCapability, MacroConfig
from covas.keybinds.abort import AbortController
from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import Macro
from covas.keybinds.sequence import PRESS, Step
from covas.macros.store import MacroStore

# ---- AbortController semantics (the primitive the fix rests on) -------------

def test_abort_marks_every_in_flight_run():
    ctl = AbortController()
    a, b = ctl.begin(), ctl.begin()
    ctl.abort()
    assert ctl.is_aborted(a) and ctl.is_aborted(b)   # one abort stops ALL in-flight runs
    assert ctl.abort_count == 1


def test_new_run_does_not_clear_a_concurrent_runs_abort():
    # The exact #154 race, at the primitive level: A is running and gets aborted; B starts
    # concurrently. B's start must NOT erase A's abort (the old clear() did).
    ctl = AbortController()
    a = ctl.begin()                 # sequence A running
    ctl.abort()                     # Commander says "abort" — A must stop
    assert ctl.is_aborted(a)
    b = ctl.begin()                 # macro B starts concurrently
    assert not ctl.is_aborted(b)    # B is a genuinely fresh run (its own token)
    assert ctl.is_aborted(a)        # ...but A's abort SURVIVES B's start
    ctl.end(b)
    assert ctl.is_aborted(a)        # ...and survives B finishing, too


def test_abort_does_not_latch_onto_later_runs():
    # An abort raised while nothing is running must not kill the NEXT run (matches the old
    # "a fresh run is not killed by a stale abort" intent).
    ctl = AbortController()
    ctl.abort()
    t = ctl.begin()
    assert not ctl.is_aborted(t)


# ---- end-to-end: two capabilities sharing one controller -------------------

class _RecordingExecutor:
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


def test_concurrent_macro_start_does_not_defeat_keybind_hard_abort(tmp_path):
    shared = AbortController()
    ex = _RecordingExecutor()

    # A two-step keybind SEQUENCE (press StepOne, then StepTwo), allowlisted, confirmation off.
    seq = Macro(name="twostep", tool="run_twostep", action="", arm_phrase="do two steps",
                done_phrase="done", confirm_required=False,
                steps=(Step(PRESS, action="StepOne"), Step(PRESS, action="StepTwo")))
    kbinds = {"StepOne": KeyBinding(action="StepOne", key="Key_1"),
              "StepTwo": KeyBinding(action="StepTwo", key="Key_2")}
    kcap = KeybindCapability(
        binds=kbinds, executor=ex,
        config=KeybindConfig(enabled=True, require_confirmation=False, combat_guard=False,
                             mode_guard=False, allowlist=("twostep",)),
        macros={"twostep": seq}, status_snapshot=None, abort_controller=shared)

    # A benign custom macro on the SAME shared abort controller and the SAME executor.
    benign = Macro(name="benign", tool="run_benign", action="", arm_phrase="benign",
                   done_phrase="benign done", confirm_required=False,
                   steps=(Step(PRESS, action="Benign"),))
    mcap = MacroCapability(
        store=MacroStore(tmp_path / "macros.jsonl"),
        config=MacroConfig(enabled=True, require_confirmation=False, combat_guard=False,
                           mode_guard=False),
        binds={"Benign": KeyBinding(action="Benign", key="Key_B")}, executor=ex,
        allowlist=lambda: frozenset(), actions={}, status_snapshot=None,
        abort_controller=shared, spawn=lambda fn: fn(), sleep=lambda s: None)

    # Model the concurrency deterministically: when sequence A presses its FIRST key, the Commander
    # says "abort" (raising the shared hard abort + release_all) AND a benign triggered macro B
    # starts executing at the same instant. Under the OLD single-Event design, B's _execute() would
    # clear() the abort A had just raised — so A's next poll read False and re-pressed StepTwo.
    fired = {"done": False}
    real_press = ex.press

    def press(binding):
        real_press(binding)
        if binding.key == "Key_1" and not fired["done"]:
            fired["done"] = True
            kcap.run_tool("abort_keybinds", {})   # Commander aborts the running sequence A
            mcap._execute(benign)                 # macro B starts concurrently (mints a fresh token)

    ex.press = press  # type: ignore[assignment]

    kcap.run_tool("run_twostep", {})              # run sequence A to completion

    presses = [op for op in ex.ops if op[0] == "press"]
    assert ("press", "Key_1") in presses          # first step ran (that's where abort fired)
    assert ("press", "Key_2") not in presses      # remaining step SUPPRESSED — the abort survived
    assert ("press", "Key_B") in presses          # the concurrent benign macro still ran normally
    assert ("release_all",) in ex.ops             # the hard abort released held keys
