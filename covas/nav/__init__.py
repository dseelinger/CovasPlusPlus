"""Navigation / outfitting lookups (find-closest-module feature).

Two data sources, deliberately split (see the build prompt + DESIGN §3):

  * Module TAXONOMY — a bundled static table (`modules.py`). Names/sizes/mounts/ratings
    are baked in from the real EDCD/Spansh outfitting data, so the whole ask → confirm →
    cancel disambiguation dialog is OFFLINE, fast, and unit-testable. No network for
    understanding what the Commander asked for.
  * Station LOCATION — Spansh's live station search (`closest.py`). Only touched AFTER a
    module is resolved and confirmed, to find the nearest station that actually sells it.

`clipboard.py` copies the resulting SYSTEM name so the Commander can paste it into the
galaxy map. Everything I/O-bound (HTTP, clipboard) is injected so tests stay hermetic.
"""
from .modules import (Ambiguous, NeedAttrs, Resolved, Unknown, resolve)
from .closest import (ClosestResult, NavError, RequestsHttp, find_closest_module)
from .clipboard import copy
from .location import current_system_from_journal
from .carrier import (CarrierInfo, carrier_from_journals, squadron_name_from_journals)

__all__ = [
    "Ambiguous",
    "CarrierInfo",
    "ClosestResult",
    "NavError",
    "NeedAttrs",
    "RequestsHttp",
    "Resolved",
    "Unknown",
    "carrier_from_journals",
    "copy",
    "current_system_from_journal",
    "find_closest_module",
    "resolve",
    "squadron_name_from_journals",
]
