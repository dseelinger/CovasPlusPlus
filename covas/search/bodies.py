"""Canonical body-search vocabulary (baked from the live Spansh API, 2026-07).

Body SUBTYPES and biological-signal LANDMARK subtypes exactly as Spansh's `bodies/search`
accepts and emits them — verified against the live API (an unknown value is silently ignored,
so these were confirmed by watching a filter actually NARROW results, not by trusting docs).

Two vocabularies power the body finder (`capabilities/body_search_capability.py`):

  * `BODY_SUBTYPES` — the planetary body types a Commander asks for by voice (Earth-like world,
    Ammonia world, Water world, the gas-giant classes, …). Stars are deliberately left out: the
    finder answers "nearest Earth-like / ammonia / water world", not "nearest K-type star".
  * `BIO_GENERA` — the Odyssey exobiology genera and their species, the Spansh `landmark_subtype`
    values that back "a body with biological signals of TYPE X". A Commander may say a whole genus
    ("Bacterium") — which expands to an OR over all its species — a single species ("Bacterium
    Aurasus"), or "any biological" (the union of every species). Spansh's enum filter takes a list
    of exact strings, so a spoken value must resolve to real ones or the search silently widens.

`resolve_subtype` / `resolve_bio_signal` do the loose spoken -> canonical mapping via the shared
`vocab` matcher; the `nearest_*` siblings feed a spoken "did you mean…" correction. Both only
ever return values from these tables — never an invention.
"""
from __future__ import annotations

from . import vocab

# Planetary body subtypes Spansh emits, minus stars (voice finder is about worlds/planets).
# Verified accepted: filtering `subtype` to any one of these narrows results to that type.
BODY_SUBTYPES = (
    "Earth-like world", "Ammonia world", "Water world", "Water giant",
    "High metal content world", "Metal-rich body", "Rocky body", "Rocky Ice world", "Icy body",
    "Class I gas giant", "Class II gas giant", "Class III gas giant", "Class IV gas giant",
    "Class V gas giant", "Gas giant with ammonia-based life", "Gas giant with water-based life",
    "Helium gas giant", "Helium-rich gas giant",
)

_SUBTYPE_ALIASES = {
    "earthlike": "Earth-like world", "earth like world": "Earth-like world",
    "earth like": "Earth-like world", "earth-like": "Earth-like world", "elw": "Earth-like world",
    "ammonia": "Ammonia world", "ammonia world": "Ammonia world", "aw": "Ammonia world",
    "water world": "Water world", "ww": "Water world", "water giant": "Water giant",
    "high metal content": "High metal content world", "hmc": "High metal content world",
    "high metal": "High metal content world", "metal rich": "Metal-rich body",
    "metal-rich": "Metal-rich body", "rocky": "Rocky body", "rocky world": "Rocky body",
    "rocky ice": "Rocky Ice world", "icy": "Icy body", "ice world": "Icy body",
    "icy world": "Icy body",
    "class 1 gas giant": "Class I gas giant", "class 2 gas giant": "Class II gas giant",
    "class 3 gas giant": "Class III gas giant", "class 4 gas giant": "Class IV gas giant",
    "class 5 gas giant": "Class V gas giant",
    "ammonia based life": "Gas giant with ammonia-based life",
    "water based life": "Gas giant with water-based life",
    "helium gas giant": "Helium gas giant", "helium rich gas giant": "Helium-rich gas giant",
}

# Odyssey exobiology genera -> their Spansh `landmark_subtype` species (live values, 2026-07).
# A genus query expands to an OR over all its species; a species query is that one string.
BIO_GENERA: dict[str, tuple[str, ...]] = {
    "Aleoida": ("Aleoida Arcus", "Aleoida Coronamus", "Aleoida Gravis", "Aleoida Laminiae",
                "Aleoida Spica"),
    "Bacterium": ("Bacterium Acies", "Bacterium Alcyoneum", "Bacterium Aurasus",
                  "Bacterium Bullaris", "Bacterium Cerbrus", "Bacterium Informem",
                  "Bacterium Nebulus", "Bacterium Omentum", "Bacterium Scopulum",
                  "Bacterium Tela", "Bacterium Verrata", "Bacterium Vesicula", "Bacterium Volu"),
    "Cactoida": ("Cactoida Cortexum", "Cactoida Lapis", "Cactoida Peperatis",
                 "Cactoida Pullulanta", "Cactoida Vermis"),
    "Clypeus": ("Clypeus Lacrimam", "Clypeus Margaritus", "Clypeus Speculumi"),
    "Concha": ("Concha Aureolas", "Concha Biconcavis", "Concha Labiata", "Concha Renibus"),
    "Electricae": ("Electricae Pluma", "Electricae Radialem"),
    "Fonticulua": ("Fonticulua Campestris", "Fonticulua Digitos", "Fonticulua Fluctus",
                   "Fonticulua Lapida", "Fonticulua Segmentatus", "Fonticulua Upupam"),
    "Frutexa": ("Frutexa Acus", "Frutexa Collum", "Frutexa Fera", "Frutexa Flabellum",
                "Frutexa Flammasis", "Frutexa Metallicum", "Frutexa Sponsae"),
    "Fumerola": ("Fumerola Aquatis", "Fumerola Carbosis", "Fumerola Extremus", "Fumerola Nitris"),
    "Fungoida": ("Fungoida Bullarum", "Fungoida Gelata", "Fungoida Setisis", "Fungoida Stabitis"),
    "Osseus": ("Osseus Cornibus", "Osseus Discus", "Osseus Fractus", "Osseus Pellebantus",
               "Osseus Pumice", "Osseus Spiralis"),
    "Recepta": ("Recepta Conditivus", "Recepta Deltahedronix", "Recepta Umbrux"),
    "Stratum": ("Stratum Araneamus", "Stratum Cucumisis", "Stratum Excutitus", "Stratum Frigus",
                "Stratum Laminamus", "Stratum Limaxus", "Stratum Paleas", "Stratum Tectonicas"),
    "Tubus": ("Tubus Cavas", "Tubus Compagibus", "Tubus Conifer", "Tubus Rosarium",
              "Tubus Sororibus"),
    "Tussock": ("Tussock Albata", "Tussock Capillum", "Tussock Caputus", "Tussock Catena",
                "Tussock Cultro", "Tussock Divisa", "Tussock Ignis", "Tussock Pennata",
                "Tussock Pennatis", "Tussock Propagito", "Tussock Serrati", "Tussock Stigmasis",
                "Tussock Triticum", "Tussock Ventusa", "Tussock Virgam"),
}

BIO_GENUS_NAMES = tuple(BIO_GENERA.keys())
BIO_SPECIES = tuple(sp for species in BIO_GENERA.values() for sp in species)
# Every species, for "any biological signal" — one OR over the whole exobiology catalogue.
ALL_BIO_SPECIES = BIO_SPECIES

# Spoken shorthands that mean "any biological signal at all".
_ANY_BIO = {"biological", "biologicals", "bio", "biology", "exobiology", "exobio", "organic",
            "organics", "life", "any biological", "any bio", "biological signal",
            "biological signals", "bio signal", "bio signals"}


def resolve_subtype(spoken) -> str | None:
    """The canonical Spansh body subtype for a spoken value (aliases + tight fuzzy), or None."""
    return vocab.resolve(BODY_SUBTYPES, spoken, aliases=_SUBTYPE_ALIASES)


def nearest_subtype(spoken) -> str | None:
    """The closest body subtype to an unresolved spoken term (looser), for a correction."""
    return vocab.nearest(BODY_SUBTYPES, spoken, aliases=_SUBTYPE_ALIASES)


def resolve_bio_signal(spoken) -> list[str] | None:
    """The Spansh `landmark_subtype` value(s) for a spoken biological signal, or None.

    A genus ("Bacterium") -> all its species; a species ("Bacterium Aurasus") -> just that one;
    "any biological" / "bio" / "life" -> the union of every species. The list is what the
    capability fills the `landmark_subtype` enum slot with (Spansh ORs a multi-value enum).

    Order matters: an EXACT genus/species match wins before any fuzzing (else "Bacterium" fuzzy-
    matches a random species like "Bacterium Volu"). For a genuine mishear we then fuzz, but bias
    by shape — a multi-word term ("bacterium aurasus") is a species attempt, a single word a
    genus attempt — so a one-word mishear resolves to the genus, not an arbitrary species."""
    if spoken is None:
        return None
    key = vocab.norm(spoken)
    if not key:
        return None
    if key in {vocab.norm(s) for s in _ANY_BIO}:
        return list(ALL_BIO_SPECIES)
    for g in BIO_GENUS_NAMES:                 # exact genus first (a genus is a species' prefix)
        if vocab.norm(g) == key:
            return list(BIO_GENERA[g])
    for s in BIO_SPECIES:                     # then exact species
        if vocab.norm(s) == key:
            return [s]
    multiword = len(str(spoken).split()) >= 2
    order = (_fuzzy_species, _fuzzy_genus) if multiword else (_fuzzy_genus, _fuzzy_species)
    for attempt in order:
        got = attempt(spoken)
        if got is not None:
            return got
    return None


def _fuzzy_genus(spoken) -> list[str] | None:
    g = vocab.resolve(BIO_GENUS_NAMES, spoken)
    return list(BIO_GENERA[g]) if g is not None else None


def _fuzzy_species(spoken) -> list[str] | None:
    s = vocab.resolve(BIO_SPECIES, spoken)
    return [s] if s is not None else None


def nearest_bio_signal(spoken) -> str | None:
    """The closest genus or species name to an unresolved spoken bio term, for a spoken 'did you
    mean…'. Prefers a genus (the shorter, more likely-spoken unit), then a species. A string or
    None — never a list, since a correction names one thing."""
    genus = vocab.nearest(BIO_GENUS_NAMES, spoken)
    if genus is not None:
        return genus
    return vocab.nearest(BIO_SPECIES, spoken)
