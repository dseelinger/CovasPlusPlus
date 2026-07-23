"""Special-place classifier (issue #138) — recognise notable arrival locations.

A proactive arrival callout is event-generic by default ("docked at Farseer Inc"); this turns a
raw location into a `Place {kind, label, detail}` when it's somewhere worth remarking on, so the
callout can be place-AWARE. Everything here is PURE lookup against bundled/offline data — the LLM
voices the label, it never invents it (grounding discipline).

Kinds recognised:

  * ENGINEER base — the docked system+station matches the bundled `ENGINEERS` table -> the
    engineer's name + what they engineer.
  * OWN fleet carrier — reuses the caller-supplied `at_own_carrier` (EDContext, issue #19).
  * LANDMARK — a small, one-line-to-extend table of famous stations/systems (Hutton Orbital, …).
  * FIRST visit to a system — the caller passes `first_visit` (from the visit ledger).

Unknown/ordinary place -> None (the callout behaves exactly as it did before this feature). The
facts helper (`place_facts`) then decides whether the place OR the visit pattern is notable enough
to enrich the prompt at all — see its docstring.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .engineers import ENGINEERS
from .visit_ledger import VisitStats

# Place kinds — stable string tags fed to the LLM as structured facts (not free prose).
KIND_ENGINEER = "engineer base"
KIND_OWN_CARRIER = "your fleet carrier"
KIND_LANDMARK = "landmark"
KIND_FIRST_SYSTEM = "first visit to this system"

# How many 24h visits reads as "you practically live here" (a frequency remark on its own).
FREQUENT_24H = 5
# Round-number lifetime totals worth a nod ("your 50th visit here").
MILESTONES = frozenset({10, 25, 50, 100, 200, 500, 1000})


@dataclass(frozen=True)
class Place:
    """A recognised special place. `label` is a short spoken hook ("Farseer Inc, Felicity
    Farseer's workshop"); `detail` is optional extra grounding ("engineers Frame Shift Drive,
    Thrusters, Sensors"). Both are FACTS for the model to voice, never invented by it."""
    kind: str
    label: str
    detail: str = ""


@dataclass(frozen=True)
class Landmark:
    """One famous place. `station` None = a whole-SYSTEM landmark (matched on arrival in-system);
    otherwise a STATION landmark (matched on docking). Add a row to extend — that's the whole
    extension surface."""
    system: str
    station: str | None
    label: str
    detail: str = ""


# The bundled landmark table. Deliberately tiny — the obvious tourist/lore spots — and designed so
# a new kind is a single row. NOT engineer bases (those come from ENGINEERS) and NOT the own carrier
# (that's live state), so there's no overlap to resolve.
LANDMARKS: tuple[Landmark, ...] = (
    Landmark("Alpha Centauri", "Hutton Orbital", "Hutton Orbital",
             "the famous 6.5-hour supercruise haul — and the mug"),
    Landmark("Sol", None, "Sol", "humanity's home system, permit-locked"),
    Landmark("Shinrarta Dezhra", None, "Shinrarta Dezhra",
             "the pilots' home, Founders World"),
    Landmark("Colonia", None, "Colonia", "the far-flung deep-space colony, ~22,000 ly from the bubble"),
    Landmark("Sagittarius A*", None, "Sagittarius A*",
             "the supermassive black hole at the galactic core"),
)


def _norm(text: object) -> str:
    """Lowercase, drop punctuation, collapse whitespace — tolerant matching of place names."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()


# Pre-normalised lookup maps, built once at import.
_ENGINEER_BY_LOC = {(_norm(e.system), _norm(e.station)): e for e in ENGINEERS}
_STATION_LANDMARKS = {(_norm(lm.system), _norm(lm.station)): lm
                      for lm in LANDMARKS if lm.station}
_SYSTEM_LANDMARKS = {_norm(lm.system): lm for lm in LANDMARKS if not lm.station}


def _engineer_specialties(engineer, limit: int = 3) -> str:
    """A short grounded phrase for what an engineer engineers, e.g. 'Frame Shift Drive,
    Thrusters, Sensors'. Capped so the fact stays terse."""
    specs = list(engineer.specialties)[:limit]
    return ", ".join(specs)


def classify_station(system: object, station: object,
                     *, at_own_carrier: bool = False) -> Place | None:
    """Classify a DOCKED location, or None for an ordinary station. Precedence: engineer base >
    own fleet carrier > station landmark. Pure lookup — no I/O, no invention."""
    if not station:
        return None
    key = (_norm(system), _norm(station))
    eng = _ENGINEER_BY_LOC.get(key)
    if eng is not None:
        label = f"{eng.station}, {eng.name}'s workshop"
        detail = f"engineers {_engineer_specialties(eng)}"
        return Place(KIND_ENGINEER, label, detail)
    if at_own_carrier:
        return Place(KIND_OWN_CARRIER, f"{station}, your own fleet carrier")
    lm = _STATION_LANDMARKS.get(key)
    if lm is not None:
        return Place(KIND_LANDMARK, lm.label, lm.detail)
    return None


def classify_system(system: object, *, first_visit: bool = False) -> Place | None:
    """Classify arrival in a SYSTEM, or None for an ordinary one. A system landmark wins over a
    bare first-visit note. Pure."""
    if not system:
        return None
    lm = _SYSTEM_LANDMARKS.get(_norm(system))
    if lm is not None:
        return Place(KIND_LANDMARK, lm.label, lm.detail)
    if first_visit:
        return Place(KIND_FIRST_SYSTEM, str(system), "you've never been here before")
    return None


def place_facts(place: Place | None, stats: VisitStats | None) -> dict | None:
    """Build the STRUCTURED, grounded facts to feed the proactive prompt — or None when neither the
    place nor the visit pattern is notable enough to remark on (so ordinary arrivals stay generic).

    Notable = the place is special (any `Place`) OR the visit pattern stands out: first visit, a
    round-number milestone, or unusually high 24h frequency. A plain repeat visit to an ordinary
    place is NOT notable (returns None) — that's what keeps history remarks occasional. The LLM
    phrases whatever lands here; every value is a fact it must voice accurately, never invent."""
    facts: dict = {}
    notable = False
    if place is not None:
        facts["place"] = place.kind
        facts["label"] = place.label
        if place.detail:
            facts["detail"] = place.detail
        notable = True
    if stats is not None and stats.total > 0:
        if stats.first_visit:
            facts["first_visit"] = True
            notable = True
        elif stats.total in MILESTONES:
            facts["visit_number"] = stats.total
            notable = True
        if stats.visits_24h >= FREQUENT_24H:
            facts["visits_24h"] = stats.visits_24h
            notable = True
        elif stats.visits_24h >= 2 and place is not None:
            # Ride a special-place remark with the repeat count, but don't make a bare repeat
            # (ordinary place, low frequency) notable on its own.
            facts["visits_24h"] = stats.visits_24h
    return facts if notable else None


def render_facts(facts: dict) -> str:
    """Render the structured facts dict into one grounded English clause for the prompt. Kept
    deterministic (no LLM) so the model receives clean, unambiguous facts to voice."""
    parts: list[str] = []
    label = facts.get("label")
    if label:
        parts.append(f"this is {label}")
    detail = facts.get("detail")
    if detail:
        parts.append(detail)
    if facts.get("first_visit"):
        parts.append("this is your first ever visit here")
    vn = facts.get("visit_number")
    if isinstance(vn, int):
        parts.append(f"this is your {_ordinal(vn)} visit here")
    v24 = facts.get("visits_24h")
    if isinstance(v24, int):
        times = "time" if v24 == 1 else "times"
        parts.append(f"you've been here {v24} {times} in the last 24 hours")
    return "; ".join(parts)


def _ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 50 -> '50th'. Pure."""
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
