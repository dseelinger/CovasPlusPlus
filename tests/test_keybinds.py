"""Unit tests for the keybind binds parser, resolver, scancodes, and executor (DESIGN §6, §9).

All offline and hermetic: the .binds parser + preset resolver run against a sample fixture,
scancode lookups are pure, and the executor is driven through a recording fake backend so no
real keystrokes are injected. Real in-game injection is a manual on-hardware test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from covas.keybinds import BindsError, KeyBinding, load_binds, parse_binds, resolve_binds_file
from covas.keybinds.binds import active_preset
from covas.keybinds.executor import ExecutorError, KeyExecutor
from covas.keybinds.scancodes import scancode_for

FIXTURES = Path(__file__).parent / "fixtures" / "keybinds"
SAMPLE = (FIXTURES / "Custom.4.0.binds").read_text(encoding="utf-8")


# --- parse_binds -----------------------------------------------------------

def test_keyboard_primary_binding_is_usable():
    binds = parse_binds(SAMPLE)
    lg = binds["LandingGearToggle"]
    assert lg.usable
    assert lg.key == "Key_L"
    assert lg.modifiers == ()
    assert lg.source == "Primary"
    assert lg.unusable_reason is None


def test_keyboard_binding_captures_modifiers():
    binds = parse_binds(SAMPLE)
    hsc = binds["HyperSuperCombination"]
    assert hsc.usable
    assert hsc.key == "Key_J"
    assert hsc.modifiers == ("Key_LeftShift",)


def test_keyboard_falls_back_to_secondary_when_primary_is_joystick():
    binds = parse_binds(SAMPLE)
    scoop = binds["ToggleCargoScoop"]
    assert scoop.usable
    assert scoop.key == "Key_Home"
    assert scoop.source == "Secondary"


def test_joystick_only_action_is_unusable():
    binds = parse_binds(SAMPLE)
    boost = binds["UseBoostJuice"]
    assert not boost.usable
    assert boost.key is None
    assert "no keyboard binding" in boost.unusable_reason


def test_unbound_action_is_unusable():
    binds = parse_binds(SAMPLE)
    assert not binds["SetSpeedZero"].usable


def test_non_action_scalars_are_skipped():
    binds = parse_binds(SAMPLE)
    assert "MouseXMode" not in binds
    assert "KeyboardLayout" not in binds


def test_parse_malformed_xml_is_empty_not_crash():
    assert parse_binds("<Root><LandingGearToggle>") == {}
    assert parse_binds("not xml at all") == {}


# --- binding preference (#29) ----------------------------------------------

# An action with a KEYBOARD key on BOTH slots — the only case where the preference changes
# which key is chosen (the shipped fixture never doubles up keyboard binds).
_BOTH_KB = ("<Root><ToggleButtonUpInput>"
            '<Primary Device="Keyboard" Key="Key_P" />'
            '<Secondary Device="Keyboard" Key="Key_S" />'
            "</ToggleButtonUpInput></Root>")


def test_prefer_primary_is_the_default():
    b = parse_binds(_BOTH_KB)["ToggleButtonUpInput"]
    assert b.key == "Key_P" and b.source == "Primary"


def test_prefer_secondary_picks_secondary_keyboard():
    b = parse_binds(_BOTH_KB, prefer="secondary")["ToggleButtonUpInput"]
    assert b.key == "Key_S" and b.source == "Secondary"


def test_prefer_secondary_falls_back_to_primary_when_secondary_absent():
    # LandingGearToggle has a keyboard key only on Primary — secondary-preference still finds it.
    b = parse_binds(SAMPLE, prefer="secondary")["LandingGearToggle"]
    assert b.key == "Key_L" and b.source == "Primary"


def test_prefer_primary_falls_back_to_secondary_when_primary_is_joystick():
    # ToggleCargoScoop's Primary is a joystick; primary-preference falls back to the Secondary key.
    b = parse_binds(SAMPLE, prefer="primary")["ToggleCargoScoop"]
    assert b.key == "Key_Home" and b.source == "Secondary"


def test_binding_preference_from_cfg():
    from covas.keybinds.binds import binding_preference
    assert binding_preference(None) == "primary"
    assert binding_preference({}) == "primary"
    assert binding_preference({"keybinds": {"binding_preference": "secondary"}}) == "secondary"
    assert binding_preference({"keybinds": {"binding_preference": "SECONDARY"}}) == "secondary"
    # Unrecognized values fall back to the safe default.
    assert binding_preference({"keybinds": {"binding_preference": "nonsense"}}) == "primary"


# --- active_preset + resolve_binds_file ------------------------------------

def test_active_preset_reads_first_nonempty_line():
    assert active_preset(FIXTURES) == "Custom"


def test_active_preset_missing_marker_returns_none(tmp_path):
    assert active_preset(tmp_path) is None


def test_resolve_uses_active_preset(tmp_path):
    (tmp_path / "StartPreset.4.start").write_text("MyPreset\n", encoding="utf-8")
    (tmp_path / "MyPreset.4.0.binds").write_text(SAMPLE, encoding="utf-8")
    resolved = resolve_binds_file({}, dir_=tmp_path)
    assert resolved.name == "MyPreset.4.0.binds"


def test_resolve_falls_back_to_old_start_marker(tmp_path):
    (tmp_path / "StartPreset.start").write_text("Legacy\n", encoding="utf-8")
    (tmp_path / "Legacy.4.0.binds").write_text(SAMPLE, encoding="utf-8")
    assert resolve_binds_file({}, dir_=tmp_path).name == "Legacy.4.0.binds"


def test_resolve_config_override_wins(tmp_path):
    custom = tmp_path / "my.binds"
    custom.write_text(SAMPLE, encoding="utf-8")
    cfg = {"keybinds": {"binds_file": str(custom)}}
    assert resolve_binds_file(cfg, dir_=tmp_path) == custom


def test_resolve_override_relative_to_bindings_dir(tmp_path):
    (tmp_path / "rel.binds").write_text(SAMPLE, encoding="utf-8")
    cfg = {"keybinds": {"binds_file": "rel.binds"}}
    assert resolve_binds_file(cfg, dir_=tmp_path) == tmp_path / "rel.binds"


def test_resolve_missing_override_raises(tmp_path):
    cfg = {"keybinds": {"binds_file": str(tmp_path / "nope.binds")}}
    with pytest.raises(BindsError):
        resolve_binds_file(cfg, dir_=tmp_path)


def test_resolve_no_preset_raises(tmp_path):
    with pytest.raises(BindsError):
        resolve_binds_file({}, dir_=tmp_path)


def test_resolve_preset_named_but_file_missing_raises(tmp_path):
    (tmp_path / "StartPreset.4.start").write_text("Ghost\n", encoding="utf-8")
    with pytest.raises(BindsError):
        resolve_binds_file({}, dir_=tmp_path)


def test_resolve_picks_highest_version_suffix(tmp_path):
    # Both .4.0 and .4.2 present -> take the newest.
    (tmp_path / "StartPreset.4.start").write_text("Custom\n", encoding="utf-8")
    (tmp_path / "Custom.4.0.binds").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "Custom.4.2.binds").write_text(SAMPLE, encoding="utf-8")
    assert resolve_binds_file({}, dir_=tmp_path).name == "Custom.4.2.binds"


def test_resolve_finds_newer_suffix_when_4_0_absent(tmp_path):
    # The exact bug: only Custom.4.2.binds exists (old code only looked for .4.0).
    (tmp_path / "StartPreset.4.start").write_text("Custom\n", encoding="utf-8")
    (tmp_path / "Custom.4.2.binds").write_text(SAMPLE, encoding="utf-8")
    assert resolve_binds_file({}, dir_=tmp_path).name == "Custom.4.2.binds"


def test_resolve_falls_back_to_unversioned_binds(tmp_path):
    (tmp_path / "StartPreset.4.start").write_text("Plain\n", encoding="utf-8")
    (tmp_path / "Plain.binds").write_text(SAMPLE, encoding="utf-8")
    assert resolve_binds_file({}, dir_=tmp_path).name == "Plain.binds"


def test_resolve_scoped_to_preset_ignores_others_and_nonnumeric(tmp_path):
    # A different preset's file and a non-numeric middle must NOT be picked for 'Custom'.
    (tmp_path / "StartPreset.4.start").write_text("Custom\n", encoding="utf-8")
    (tmp_path / "Default.4.2.binds").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "Custom.backup.binds").write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(BindsError):
        resolve_binds_file({}, dir_=tmp_path)


def test_load_binds_end_to_end(tmp_path):
    (tmp_path / "StartPreset.4.start").write_text("Custom\n", encoding="utf-8")
    (tmp_path / "Custom.4.0.binds").write_text(SAMPLE, encoding="utf-8")
    binds = load_binds({}, dir_=tmp_path)
    assert binds["LandingGearToggle"].key == "Key_L"


# --- scancodes -------------------------------------------------------------

def test_scancode_for_letter():
    assert scancode_for("Key_L") == (0x26, False)


def test_scancode_for_extended_key():
    sc, extended = scancode_for("Key_Home")
    assert sc == 0x47 and extended is True


def test_scancode_right_ctrl_is_extended():
    assert scancode_for("Key_RightControl") == (0x1D, True)


def test_scancode_case_insensitive():
    assert scancode_for("key_l") == (0x26, False)


def test_scancode_unknown_is_none():
    assert scancode_for("Key_Nope") is None
    assert scancode_for("") is None


# --- executor (fake backend, no real injection) ----------------------------

class _FakeBackend:
    """Records key events instead of calling SendInput."""
    def __init__(self) -> None:
        self.events: list[tuple[str, int, bool]] = []

    def key_down(self, scancode: int, extended: bool) -> None:
        self.events.append(("down", scancode, extended))

    def key_up(self, scancode: int, extended: bool) -> None:
        self.events.append(("up", scancode, extended))


def _executor():
    be = _FakeBackend()
    # sleep is a no-op so tests don't actually wait.
    return KeyExecutor(backend=be, sleep=lambda _s: None, tap_ms=0), be


def test_press_emits_down_then_up():
    ex, be = _executor()
    ex.press(KeyBinding(action="LandingGearToggle", key="Key_L"))
    assert be.events == [("down", 0x26, False), ("up", 0x26, False)]


def test_press_with_modifier_wraps_the_key():
    ex, be = _executor()
    ex.press(KeyBinding(action="X", key="Key_J", modifiers=("Key_LeftShift",)))
    assert be.events == [
        ("down", 0x2A, False),   # modifier down first
        ("down", 0x24, False),   # key down
        ("up", 0x24, False),     # key up
        ("up", 0x2A, False),     # modifier up last
    ]


def test_press_unusable_binding_raises():
    ex, _ = _executor()
    with pytest.raises(ExecutorError):
        ex.press(KeyBinding(action="UseBoostJuice", key=None))


def test_press_unmapped_key_raises():
    ex, _ = _executor()
    with pytest.raises(ExecutorError):
        ex.press(KeyBinding(action="X", key="Key_Nope"))


def test_hold_then_release_all_lifts_key():
    ex, be = _executor()
    ex.hold(KeyBinding(action="X", key="Key_L"), 0.01)
    # hold() releases on its own; nothing left held afterwards.
    assert ("down", 0x26, False) in be.events
    assert ("up", 0x26, False) in be.events


def test_release_all_lifts_a_stuck_key(monkeypatch):
    # Simulate a hold interrupted before its own release by never letting hold() finish its
    # release path: call release_all() while we've manually marked a key down.
    ex, be = _executor()
    ex._mark(0x26, False, down=True)      # pretend Key_L is being held
    ex.release_all()
    assert ("up", 0x26, False) in be.events
    assert ex._down == set()


def test_extended_flag_reaches_backend():
    ex, be = _executor()
    ex.press(KeyBinding(action="ToggleCargoScoop", key="Key_Home"))
    assert be.events == [("down", 0x47, True), ("up", 0x47, True)]


# --- press key tracking (#159) --------------------------------------------

def test_press_leaves_nothing_held():
    """A clean tap tracks its keys but lifts them all — `_down` is empty afterwards."""
    ex, _ = _executor()
    ex.press(KeyBinding(action="X", key="Key_J", modifiers=("Key_LeftShift",)))
    assert ex._down == set()


class _FlakyBackend(_FakeBackend):
    """A backend whose key_up for one scancode raises a bounded number of times, to model a
    transient injection fault mid-press."""
    def __init__(self, fail_up_scancode: int, fail_times: int) -> None:
        super().__init__()
        self._fail_sc = fail_up_scancode
        self._fail_times = fail_times

    def key_up(self, scancode: int, extended: bool) -> None:
        if scancode == self._fail_sc and self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("key_up failed")
        super().key_up(scancode, extended)


def test_press_tracks_keys_so_release_all_recovers_a_failed_keyup():
    # press() now records the key in _down BEFORE pressing it, so a key_up that fails mid-press
    # leaves the key tracked as held — a later hard abort can still lift it rather than stranding it.
    be = _FlakyBackend(fail_up_scancode=0x26, fail_times=1)   # Key_L up fails once
    ex = KeyExecutor(backend=be, sleep=lambda _s: None, tap_ms=0)
    with pytest.raises(RuntimeError):
        ex.press(KeyBinding(action="X", key="Key_L"))
    assert (0x26, False) in ex._down                          # stranded key IS tracked
    ex.release_all()                                          # backend healthy now
    assert ex._down == set()                                  # hard abort lifted it
    assert ("up", 0x26, False) in be.events


# --- abort-aware hold (#159) ----------------------------------------------

def test_hold_returns_early_when_release_all_lifts_the_key():
    # Model a concurrent hard abort: the injected sleep calls release_all() on the first poll
    # chunk, discarding the held key. hold()'s next poll sees it gone and stops promptly instead
    # of sleeping out the full (clamped) duration.
    be = _FakeBackend()
    holder: dict[str, KeyExecutor] = {}
    chunks: list[float] = []

    def sleep(s: float) -> None:
        chunks.append(s)
        if len(chunks) == 1:
            holder["ex"].release_all()

    ex = KeyExecutor(backend=be, sleep=sleep, tap_ms=0)
    holder["ex"] = ex
    ex.hold(KeyBinding(action="X", key="Key_L"), 5.0)
    assert len(chunks) <= 2                                   # NOT ~100 chunks of a 5s hold
    assert ex._down == set()
    assert ("up", 0x26, False) in be.events


def test_hold_returns_early_on_abort_predicate():
    # An injected abort predicate is polled between chunks; once it flips True, hold() stops and
    # its own release path lifts the key.
    be = _FakeBackend()
    aborted = {"v": False}
    chunks: list[float] = []

    def sleep(s: float) -> None:
        chunks.append(s)
        aborted["v"] = True

    ex = KeyExecutor(backend=be, sleep=sleep, tap_ms=0)
    ex.hold(KeyBinding(action="X", key="Key_L"), 5.0, abort=lambda: aborted["v"])
    assert len(chunks) == 1
    assert ex._down == set()
    assert ("up", 0x26, False) in be.events
