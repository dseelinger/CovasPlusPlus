"""Ship -> landing-pad-size lookup (issue #117).

Pure, offline table mapping an Elite Dangerous ship's INTERNAL journal symbol (the
`Loadout`/`LoadGame` `Ship` field, e.g. "sidewinder", "python", "federation_corvette" —
stable and non-localized, unlike the display name kept in `EDContext.ship`) to the smallest
landing pad it needs: "S" / "M" / "L". This backs the nav "Match Current Ship Size" pad
option: instead of a static config value, the search resolves a live minimum pad from the
Commander's ACTUAL current ship.

Pad sizes nest downward in Elite Dangerous — an L-pad station also has M and S pads, and an
outpost's largest is M (with S too); there's no "large-only" station. So "a station fits my
ship" reduces to "the station's largest pad >= my ship's size", which is exactly the MINIMUM
`pad_size` contract `find_closest_capability._pad_constraint` already implements. This module
only has to resolve "match" to the right S/M/L letter.

An unrecognized/newly-released symbol returns None so the caller applies the conservative
fallback (Large — a Large pad fits any ship, so a search never sends the Commander somewhere
they can't dock). Never raises on an unknown symbol.
"""
from __future__ import annotations

# Large-pad ships. Pad sizes don't always track "ship class" — e.g. the Orca and Type-7/8
# transporters are Large despite reading as "medium-sized" hulls.
_LARGE = frozenset({
    "anaconda",
    "federation_corvette",     # Federal Corvette
    "cutter",                  # Imperial Cutter
    "type9",                   # Type-9 Heavy
    "type9_military",          # Type-10 Defender
    "belugaliner",             # Beluga Liner
    "type7",                   # Type-7 Transporter
    "type8",                   # Type-8 Transporter
    "orca",                    # Orca
    "panthermkii",             # Panther Clipper MkII
})

# Medium-pad ships.
_MEDIUM = frozenset({
    "asp", "asp_scout",                                 # Asp Explorer / Scout
    "python", "python_nx",                              # Python / Python MkII
    "krait_mkii", "krait_light",                        # Krait MkII / Phantom
    "federation_dropship", "federation_dropship_mkii",  # Fed Dropship / Federal Assault Ship
    "federation_gunship",                                # Federal Gunship
    "vulture",
    "ferdelance",                                        # Fer-de-Lance
    "mamba",
    "empire_trader",                                     # Imperial Clipper
    "type6",                                             # Type-6 Transporter
    "independant_trader",                                 # Keelback
    "dolphin",
    "cobramkiv", "cobramkv",                             # Cobra MkIV / MkV
    "typex", "typex_2", "typex_3",                       # Alliance Chieftain/Crusader/Challenger
    "diamondbackxl",                                      # Diamondback Explorer
    "mandalay",
    "corsair",
})

# Small-pad ships.
_SMALL = frozenset({
    "sidewinder",
    "eagle", "empire_eagle",                # Eagle / Imperial Eagle
    "hauler",
    "adder",
    "viper", "viper_mkiv",                  # Viper MkIII / MkIV
    "cobramkiii",                           # Cobra MkIII
    "diamondback",                          # Diamondback Scout
    "empire_courier",                       # Imperial Courier
})

# NOTE: a handful of the newest hulls (Type-11 Prospector, and the PowerPlay 2.0 additions —
# Kestrel Mk II, Caspian Explorer, Lynx Highliner) are intentionally left OUT of this table:
# their pad requirement isn't confidently verified here, and an unmapped symbol safely falls
# back to Large (never sends the Commander to a pad too small). Add them once confirmed.

_SIZE_BY_SYMBOL: dict[str, str] = {
    **{s: "L" for s in _LARGE},
    **{s: "M" for s in _MEDIUM},
    **{s: "S" for s in _SMALL},
}


def ship_pad_size(symbol: str | None) -> str | None:
    """The smallest landing pad the named ship needs ("S"/"M"/"L"), or None when `symbol` is
    missing/unrecognized — the caller applies the conservative Large fallback. Matching is
    case-insensitive on ED's internal ship symbol (never the localized display name)."""
    if not symbol:
        return None
    return _SIZE_BY_SYMBOL.get(str(symbol).strip().lower())
