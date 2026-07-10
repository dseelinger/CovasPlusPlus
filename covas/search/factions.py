"""Canonical minor-faction / BGS-state vocabulary (baked from the live Spansh API, 2026-07).

The faction STATES Spansh's `controlling_minor_faction_state` filter accepts and its results
emit — the states a Commander searches for by voice ("systems at war", "anywhere in boom").
Shared by the minor-factions and misc capabilities.

Faction NAMES are deliberately NOT a vocabulary: there are ~90k minor factions, so a spoken
faction name is passed through as free text and validated by the search itself (no results ->
"couldn't find it"), never invented in output — the result system name always comes from
Spansh. Only the finite STATE set is validated here.
"""
from __future__ import annotations

from . import vocab

# BGS states, exactly as Spansh spells them. "None" (no active state) is a real value but not a
# useful search target, so it's excluded from the spoken vocabulary.
FACTION_STATES = ("Blight", "Boom", "Bust", "Civil Liberty", "Civil Unrest", "Civil War",
                  "Drought", "Election", "Expansion", "Famine", "Infrastructure Failure",
                  "Investment", "Lockdown", "Natural Disaster", "Outbreak", "Pirate Attack",
                  "Public Holiday", "Retreat", "Terrorist Attack", "War")

_STATE_ALIASES = {
    "war": "War", "wars": "War", "at war": "War",
    "civil war": "Civil War", "civil wars": "Civil War",
    "boom": "Boom", "booming": "Boom",
    "bust": "Bust", "famine": "Famine", "outbreak": "Outbreak", "plague": "Outbreak",
    "election": "Election", "elections": "Election",
    "lockdown": "Lockdown", "expansion": "Expansion", "expanding": "Expansion",
    "retreat": "Retreat", "investment": "Investment",
    "infrastructure failure": "Infrastructure Failure", "damaged": "Infrastructure Failure",
    "natural disaster": "Natural Disaster", "disaster": "Natural Disaster",
    "civil unrest": "Civil Unrest", "unrest": "Civil Unrest",
    "terrorist attack": "Terrorist Attack", "terrorism": "Terrorist Attack",
    "pirate attack": "Pirate Attack", "blight": "Blight", "drought": "Drought",
    "public holiday": "Public Holiday", "holiday": "Public Holiday",
    "civil liberty": "Civil Liberty",
}


def resolve_state(spoken) -> str | None:
    return vocab.resolve(FACTION_STATES, spoken, aliases=_STATE_ALIASES)


def nearest_state(spoken) -> str | None:
    return vocab.nearest(FACTION_STATES, spoken, aliases=_STATE_ALIASES)
