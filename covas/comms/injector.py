"""Text-injection seam for in-game comms (issue #49, DESIGN §6).

THE TRICKY BIT of "send a message by voice": unlike a landing-gear toggle (one scancode), a
chat message is arbitrary CHARACTER input. Two candidate paths:

  1. per-character `SendInput` — replay the whole string as scancodes. Fragile: it has to map
     every character to a scancode + shift-state on the Commander's keyboard layout, and
     dead keys / AltGr / IME all break it.
  2. **clipboard-paste** — put the finished string on the Windows clipboard and paste it into
     ED's focused chat box with **Ctrl+V**. The OS handles the character mapping; we send two
     fixed keystrokes regardless of message content.

We take path 2 (clipboard-paste) — the more reliable one — reusing the existing clipboard
writer (`covas/nav/clipboard.py`, `clip.exe`, no new dependency). This module is that seam:
`ClipboardTextInjector.inject(text)` copies + pastes, and `.send()` presses Enter to commit.
Both the clipboard writer and the key executor are injected, so the whole thing is unit-tested
offline with a recording fake executor + a fake clipboard — no real input, no real clipboard.

`PASTE_BINDING` / `SEND_BINDING` are CONSTRUCTED `KeyBinding`s, not ED `.binds` action tokens:
Ctrl+V and Enter are OS/text-field keystrokes the game doesn't rebind, so we hardcode the key
tokens and let the shared scancode executor press them exactly like any other binding.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from ..keybinds.binds import KeyBinding
from ..keybinds.executor import ExecutorError
from ..nav import clipboard

# Ctrl+V (paste) and Enter (commit) as fixed keyboard chords. These are NOT read from the ED
# .binds file — they're OS/chat-field keystrokes — so we build the KeyBinding directly and the
# executor presses their scancodes (Key_V + Key_LeftControl modifier; Key_Enter).
PASTE_BINDING = KeyBinding(action="ClipboardPaste", key="Key_V",
                           modifiers=("Key_LeftControl",))
SEND_BINDING = KeyBinding(action="CommsSend", key="Key_Enter")

# Default pause after copying before pasting, and after focusing the box — the chat field needs
# a beat to take focus / accept the paste. Injected sleep makes this instant + deterministic in
# tests.
DEFAULT_SETTLE_SECONDS = 0.15


class InjectorError(Exception):
    """The text couldn't be injected — a clipboard write failure or a key-injection fault.
    Carries a Commander-facing message; the capability turns it into speech (fail-soft)."""


class ClipboardTextInjector:
    """Get composed text into ED's focused chat box via clipboard-paste, then commit it.

    Everything is injected so the default `pytest` run never spawns `clip.exe` or fires a real
    key:
      * `executor` — a `KeyExecutor` (or a recording fake) — SHARED with the keybind capability
        so a hard abort releases any key this pressed too.
      * `copy`     — the clipboard writer (defaults to `nav.clipboard.copy`; tests pass a fake).
      * `sleep`    — the settle wait (injected so tests don't actually wait).
    """

    def __init__(
        self,
        *,
        executor: object,
        copy: Optional[Callable[[str], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
        settle: float = DEFAULT_SETTLE_SECONDS,
    ) -> None:
        self._executor = executor
        self._copy = copy or clipboard.copy
        self._sleep = sleep
        self._settle = max(0.0, float(settle))

    def inject(self, text: str) -> None:
        """Put `text` on the clipboard and paste it (Ctrl+V) into the focused chat box. Raises
        `InjectorError` on a clipboard or key-injection failure so the capability can fail soft
        (it must never leave a half-typed message and never crash the loop)."""
        try:
            self._copy(str(text))
        except Exception as e:  # noqa: BLE001 — ClipboardError or any writer fault -> normalize
            raise InjectorError(f"couldn't set the clipboard: {e}") from e
        if self._settle:
            self._sleep(self._settle)
        try:
            self._executor.press(PASTE_BINDING)
        except ExecutorError as e:
            raise InjectorError(f"couldn't paste into the chat box: {e}") from e

    def send(self) -> None:
        """Press Enter to commit (send) the message currently in the chat box. Separate from
        `inject` so the capability's read-back gate controls exactly when the send fires."""
        try:
            self._executor.press(SEND_BINDING)
        except ExecutorError as e:
            raise InjectorError(f"couldn't send the message: {e}") from e
