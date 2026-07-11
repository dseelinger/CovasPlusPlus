"""Unit tests for the single-instance guard (offline, DESIGN §9).

A named mutex is a local OS object — no network, no cost — so these run in the default suite.
They use a test-specific mutex name so they can never collide with a real running app.
"""
from __future__ import annotations

import sys

import pytest

from covas.single_instance import SingleInstance, ensure_single_instance

_NAME = "COVAS_Plus_Plus_TEST_single_instance_pytest"


@pytest.mark.skipif(sys.platform != "win32", reason="named-mutex guard is Windows-only")
def test_second_instance_is_blocked_on_windows():
    first, second = SingleInstance(_NAME), SingleInstance(_NAME)
    try:
        assert first.acquire() is True         # first wins
        assert second.acquire() is False       # second is refused while the first holds it
    finally:
        first.release()
        second.release()


@pytest.mark.skipif(sys.platform != "win32", reason="named-mutex guard is Windows-only")
def test_release_lets_a_new_instance_acquire():
    name = _NAME + "_reacquire"
    first = SingleInstance(name)
    assert first.acquire() is True
    first.release()                            # freeing it should allow a fresh acquire
    second = SingleInstance(name)
    try:
        assert second.acquire() is True
    finally:
        second.release()


@pytest.mark.skipif(sys.platform != "win32", reason="named-mutex guard is Windows-only")
def test_ensure_exits_when_another_instance_holds_it():
    holder = SingleInstance(_NAME + "_ensure")
    assert holder.acquire() is True
    try:
        with pytest.raises(SystemExit) as ei:
            ensure_single_instance(name=_NAME + "_ensure")
        assert ei.value.code == 1
    finally:
        holder.release()


def test_ensure_returns_a_held_lock_when_free():
    lock = ensure_single_instance(name=_NAME + "_free")
    try:
        assert isinstance(lock, SingleInstance)
    finally:
        lock.release()


@pytest.mark.skipif(sys.platform == "win32", reason="off-Windows the guard is a deliberate no-op")
def test_guard_is_noop_off_windows():
    a, b = SingleInstance(_NAME), SingleInstance(_NAME)
    assert a.acquire() is True and b.acquire() is True   # never blocks on non-Windows
