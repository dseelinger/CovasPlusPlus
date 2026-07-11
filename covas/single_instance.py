"""Single-instance guard — stop two COVAS++ processes talking over each other.

Launching the app twice starts two voice loops that share the one microphone and the one set
of speakers, so they listen to the same push-to-talk and speak over each other. This guard
makes the second launch bail out with a clear message instead of running.

Implementation: a Windows named mutex via `ctypes` — no new dependency (CLAUDE.md prefers the
standard library). The OS destroys the mutex the moment the owning process exits, even on a
crash, so there's nothing to clean up the way a stale PID lock-file would leave behind. The
name has no ``Global\\`` prefix, so it's per-session: a second instance in the same Windows
session is blocked, but different users each get their own. On non-Windows (CI / dev boxes)
it's a no-op — the app targets Windows and the guard must never block a Linux test run.

Call `ensure_single_instance()` once, early, from an entry point (before building `App`, so a
rejected second launch doesn't waste time loading the Whisper model). Keep the returned lock
referenced for the process lifetime.
"""
from __future__ import annotations

import sys

# App-unique, session-local mutex name (no "Global\" prefix — see module docstring).
_MUTEX_NAME = "COVAS_Plus_Plus_SingleInstance_Mutex"
_ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Holds an OS-level lock proving this is the only running COVAS++ instance.

    `acquire()` returns False if another instance already holds the lock. Keep the object
    alive for the process lifetime — the lock is released when the handle closes (explicitly
    via `release()`, or by the OS on process exit)."""

    def __init__(self, name: str = _MUTEX_NAME) -> None:
        self._name = name
        self._handle = None

    def acquire(self) -> bool:
        """True if we now hold the single-instance lock, False if another instance has it.

        Fail-OPEN: anything unexpected (non-Windows, or a Win32 API failure) returns True, so
        the guard can only ever block a genuine second instance — it never wrongly stops the
        app from starting."""
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # HANDLE is pointer-wide; declare the prototype so a 64-bit handle isn't truncated.
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel32.CreateMutexW(None, False, self._name)
            err = ctypes.get_last_error()
            if not handle:
                return True                       # couldn't create it — don't block startup
            if err == _ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)      # another instance owns it; drop our handle
                return False
            self._handle = handle
            return True
        except Exception:  # noqa: BLE001 — a guard failure must never crash startup
            return True

    def release(self) -> None:
        """Release the lock (the OS also does this automatically on process exit)."""
        if self._handle is None:
            return
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(self._handle)
        except Exception:  # noqa: BLE001 — cleanup must never raise on exit
            pass
        self._handle = None


_ALREADY_RUNNING_MSG = (
    "COVAS++ is already running. Only one instance can run at a time — a second one would "
    "share your microphone and speakers and talk over the first. Close the other window "
    "first (quit it with Ctrl+Alt+Q)."
)


def ensure_single_instance(*, name: str = _MUTEX_NAME) -> SingleInstance:
    """Acquire the single-instance lock, or exit the process if another instance holds it.

    Returns the held lock (keep it referenced for the process lifetime). Call once, early — an
    entry point should invoke this BEFORE constructing `App`, so a rejected second launch fails
    fast without loading the Whisper model."""
    lock = SingleInstance(name)
    if lock.acquire():
        return lock
    print(_ALREADY_RUNNING_MSG, file=sys.stderr)
    sys.exit(1)
