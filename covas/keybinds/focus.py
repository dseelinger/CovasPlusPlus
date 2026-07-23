"""Bring the Elite Dangerous window to the foreground so injected input can't misfire (#105).

COVAS++ injects keystrokes (`covas/keybinds/executor.py`) and pastes comms text
(`covas/comms/injector.py`) into **whatever window currently owns OS focus** — nothing today
checks that ED is actually frontmost. If focus has drifted (you glanced at the control panel, a
browser stole foreground, you alt-tabbed) a spoken "landing gear" lands in the wrong window.
This module makes injection deterministic by giving COVAS the ability to foreground ED.

It mirrors the executor's shape on purpose:
  * pure Win32 via `ctypes.WinDLL` (`user32` for the window calls, `kernel32` to resolve a PID's
    image name — both are core Windows DLLs, **no new dependency**), constructed lazily so
    importing this module is safe on any platform;
  * the real Win32 calls live behind an INJECTABLE `backend`, so the default `pytest` run drives
    a fake enumerator and never touches a real window.

Three primitives:
    find_ed_window()   -> hwnd | None   # full EnumWindows sweep, match by PROCESS image name
    is_foreground()    -> bool          # HOT PATH — resolves only GetForegroundWindow()'s PID,
                                        #   NEVER enumerates
    ensure_foreground()-> bool          # no-op when ED is already frontmost, else find + raise

**Why the hot-path split matters (not an optimisation — a requirement).** Auto-focus runs
before every deliberate keybind macro and every comms send; the common case is "you're flying,
ED is focused." `is_foreground()` must stay a couple of syscalls (one foreground-PID compare)
so that case costs nothing — a naive `ensure_foreground()` that enumerated every call would tax
every keypress. Only when ED is NOT already in front do we fall through to the full sweep and
the foreground-lock dance.

**The foreground lock.** `SetForegroundWindow` silently fails (flashes the taskbar instead of
raising) unless the caller satisfies Windows' foreground-lock rules — roughly "you got the most
recent user input", which a background PTT-triggered app does not. The robust unlock is the
**AttachThreadInput dance**: briefly share the current foreground thread's input queue so
Windows treats our `SetForegroundWindow` as coming from the focused app itself. We restore a
minimised window first, and always detach in a `finally`. A stray `ALT` tap immediately before
`SetForegroundWindow` (synthesised via the executor's `SendInput`) is an equally valid unlock
and is kept documented as the fallback — we prefer AttachThreadInput because it doesn't leak a
keypress into whatever is currently focused.
"""
from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

# ED's process image name (the single-instance guard and the VR toggle script key on this too)
# and a title substring used only as a fallback matcher — robust to title localisation. Compared
# case-insensitively.
ELITE_IMAGE = "elitedangerous64.exe"
ELITE_TITLE_HINT = "elite - dangerous"

# ShowWindow / process-access constants.
_SW_RESTORE = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class WindowFocuserError(Exception):
    """Raised when the real Win32 backend can't be built (off-Windows). Mirrors
    `ExecutorError`: callers catch it and simply leave the focus feature absent."""


class Win32Backend:
    """The real focuser backend: thin wrappers over `user32`/`kernel32`. Windows only;
    constructing it off-Windows raises so the failure is loud and local (the app catches it and
    leaves focus unavailable). Every method is a syscall or two — no policy lives here, so the
    `WindowFocuser` above it is fully unit-testable against a fake implementing the same surface."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise WindowFocuserError("Window focus requires Windows (user32/SetForegroundWindow).")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        # WNDENUMPROC is Windows-only (ctypes.WINFUNCTYPE doesn't exist off-Windows), so the type
        # is built here, inside the Windows-guarded ctor, not at module import.
        self._ENUMPROC = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        self._user32.GetForegroundWindow.restype = ctypes.c_void_p
        self._user32.IsWindowVisible.argtypes = (ctypes.c_void_p,)
        self._user32.IsIconic.argtypes = (ctypes.c_void_p,)
        self._user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
        self._user32.BringWindowToTop.argtypes = (ctypes.c_void_p,)
        self._user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)

    # -- foreground / enumeration -----------------------------------------------------
    def foreground_window(self) -> int | None:
        return self._user32.GetForegroundWindow() or None

    def enum_windows(self) -> list[int]:
        """Every VISIBLE top-level window handle. The one place we sweep — only reached from
        `find_ed_window()`, never from the `is_foreground()` hot path."""
        out: list[int] = []

        def _cb(hwnd, _lparam):  # noqa: ANN001 — ctypes callback
            if self._user32.IsWindowVisible(hwnd):
                out.append(hwnd)
            return True

        self._user32.EnumWindows(self._ENUMPROC(_cb), 0)
        return out

    def window_pid(self, hwnd: int) -> int | None:
        pid = ctypes.c_uint32(0)
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None

    def window_thread(self, hwnd: int) -> int:
        """The thread id that owns `hwnd` (for AttachThreadInput). GetWindowThreadProcessId's
        return value is the thread id; passing NULL for the PID out-param is fine."""
        return int(self._user32.GetWindowThreadProcessId(hwnd, None))

    def current_thread(self) -> int:
        return int(self._kernel32.GetCurrentThreadId())

    def window_title(self, hwnd: int) -> str:
        length = int(self._user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def image_name(self, pid: int) -> str | None:
        """The base image name (e.g. `EliteDangerous64.exe`) for `pid`, or None if it can't be
        opened. Uses PROCESS_QUERY_LIMITED_INFORMATION so it works against another user's/elevated
        process without needing full rights."""
        handle = self._kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = ctypes.c_uint32(260)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = self._kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size))
            if not ok:
                return None
            return buf.value.rsplit("\\", 1)[-1]
        finally:
            self._kernel32.CloseHandle(handle)

    # -- the foreground-lock dance primitives -----------------------------------------
    def is_iconic(self, hwnd: int) -> bool:
        return bool(self._user32.IsIconic(hwnd))

    def restore(self, hwnd: int) -> None:
        self._user32.ShowWindow(hwnd, _SW_RESTORE)

    def attach_thread_input(self, from_thread: int, to_thread: int, attach: bool) -> bool:
        return bool(self._user32.AttachThreadInput(from_thread, to_thread, bool(attach)))

    def bring_to_top(self, hwnd: int) -> None:
        self._user32.BringWindowToTop(hwnd)

    def set_foreground(self, hwnd: int) -> bool:
        return bool(self._user32.SetForegroundWindow(hwnd))


class WindowFocuser:
    """Locate and foreground the Elite Dangerous window, over an injectable `backend`.

    All Win32 lives in the backend, so this class holds only the policy (match by process image,
    hot-path guarantee, foreground-lock dance) and is unit-tested against a fake backend — the
    default `pytest` run never touches a real window.
    """

    def __init__(
        self,
        *,
        backend: object | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        # Built lazily/injectably: tests pass a fake enumerator, real runs get Win32Backend
        # (which raises off-Windows so the app can leave the feature absent).
        self._backend = backend if backend is not None else Win32Backend()
        self._log = log

    def _is_elite(self, hwnd: int | None) -> bool:
        """Does `hwnd` belong to the EliteDangerous64.exe process? Resolves the window's PID then
        that PID's image name — no enumeration, so it's safe on the hot path."""
        if not hwnd:
            return False
        pid = self._backend.window_pid(hwnd)
        if not pid:
            return False
        name = self._backend.image_name(pid)
        return bool(name) and name.lower() == ELITE_IMAGE

    def find_ed_window(self) -> int | None:
        """The visible top-level ED window, matched by PROCESS image name (robust to title
        localisation); a title-substring match is a fallback only. None if ED isn't running.
        This is the enumerating path — deliberately NOT called by `is_foreground()`."""
        title_fallback: int | None = None
        for hwnd in self._backend.enum_windows():
            if self._is_elite(hwnd):
                return hwnd
            if title_fallback is None:
                title = (self._backend.window_title(hwnd) or "").lower()
                if ELITE_TITLE_HINT in title:
                    title_fallback = hwnd
        return title_fallback

    def is_foreground(self) -> bool:
        """True when ED already owns the foreground. HOT PATH: resolves only
        `GetForegroundWindow()`'s PID — it MUST NOT enumerate (that guarantee is asserted in the
        unit tests), so the common already-focused case costs one PID compare."""
        return self._is_elite(self._backend.foreground_window())

    def ensure_foreground(self) -> bool:
        """Make ED frontmost, returning True on success. A fast no-op when ED is already in front
        (via the `is_foreground()` hot path — no enumeration). Only when ED is NOT in front does
        it enumerate to find the window and run the foreground-lock dance. Never raises — a focus
        failure must not break the voice loop; it returns False and the caller degrades."""
        try:
            if self.is_foreground():
                return True
            hwnd = self.find_ed_window()
            if not hwnd:
                return False
            return self._raise_to_front(hwnd)
        except Exception as e:  # noqa: BLE001 — focus is best-effort; never break the loop
            self._logline(f"ensure_foreground error: {e}")
            return False

    def _raise_to_front(self, hwnd: int) -> bool:
        """The foreground-lock unlock: restore if minimised, attach to the current foreground
        thread's input queue so SetForegroundWindow is honoured, bring the window up, then always
        detach. Returns whether ED ended up frontmost."""
        b = self._backend
        if b.is_iconic(hwnd):
            b.restore(hwnd)                      # un-minimise first, or it comes up hidden
        fg = b.foreground_window()
        fg_thread = b.window_thread(fg) if fg else 0
        me = b.current_thread()
        attached = False
        if fg_thread and fg_thread != me:
            attached = b.attach_thread_input(fg_thread, me, True)
        try:
            b.bring_to_top(hwnd)
            ok = bool(b.set_foreground(hwnd))
        finally:
            if attached:
                b.attach_thread_input(fg_thread, me, False)  # always detach
        # Trust an explicit success, but also re-check: SetForegroundWindow can report failure yet
        # still have raised the window (and vice-versa under the lock), so confirm via the hot path.
        return ok or self.is_foreground()

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
