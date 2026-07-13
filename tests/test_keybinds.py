"""Unit tests for the keybind binds parser, resolver, scancodes, and executor (DESIGN §6, §9).

All offline and hermetic: the .binds parser + preset resolver run against a sample fixture,
scancode lookups are pure, and the executor is driven through a recording fake backend so no
real keystrokes are injected. Real in-game injection is a manual on-hardware test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from covas.keybinds import (BindsError, KeyBinding, load_binds, parse_binds,
                            resolve_binds_file)
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
