"""Copy text (the resolved SYSTEM name) to the Windows clipboard.

Choice: shell out to the built-in `clip.exe` rather than add a `pyperclip` dependency.
Rationale — CLAUDE.md says stdlib-first and to justify any new dep; `clip.exe` ships with
Windows, needs no install, and the payload here is an ASCII system name (no Unicode-encoding
subtlety to worry about). It's injected into the capability as a plain `copy` callable, so
tests pass a fake and the default `pytest` never spawns a process or touches the real
clipboard.

`copy()` raises ClipboardError on failure; the caller treats a copy failure as non-fatal
(the system name is still spoken) — the clipboard is a convenience, not the answer.
"""
from __future__ import annotations

import subprocess


class ClipboardError(Exception):
    """The clipboard write failed (non-Windows host, clip.exe missing, or a spawn error)."""


def copy(text: str) -> None:
    """Put `text` on the Windows clipboard via clip.exe. `clip` reads stdin verbatim, so we
    pass the string with no trailing newline. Raises ClipboardError on any failure."""
    try:
        subprocess.run(
            ["clip"],
            input=str(text),
            text=True,
            check=True,
            # clip is a console app; hide any window flash and don't inherit stdout/stderr.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise ClipboardError(f"clipboard copy failed: {e}") from e
