"""Ship-action batches (issue #29 registry seam).

Importing this package registers every shipped macro into `keybinds.registry` — each batch
is its own module, imported here for its registration side effect. A Phase-1 action batch
(#30–#35) adds a module and one import line here; `KeybindCapability` needs no edit.
"""
from __future__ import annotations

from . import ship           # noqa: F401 — imported for the register() side effect
from . import ship_systems   # noqa: F401 — Tier-1 ship-systems batch (#31)
from . import flight         # noqa: F401 — Tier-1 flight/nav batch (#30)
from . import panels         # noqa: F401 — Tier-1 panels/UI/fire-groups batch (#32)
from . import on_foot        # noqa: F401 — Odyssey on-foot batch (#34)
from . import srv            # noqa: F401 — SRV / buggy batch (#35)

__all__ = ["ship", "ship_systems", "flight", "panels", "on_foot", "srv"]
