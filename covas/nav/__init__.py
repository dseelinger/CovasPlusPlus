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
from .modules import (Ambiguous, NeedAttrs, Resolved, Unknown, MODULE_NAMES, resolve)
from .module_index import ModuleIndex
from .ships import (AmbiguousShip, ResolvedShip, UnknownShip, SHIP_NAMES, resolve_ship)
from .ship_index import ShipIndex
from .ship_specs import Spec, get_spec, has_spec, summary as ship_spec_summary
from .closest import (ClosestResult, NavError, RequestsHttp, find_closest_module)
from .ship_search import find_closest_ship
from .clipboard import copy
from .location import current_system_from_journal
from .carrier import (CarrierInfo, carrier_from_journals, squadron_name_from_journals)

__all__ = [
    "Ambiguous",
    "AmbiguousShip",
    "CarrierInfo",
    "ClosestResult",
    "MODULE_NAMES",
    "ModuleIndex",
    "NavError",
    "NeedAttrs",
    "RequestsHttp",
    "Resolved",
    "ResolvedShip",
    "SHIP_NAMES",
    "ShipIndex",
    "Spec",
    "Unknown",
    "UnknownShip",
    "carrier_from_journals",
    "copy",
    "current_system_from_journal",
    "find_closest_module",
    "find_closest_ship",
    "get_spec",
    "has_spec",
    "resolve",
    "resolve_ship",
    "ship_spec_summary",
    "squadron_name_from_journals",
]
