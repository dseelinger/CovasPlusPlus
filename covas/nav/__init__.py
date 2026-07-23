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
from .carrier import CarrierInfo, carrier_from_journals, squadron_name_from_journals
from .clipboard import copy
from .closest import ClosestResult, NavError, RequestsHttp, find_closest_module
from .location import current_system_from_journal
from .module_index import ModuleIndex
from .modules import MODULE_NAMES, Ambiguous, NeedAttrs, Resolved, Unknown, resolve
from .ship_index import ShipIndex
from .ship_search import find_closest_ship
from .ship_specs import Spec, get_spec, has_spec
from .ship_specs import summary as ship_spec_summary
from .ships import SHIP_NAMES, AmbiguousShip, ResolvedShip, UnknownShip, resolve_ship

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
