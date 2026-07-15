"""In-game comms — compose + send Elite Dangerous chat text by voice (issue #49).

Unlike every other Tier-1 ship action (a single named scancode), sending a message needs
actual CHARACTER input. The reliable path on Windows is **clipboard-paste**: put the composed
message on the clipboard and paste it into ED's chat box with Ctrl+V, rather than synthesising
a per-character `SendInput` stream (fragile across keyboard layouts / dead keys / IME).

`injector.py` is that text-injection seam — a thin, injectable object the capability calls to
get the composed text into the focused chat field and commit it. The capability itself
(`covas/capabilities/comms_capability.py`) owns the outward-facing SAFETY: a mandatory
read-back-before-send gate so a garbled message never reaches strangers.
"""
from __future__ import annotations

from .injector import (PASTE_BINDING, SEND_BINDING, ClipboardTextInjector,
                       InjectorError)

__all__ = [
    "ClipboardTextInjector",
    "InjectorError",
    "PASTE_BINDING",
    "SEND_BINDING",
]
