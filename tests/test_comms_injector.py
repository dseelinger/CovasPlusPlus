"""Unit tests for the clipboard-paste text injector (issue #49).

Offline + hermetic: a recording fake executor and a fake clipboard writer, so no real key is
pressed and clip.exe is never spawned. Asserts the chosen mechanism — set clipboard, then paste
(Ctrl+V), then commit (Enter) — and that failures normalise to InjectorError (fail-soft).
"""
from __future__ import annotations

import pytest

from covas.comms.injector import (ClipboardTextInjector, InjectorError,
                                  PASTE_BINDING, SEND_BINDING)
from covas.keybinds.executor import ExecutorError


class _RecordingExecutor:
    def __init__(self, fail_on: str | None = None) -> None:
        self.pressed: list[str] = []
        self._fail_on = fail_on          # action token to raise on

    def press(self, binding) -> None:
        if binding.action == self._fail_on:
            raise ExecutorError(f"boom on {binding.action}")
        self.pressed.append(binding.action)


def _inj(executor, copy=None, settle=0.0):
    sleeps: list[float] = []
    inj = ClipboardTextInjector(executor=executor, copy=copy,
                                sleep=sleeps.append, settle=settle)
    return inj, sleeps


# --- the paste-based injection mechanism -----------------------------------

def test_inject_copies_then_pastes():
    ex = _RecordingExecutor()
    copied: list[str] = []
    inj, _ = _inj(ex, copy=copied.append)
    inj.inject("o7 commander")
    assert copied == ["o7 commander"]                 # clipboard set first
    assert ex.pressed == [PASTE_BINDING.action]       # then Ctrl+V


def test_paste_binding_is_ctrl_v():
    assert PASTE_BINDING.key == "Key_V"
    assert PASTE_BINDING.modifiers == ("Key_LeftControl",)


def test_send_presses_enter():
    ex = _RecordingExecutor()
    inj, _ = _inj(ex)
    inj.send()
    assert ex.pressed == [SEND_BINDING.action]
    assert SEND_BINDING.key == "Key_Enter"


def test_settle_waits_between_copy_and_paste():
    ex = _RecordingExecutor()
    inj, sleeps = _inj(ex, copy=lambda _t: None, settle=0.2)
    inj.inject("hi")
    assert sleeps == [0.2]


def test_no_settle_does_not_sleep():
    ex = _RecordingExecutor()
    inj, sleeps = _inj(ex, copy=lambda _t: None, settle=0.0)
    inj.inject("hi")
    assert sleeps == []


# --- fail-soft: everything normalises to InjectorError ---------------------

def test_clipboard_failure_raises_injector_error_before_pressing():
    ex = _RecordingExecutor()

    def boom(_text):
        raise RuntimeError("clip.exe missing")

    inj, _ = _inj(ex, copy=boom)
    with pytest.raises(InjectorError):
        inj.inject("hi")
    assert ex.pressed == []                            # nothing pasted if the copy failed


def test_paste_executor_failure_raises_injector_error():
    ex = _RecordingExecutor(fail_on=PASTE_BINDING.action)
    inj, _ = _inj(ex, copy=lambda _t: None)
    with pytest.raises(InjectorError):
        inj.inject("hi")


def test_send_executor_failure_raises_injector_error():
    ex = _RecordingExecutor(fail_on=SEND_BINDING.action)
    inj, _ = _inj(ex)
    with pytest.raises(InjectorError):
        inj.send()
