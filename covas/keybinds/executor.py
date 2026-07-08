"""Scancode-level key injection for Elite Dangerous (DESIGN §6).

Elite (like most DirectInput games) reads **hardware scancodes**, and frequently ignores the
plain virtual-key events that `keybd_event`/most automation libraries send. So the executor
drives the Win32 `SendInput` API with `KEYEVENTF_SCANCODE`, sending the Set-1 make/break
codes from `scancodes.py`. It supports the three primitives the design calls for:

    press(binding)          -> tap: modifiers down, key down, key up, modifiers up
    hold(binding, seconds)  -> key down, wait, key up (for "hold to charge" style actions)
    release(binding)        -> lift a key left down by a prior hold

plus `release_all()` — the hard-abort primitive: lift every key this executor is currently
holding, so a global abort can never strand a key down (which in ED would e.g. pin thrust).

The actual `SendInput` call lives behind an injectable `backend` so the capability can be
unit-tested end-to-end without firing real keystrokes; the default backend is Windows-only
and constructed lazily, so importing this module is safe on any platform.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable

from .binds import KeyBinding
from .scancodes import scancode_for

# SendInput constants.
_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008

# Default gap between key-down and key-up on a tap. A few ms mimics a human press; some
# games drop a press that goes down+up in the same input frame.
DEFAULT_TAP_MS = 40.0
# Safety ceiling on hold duration so a bad macro can't pin a key down indefinitely.
MAX_HOLD_SECONDS = 10.0


class ExecutorError(Exception):
    """Raised when a binding can't be injected (unmapped key token, backend unavailable)."""


# --- pointer-sized ctypes types, defined without ctypes.wintypes so this module imports on
#     non-Windows (wintypes import fails off-Windows). ULONG_PTR is pointer-sized.
_WORD = ctypes.c_uint16
_DWORD = ctypes.c_uint32
_LONG = ctypes.c_int32
_ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", _WORD), ("wScan", _WORD), ("dwFlags", _DWORD),
                ("time", _DWORD), ("dwExtraInfo", _ULONG_PTR))


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (("dx", _LONG), ("dy", _LONG), ("mouseData", _DWORD),
                ("dwFlags", _DWORD), ("time", _DWORD), ("dwExtraInfo", _ULONG_PTR))


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", _DWORD), ("wParamL", _WORD), ("wParamH", _WORD))


class _INPUTUNION(ctypes.Union):
    # Full union so sizeof(_INPUT) matches the real Win32 INPUT (sized to MOUSEINPUT);
    # SendInput rejects a wrong cbSize.
    _fields_ = (("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT))


class _INPUT(ctypes.Structure):
    _fields_ = (("type", _DWORD), ("u", _INPUTUNION))


class SendInputBackend:
    """The real key injector: one `SendInput` call per key event with a scancode. Windows
    only; constructing it off-Windows raises so the failure is loud and local."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ExecutorError("Key injection requires Windows (SendInput).")
        # use_last_error so get_last_error() reflects SendInput's failure code.
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]

    def _emit(self, scancode: int, extended: bool, keyup: bool) -> None:
        flags = _KEYEVENTF_SCANCODE
        if extended:
            flags |= _KEYEVENTF_EXTENDEDKEY
        if keyup:
            flags |= _KEYEVENTF_KEYUP
        ki = _KEYBDINPUT(wVk=0, wScan=scancode, dwFlags=flags, time=0, dwExtraInfo=0)
        inp = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            err = ctypes.get_last_error()
            raise ExecutorError(f"SendInput failed (sent={sent}, WinError={err}).")

    def key_down(self, scancode: int, extended: bool) -> None:
        self._emit(scancode, extended, keyup=False)

    def key_up(self, scancode: int, extended: bool) -> None:
        self._emit(scancode, extended, keyup=True)


def _resolve(binding: KeyBinding) -> tuple[tuple[int, bool], list[tuple[int, bool]]]:
    """(key_scancode, extended) + list of modifier (scancode, extended), or raise. A binding
    with no keyboard key, or any token we can't map to a scancode, is a hard error — better
    to refuse than press the wrong key."""
    if not binding.usable or binding.key is None:
        raise ExecutorError(binding.unusable_reason or f"{binding.action}: no keyboard key.")
    key = scancode_for(binding.key)
    if key is None:
        raise ExecutorError(f"No scancode for key '{binding.key}' ({binding.action}).")
    mods: list[tuple[int, bool]] = []
    for m in binding.modifiers:
        sc = scancode_for(m)
        if sc is None:
            raise ExecutorError(f"No scancode for modifier '{m}' ({binding.action}).")
        mods.append(sc)
    return key, mods


class KeyExecutor:
    """Turns a resolved `KeyBinding` into scancode key events via the backend. Thread-safe
    around the held-key set so `release_all()` (hard abort) can run from another thread."""

    def __init__(
        self,
        *,
        backend: object | None = None,
        sleep: Callable[[float], None] = time.sleep,
        tap_ms: float = DEFAULT_TAP_MS,
    ) -> None:
        # Backend is built lazily/injectably: tests pass a recorder, real runs get SendInput.
        self.backend = backend if backend is not None else SendInputBackend()
        self._sleep = sleep
        self._tap_s = max(0.0, tap_ms / 1000.0)
        self._lock = threading.Lock()
        self._down: set[tuple[int, bool]] = set()   # keys currently held (for release_all)

    def press(self, binding: KeyBinding) -> None:
        """Tap the binding: hold any modifiers, tap the key, release modifiers. Modifiers go
        down before the key and up after, matching how ED expects a chord."""
        key, mods = _resolve(binding)
        for sc, ext in mods:
            self.backend.key_down(sc, ext)
        try:
            self.backend.key_down(*key)
            if self._tap_s:
                self._sleep(self._tap_s)
            self.backend.key_up(*key)
        finally:
            for sc, ext in reversed(mods):
                self.backend.key_up(sc, ext)

    def hold(self, binding: KeyBinding, seconds: float) -> None:
        """Press the key (with modifiers held) for `seconds`, then release. Clamped to
        MAX_HOLD_SECONDS so a bad macro can't pin a key down. The key is tracked as held for
        the duration so a concurrent `release_all()` (abort) can lift it early."""
        seconds = max(0.0, min(float(seconds), MAX_HOLD_SECONDS))
        key, mods = _resolve(binding)
        for sc, ext in mods:
            self.backend.key_down(sc, ext)
            self._mark(sc, ext, down=True)
        self.backend.key_down(*key)
        self._mark(*key, down=True)
        try:
            self._sleep(seconds)
        finally:
            self._lift(*key)
            for sc, ext in reversed(mods):
                self._lift(sc, ext)

    def release(self, binding: KeyBinding) -> None:
        """Lift the binding's key (and modifiers) if this executor is holding it. A no-op if
        it isn't — safe to call defensively."""
        key, mods = _resolve(binding)
        self._lift(*key)
        for sc, ext in mods:
            self._lift(sc, ext)

    def release_all(self) -> None:
        """Hard-abort primitive: lift every key currently held. Never raises — an abort must
        always complete — so a backend error on one key is swallowed and the rest still lift."""
        with self._lock:
            held = list(self._down)
        for sc, ext in held:
            try:
                self.backend.key_up(sc, ext)
            except Exception:  # noqa: BLE001 — abort must not be blocked by one bad key
                pass
            with self._lock:
                self._down.discard((sc, ext))

    # -- held-key bookkeeping ----------------------------------------------------------
    def _mark(self, scancode: int, extended: bool, *, down: bool) -> None:
        with self._lock:
            if down:
                self._down.add((scancode, extended))
            else:
                self._down.discard((scancode, extended))

    def _lift(self, scancode: int, extended: bool) -> None:
        with self._lock:
            held = (scancode, extended) in self._down
        if held:
            self.backend.key_up(scancode, extended)
            self._mark(scancode, extended, down=False)
