"""Canonical star-system slot vocabulary + a pure resolver (offline).

Every value here is the EXACT string Spansh's `systems/search` accepts and its results emit —
verified against the live API (2026-07), not the docs. This matters because Spansh SILENTLY
IGNORES an unknown filter value (it returns everything, no error), so a misheard or invented
value would quietly widen a search instead of failing. The capability therefore resolves each
spoken slot to one of these canonical values BEFORE building a query, and on no-match speaks a
templated correction instead — the hallucination guard, mirroring how `nav/modules.py`
resolves module names offline.

Powerplay reflects Powerplay 2.0 as it stands live: Zachary Hudson is GONE (Jerome Archer
took the Federation power), and Arissa Lavigny-Duval appears as "A. Lavigny-Duval". These were
read straight off Spansh result fields, not assumed.

`resolve_enum` does the loose spoken -> canonical mapping (exact-after-normalization, then a
short alias table for the way a Commander/Whisper actually says it, then fuzzy) via the shared
`vocab` matcher. Pure and offline — the LLM does the fuzzy *understanding*; this VALIDATES it
against the real vocab.
"""
from __future__ import annotations

from . import vocab

# --- canonical value sets (Spansh param name -> its accepted values) -----------------------
# Enum slots. Keyed by the exact Spansh filter param (== the registry slot.param), so the
# capability, help, and the query all speak one vocabulary.

ALLEGIANCES = ("Alliance", "Empire", "Federation", "Independent", "Guardian", "Thargoid")

GOVERNMENTS = ("Anarchy", "Communism", "Confederacy", "Cooperative", "Corporate", "Democracy",
               "Dictatorship", "Feudal", "Patronage", "Prison", "Prison Colony", "Theocracy",
               "None")

ECONOMIES = ("Agriculture", "Colony", "Extraction", "High Tech", "Industrial", "Military",
             "Refinery", "Service", "Terraforming", "Tourism", "None")

SECURITIES = ("Anarchy", "Low", "Medium", "High")

# Powerplay 2.0 powers, exactly as Spansh spells them (note the abbreviated Lavigny-Duval).
POWERS = ("A. Lavigny-Duval", "Aisling Duval", "Archon Delaine", "Denton Patreus",
          "Edmund Mahon", "Felicia Winters", "Jerome Archer", "Li Yong-Rui", "Nakato Kaine",
          "Pranav Antal", "Yuri Grom", "Zemina Torval")

POWER_STATES = ("Exploited", "Fortified", "Stronghold", "Unoccupied")


# Spansh param -> its canonical values. The single source the capability validates against and
# contributes to the help subsystem's failure-recovery vocabulary.
VOCAB: dict[str, tuple[str, ...]] = {
    "allegiance": ALLEGIANCES,
    "government": GOVERNMENTS,
    "primary_economy": ECONOMIES,
    "security": SECURITIES,
    "power": POWERS,
    "power_state": POWER_STATES,
}


# --- spoken aliases the way a Commander / Whisper actually says it --------------------------
# Only what fuzzy matching wouldn't already catch (abbreviations, last-name-only, mishears).
# Normalized on both sides at lookup, so case/punctuation/spacing don't matter here.
_ALIASES: dict[str, dict[str, str]] = {
    "allegiance": {
        "fed": "Federation", "feds": "Federation", "federal": "Federation",
        "imperial": "Empire", "imps": "Empire",
        "indie": "Independent", "independents": "Independent",
        "thargoids": "Thargoid", "guardians": "Guardian",
    },
    "power": {
        # last-name-only is how they're usually spoken
        "mahon": "Edmund Mahon", "delaine": "Archon Delaine", "patreus": "Denton Patreus",
        "torval": "Zemina Torval", "winters": "Felicia Winters", "archer": "Jerome Archer",
        "antal": "Pranav Antal", "kaine": "Nakato Kaine", "grom": "Yuri Grom",
        "aisling": "Aisling Duval", "duval": "Aisling Duval",
        "lavigny": "A. Lavigny-Duval", "arissa": "A. Lavigny-Duval",
        "arissa lavigny duval": "A. Lavigny-Duval", "lavigny duval": "A. Lavigny-Duval",
        "li yong rui": "Li Yong-Rui", "yong rui": "Li Yong-Rui", "sirius": "Li Yong-Rui",
    },
    "primary_economy": {
        "hightech": "High Tech", "high-tech": "High Tech", "tech": "High Tech",
        "mining": "Extraction", "farming": "Agriculture",
    },
}


def resolve_enum(param: str, spoken) -> str | None:
    """Map a loose spoken value to the canonical Spansh value for `param`, or None if it isn't
    one (strict — for building a query)."""
    if param not in VOCAB:
        return None
    return vocab.resolve(VOCAB[param], spoken, aliases=_ALIASES.get(param))


def nearest_enum(param: str, spoken) -> str | None:
    """The closest canonical value to an UNRESOLVED spoken term (lenient) — for a spoken 'did
    you mean…' correction. Always a real value from `VOCAB` or None."""
    if param not in VOCAB:
        return None
    return vocab.nearest(VOCAB[param], spoken, aliases=_ALIASES.get(param))
