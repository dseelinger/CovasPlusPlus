"""Canonical station-search vocabulary (baked from the live Spansh API, 2026-07).

Station TYPES and SERVICES exactly as Spansh's `stations/search` accepts and emits them —
verified against the live API (an unknown value is silently ignored, so these were confirmed
by watching a filter actually narrow results, not by trusting the docs). Shared by the
stations and structure ('signals') capabilities, which both filter on the station `type`; a
type Spansh doesn't track resolves to the nearest one it does, never invented.

`resolve_type` / `resolve_service` do the loose spoken -> canonical mapping via the shared
`vocab` matcher.
"""
from __future__ import annotations

from . import vocab

# Fleet carriers ("Drake-Class Carrier") are a real station type but are handled by the
# carrier toggle (dropped from station results by default as transient), not offered as a
# type filter value — so they're deliberately absent here.
STATION_TYPES = ("Coriolis Starport", "Ocellus Starport", "Orbis Starport", "Outpost",
                 "Planetary Outpost", "Planetary Port", "Settlement", "Mega ship",
                 "Asteroid base")

# The services a Commander is likely to ask for by voice (Spansh service names, verified). Not
# the full internal list (Dock/Autodock/Flight Controller/etc. are noise for a voice search).
SERVICES = ("Market", "Outfitting", "Shipyard", "Repair", "Refuel", "Restock",
            "Universal Cartographics", "Vista Genomics", "Bartender", "Search and Rescue",
            "Missions", "Material Trader", "Technology Broker", "Interstellar Factors Contact",
            "Black Market", "Crew Lounge", "Redemption Office")

_TYPE_ALIASES = {
    "coriolis": "Coriolis Starport", "ocellus": "Ocellus Starport", "orbis": "Orbis Starport",
    "starport": "Coriolis Starport", "outpost": "Outpost",
    "planetary outpost": "Planetary Outpost", "planetary port": "Planetary Port",
    "surface port": "Planetary Port", "settlement": "Settlement", "settlements": "Settlement",
    "odyssey settlement": "Settlement",
    "megaship": "Mega ship", "mega ship": "Mega ship", "megaships": "Mega ship",
    "asteroid base": "Asteroid base", "asteroid station": "Asteroid base",
}
_SERVICE_ALIASES = {
    "outfit": "Outfitting", "outfitter": "Outfitting",
    "shipyard": "Shipyard", "buy ships": "Shipyard",
    "market": "Market", "commodities": "Market", "commodity market": "Market",
    "material trader": "Material Trader", "materials trader": "Material Trader",
    "tech broker": "Technology Broker", "technology broker": "Technology Broker",
    "interstellar factors": "Interstellar Factors Contact", "factors": "Interstellar Factors Contact",
    "ifc": "Interstellar Factors Contact",
    "black market": "Black Market", "smuggling": "Black Market",
    "genomics": "Vista Genomics", "vista genomics": "Vista Genomics",
    "repair": "Repair", "refuel": "Refuel", "restock": "Restock",
    "cartographics": "Universal Cartographics", "universal cartographics": "Universal Cartographics",
    "search and rescue": "Search and Rescue", "sar": "Search and Rescue",
}


def resolve_type(spoken) -> str | None:
    return vocab.resolve(STATION_TYPES, spoken, aliases=_TYPE_ALIASES)


def nearest_type(spoken) -> str | None:
    return vocab.nearest(STATION_TYPES, spoken, aliases=_TYPE_ALIASES)


def resolve_service(spoken) -> str | None:
    return vocab.resolve(SERVICES, spoken, aliases=_SERVICE_ALIASES)


def nearest_service(spoken) -> str | None:
    return vocab.nearest(SERVICES, spoken, aliases=_SERVICE_ALIASES)
