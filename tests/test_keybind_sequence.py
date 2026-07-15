"""Unit tests for the status-checked timed macro framework (DESIGN §6, issue #33, §9).

Offline and hermetic: a recording fake executor asserts the press/hold/release ORDER, a fake
status feed drives the inter-step checks, and an injected fake clock+sleep means NO real key
presses and NO real waiting. Two layers are covered:

  * `run_sequence` directly — the runner's primitives (press/hold/wait/require/await), its
    failure paths (unmet precondition, await timeout, missing binding), and abort.
  * `KeybindCapability` end-to-end — the shipped `launch` sequence behind confirmation, the
    hard abort stopping a run + releasing keys, and mode/allowlist gating of a sequence macro.
"""
from __future__ import annotations

from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig
from covas.keybinds.binds import KeyBinding
from covas.keybinds.registry import Macro
from covas.keybinds.sequence import (AWAIT_STATUS, HOLD, PRESS, RELEASE, REQUIRE_STATUS, WAIT,
                                      Step, run_sequence)


# ---- fakes ----------------------------------------------------------------


class RecordingExecutor:
    """Records executor primitives in call ORDER (no real key events, no real sleep — hold
    just logs the requested duration)."""

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


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeSleep:
    """A sleep that advances the injected clock instead of blocking — so awaits/deadlines are
    deterministic and instant."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.total = 0.0

    def __call__(self, seconds: float) -> None:
        self.total += seconds
        self.clock.t += seconds


# Bindings for the tokens the launch sequence presses.
_BINDS = {
    "SetSpeed50": KeyBinding(action="SetSpeed50", key="Key_2"),
    "UpThrustButton": KeyBinding(action="UpThrustButton", key="Key_R"),
    "UseBoostJuice": KeyBinding(action="UseBoostJuice", key="Key_Tab"),
    "LandingGearToggle": KeyBinding(action="LandingGearToggle", key="Key_L"),
}


def _runner_env():
    clk = FakeClock()
    return RecordingExecutor(), clk, FakeSleep(clk)


# ---- run_sequence: primitives + order -------------------------------------


def test_sequence_runs_steps_in_order():
    ex, clk, slp = _runner_env()
    steps = (
        Step(PRESS, action="SetSpeed50"),
        Step(HOLD, action="UpThrustButton", seconds=1.2),
        Step(WAIT, seconds=0.5),
        Step(PRESS, action="UseBoostJuice"),
        Step(RELEASE, action="UpThrustButton"),
    )
    out = run_sequence(steps, executor=ex, binds=_BINDS, status=lambda: {}, sleep=slp, clock=clk)
    assert out.status == "done"
    assert ex.ops == [
        ("press", "Key_2"),
        ("hold", "Key_R", 1.2),
        ("press", "Key_Tab"),      # the WAIT injects sleep, not an executor op
        ("release", "Key_R"),
    ]
    assert slp.total == 0.5        # the wait actually slept (via the injected fake), 0.5s


def test_require_status_precondition_fails_fast():
    # gear is UP, but the precondition needs it DOWN -> refuse before any key is pressed.
    ex, clk, slp = _runner_env()
    steps = (
        Step(REQUIRE_STATUS, status_key="landing_gear", expect=True, describe="gear must be down"),
        Step(PRESS, action="SetSpeed50"),
    )
    out = run_sequence(steps, executor=ex, binds=_BINDS,
                       status=lambda: {"landing_gear": False}, sleep=slp, clock=clk)
    assert out.status == "failed"
    assert "gear must be down" in out.message
    # nothing pressed; release_all called defensively on failure
    assert ex.ops == [("release_all",)]


def test_require_status_precondition_passes():
    ex, clk, slp = _runner_env()
    steps = (
        Step(REQUIRE_STATUS, status_key="landing_gear", expect=True),
        Step(PRESS, action="SetSpeed50"),
    )
    out = run_sequence(steps, executor=ex, binds=_BINDS,
                       status=lambda: {"landing_gear": True}, sleep=slp, clock=clk)
    assert out.status == "done"
    assert ex.ops == [("press", "Key_2")]


def test_await_status_succeeds_when_flag_flips():
    # Poll until Status.json reports the gear retracted; it flips to up on the 3rd read.
    ex, clk, slp = _runner_env()
    reads = {"n": 0}

    def status():
        reads["n"] += 1
        return {"landing_gear": reads["n"] < 3}   # True, True, then False (retracted)

    steps = (
        Step(PRESS, action="LandingGearToggle"),
        Step(AWAIT_STATUS, status_key="landing_gear", expect=False, seconds=4.0, poll=0.5),
    )
    out = run_sequence(steps, executor=ex, binds=_BINDS, status=status, sleep=slp, clock=clk)
    assert out.status == "done"
    assert ex.ops == [("press", "Key_L")]
    assert reads["n"] == 3          # polled until it saw the retract
    assert slp.total > 0            # it waited between polls (via the fake sleep)


def test_await_status_times_out():
    # The gear never retracts -> await times out and the sequence fails (keys released).
    ex, clk, slp = _runner_env()
    steps = (
        Step(PRESS, action="LandingGearToggle"),
        Step(AWAIT_STATUS, status_key="landing_gear", expect=False, seconds=2.0, poll=0.5,
             describe="gear didn't retract"),
    )
    out = run_sequence(steps, executor=ex, binds=_BINDS,
                       status=lambda: {"landing_gear": True}, sleep=slp, clock=clk)
    assert out.status == "failed"
    assert "gear didn't retract" in out.message
    assert ("release_all",) in ex.ops
    assert clk.t >= 2.0             # advanced past the timeout


def test_await_status_unavailable_times_out():
    # No status feed at all -> can't verify -> await times out (fail-safe, never assumes success).
    ex, clk, slp = _runner_env()
    steps = (Step(AWAIT_STATUS, status_key="docked", expect=False, seconds=1.0, poll=0.5),)
    out = run_sequence(steps, executor=ex, binds=_BINDS, status=None, sleep=slp, clock=clk)
    assert out.status == "failed"


def test_missing_binding_fails_softly():
    ex, clk, slp = _runner_env()
    steps = (Step(PRESS, action="NotBoundAction"),)
    out = run_sequence(steps, executor=ex, binds=_BINDS, status=lambda: {}, sleep=slp, clock=clk)
    assert out.status == "failed"
    assert "isn't in your Elite Dangerous bindings" in out.message
    assert ex.ops == [("release_all",)]


def test_unusable_binding_fails_softly():
    ex, clk, slp = _runner_env()
    binds = {"SetSpeed50": KeyBinding(action="SetSpeed50", key=None)}   # joystick-only / unbound
    steps = (Step(PRESS, action="SetSpeed50"),)
    out = run_sequence(steps, executor=ex, binds=binds, status=lambda: {}, sleep=slp, clock=clk)
    assert out.status == "failed"
    assert "no keyboard binding" in out.message.lower()


def test_abort_flag_stops_sequence_and_releases():
    # Abort fires after the first step -> the remaining steps never run and keys are released.
    ex, clk, slp = _runner_env()
    calls = {"n": 0}

    def abort() -> bool:
        calls["n"] += 1
        return calls["n"] > 1        # first check (before step 1) clear; then aborts

    steps = (
        Step(PRESS, action="SetSpeed50"),
        Step(PRESS, action="UseBoostJuice"),   # must NOT run
    )
    out = run_sequence(steps, executor=ex, binds=_BINDS, status=lambda: {}, sleep=slp,
                       clock=clk, abort=abort)
    assert out.status == "aborted"
    assert ex.ops == [("press", "Key_2"), ("release_all",)]   # boost never pressed


def test_abort_during_wait_stops():
    # Abort mid-wait: the WAIT is chunked so the abort flag is honoured before it completes.
    ex, clk, slp = _runner_env()
    calls = {"n": 0}

    def abort() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    steps = (Step(WAIT, seconds=5.0), Step(PRESS, action="SetSpeed50"))
    out = run_sequence(steps, executor=ex, binds=_BINDS, status=lambda: {}, sleep=slp,
                       clock=clk, abort=abort)
    assert out.status == "aborted"
    assert ("press", "Key_2") not in ex.ops


# ---- KeybindCapability: the shipped `launch` sequence end-to-end ------------


def _launch_status(gear: bool, *, mode: str = "mainship") -> dict:
    return {"in_danger": False, "being_interdicted": False, "game_mode": mode,
            "landing_gear": gear}


class _Status:
    """Mutable status snapshot the capability reads; the gear flag flips when the sequence
    presses the toggle so the final await_status is satisfiable."""

    def __init__(self) -> None:
        self.gear = True
        self.mode = "mainship"

    def __call__(self) -> dict:
        return _launch_status(self.gear, mode=self.mode)


def _launch_cap(status: _Status):
    """A capability with the real `launch` macro allowlisted, a recording executor, and an
    instant fake clock+sleep. The executor flips the shared status' gear flag on the gear
    toggle so the await verifies a real state change."""
    clk = FakeClock()
    slp = FakeSleep(clk)
    ex = RecordingExecutor()

    real_press = ex.press

    def press(binding):
        real_press(binding)
        if binding.key == "Key_L":      # LandingGearToggle -> gear retracts
            status.gear = False

    ex.press = press  # type: ignore[assignment]

    cfg = KeybindConfig(enabled=True, allowlist=("launch",))
    cap = KeybindCapability(binds=_BINDS, executor=ex, config=cfg,
                            status_snapshot=status, clock=clk, sleep=slp)
    return cap, ex


def test_launch_sequence_arms_then_confirms_and_runs():
    st = _Status()
    cap, ex = _launch_cap(st)
    # It's a consequential sequence -> arming does NOT fire.
    cap.new_turn()
    arm = cap.run_tool("launch_from_pad", {})
    assert "confirm" in arm.lower()
    assert ex.ops == []
    # Confirm on a new turn -> the whole sequence runs in order and the gear ends up retracted.
    cap.new_turn()
    done = cap.run_tool("confirm_keybind", {})
    kinds = [op[0] for op in ex.ops]
    assert kinds == ["press", "hold", "press", "press"]   # SetSpeed50, UpThrust hold, boost, gear
    assert st.gear is False
    assert "launched" in done.lower()


def test_launch_refused_when_gear_up():
    # Not on a pad (gear up) -> the require_status precondition refuses; no keys pressed.
    st = _Status()
    st.gear = False
    cap, ex = _launch_cap(st)
    cap.new_turn()
    cap.run_tool("launch_from_pad", {})
    cap.new_turn()
    msg = cap.run_tool("confirm_keybind", {})
    assert "couldn't complete" in msg.lower()
    # nothing pressed; only the defensive release_all is recorded
    assert [op for op in ex.ops if op[0] != "release_all"] == []


def test_launch_advertised_only_in_ship():
    st = _Status()
    cap, _ = _launch_cap(st)
    assert "launch_from_pad" in {t["name"] for t in cap.tools()}
    st.mode = "on_foot"
    assert "launch_from_pad" not in {t["name"] for t in cap.tools()}


def test_abort_tool_releases_and_clears_pending_sequence():
    st = _Status()
    cap, ex = _launch_cap(st)
    cap.new_turn()
    cap.run_tool("launch_from_pad", {})       # arm the sequence
    msg = cap.run_tool("abort_keybinds", {})
    assert ("release_all",) in ex.ops
    assert "abort" in msg.lower()
    # after abort, confirming finds nothing pending
    cap.new_turn()
    assert "nothing to confirm" in cap.run_tool("confirm_keybind", {}).lower()


def test_launch_not_in_default_allowlist():
    # Safety: the sequence macro ships OFF — the default allowlist is landing_gear only.
    assert KeybindConfig().allowlist == ("landing_gear",)
    cap = KeybindCapability(binds=_BINDS, executor=RecordingExecutor(),
                            config=KeybindConfig(enabled=True), status_snapshot=_Status())
    assert "launch_from_pad" not in {t["name"] for t in cap.tools()}
    assert "disallowed" in cap.run_tool("launch_from_pad", {}).lower()
