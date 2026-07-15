"""Ship-action batches (issue #29 registry seam).

Importing this package registers every shipped macro into `keybinds.registry` — each batch
is its own module, imported here for its registration side effect. A Phase-1 action batch
(#30–#35) adds a module and one import line here; `KeybindCapability` needs no edit.
"""
from __future__ import annotations

from . import ship  # noqa: F401 — imported for the register() side effect

__all__ = ["ship"]
