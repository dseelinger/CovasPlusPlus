"""Engineers reference table + `EngineerProgress` journal grounding (issue #65).

Two halves, kept apart on purpose:

  * A BUNDLED, OFFLINE reference table (`ENGINEERS`) — every ship engineer's location,
    what modules/blueprints they improve, how you earn the invitation, and the gift/task
    that unlocks their workshop. This is static game knowledge: no network at runtime.
  * The COMMANDER'S own progress, read LIVE from the journal `EngineerProgress` event
    (`parse_engineer_progress`) — Known / Invited / Unlocked and the current grade. This is
    what makes an answer honest about *your* state ("you still need to unlock them") rather
    than reciting a wiki. The capability joins the two: table gives the requirement, the
    journal gives where you actually are.

Regenerating the table
----------------------
The table below is a hand-maintained snapshot of public engineer data. Frontier moves
engineers and tweaks requirements across patches, so treat it as a point-in-time reference
and refresh it against these community sources when it drifts (last refreshed 2026-07):

  * https://inara.cz/elite/engineers/         (per-engineer pages: location, blueprints)
  * https://www.edsm.net/en/engineers         (systems / stations)
  * https://wanderer-toolbox.com/guides/engineering-unlock/  (invitation + unlock tasks)

`access`/`unlock` are the generic requirement prose; the journal is the source of truth for
whether *this* Commander has met them. Match is by the exact ED journal name (the string in
the `EngineerProgress` event's `Engineer` field), so keep `name` verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Journal Progress values, in unlock order (a higher index = further along). "Barred" is a
# rare state (locked out of an engineer) that we surface but don't rank.
PROGRESS_ORDER: tuple[str, ...] = ("Known", "Invited", "Unlocked")


@dataclass(frozen=True)
class Engineer:
    """One ship engineer's static reference data (NOT the Commander's progress).

      * `name`        — the EXACT ED journal name (matches `EngineerProgress`), verbatim.
      * `system`      — star system to fly to.
      * `station`     — the engineer's base / settlement in that system.
      * `region`      — "bubble" or "colonia" (Colonia engineers save the ~22kly trip home).
      * `specialties` — the module / weapon types they can engineer, spoken names.
      * `access`      — how to earn the invitation (the "discovery" requirement).
      * `unlock`      — the gift or first task that opens their workshop.
      * `permit`      — a permit-locked system note, or None.
    """
    name: str
    system: str
    station: str
    region: str
    specialties: tuple[str, ...]
    access: str
    unlock: str
    permit: str | None = None


# The bundled reference table. See the module docstring for sources + how to regenerate.
ENGINEERS: tuple[Engineer, ...] = (
    Engineer(
        name="Felicity Farseer", system="Deciat", station="Farseer Inc", region="bubble",
        specialties=("Frame Shift Drive", "Thrusters", "Sensors",
                     "Detailed Surface Scanner", "FSD Interdictor", "Shield Booster",
                     "Power Plant"),
        access="Reach exploration rank Scout (turn in exploration data).",
        unlock="Donate 1 unit of Meta-Alloys."),
    Engineer(
        name="Elvira Martuuk", system="Khun", station="Long Sight Base", region="bubble",
        specialties=("Frame Shift Drive", "Thrusters", "Shield Generator",
                     "Shield Cell Bank", "FSD Interdictor"),
        access="Travel at least 300 light-years from your career start system.",
        unlock="Supply 3 units of Soontill Relics."),
    Engineer(
        name="The Dweller", system="Wyrd", station="Black Hide", region="bubble",
        specialties=("Power Distributor", "Power Plant"),
        access="Deal with (sell to) at least 5 different black markets.",
        unlock="Pay 500,000 credits."),
    Engineer(
        name="Lei Cheung", system="Laksak", station="Trader's Rest", region="bubble",
        specialties=("Shield Generator", "Shield Booster", "Sensors",
                     "Detailed Surface Scanner"),
        access="Trade at 50 or more different markets.",
        unlock="Supply 200 tons of Gold."),
    Engineer(
        name="Professor Palin", system="Arque", station="Abel Laboratories", region="bubble",
        specialties=("Thrusters", "Frame Shift Drive"),
        access="Get a referral from Elvira Martuuk, then be at least 5,000 ly from start.",
        unlock="Supply 25 units of Sensor Fragments."),
    Engineer(
        name="Marco Qwent", system="Sirius", station="Qwent Research Base", region="bubble",
        specialties=("Power Plant", "Power Distributor"),
        access="Earn an invitation from Sirius Corporation (help a Sirius-aligned faction).",
        unlock="Supply 25 units of Modular Terminals.",
        permit="Sirius system is permit-locked."),
    Engineer(
        name="Tod 'The Blaster' McQuinn", system="Wolf 397", station="Trophy Camp",
        region="bubble",
        specialties=("Multi-Cannon", "Rail Gun", "Cannon", "Fragment Cannon"),
        access="Earn 15 or more Bounty Vouchers.",
        unlock="Supply 100,000 credits' worth of Bounty Vouchers."),
    Engineer(
        name="Selene Jean", system="Kuk", station="Prospector's Rest", region="bubble",
        specialties=("Armour", "Hull Reinforcement Package"),
        access="Get a referral from Tod McQuinn, then mine/refine at least 500 tons of ore.",
        unlock="Supply 10 units of Painite."),
    Engineer(
        name="Bill Turner", system="Alioth", station="Turner Metallics Inc", region="bubble",
        specialties=("Plasma Accelerator", "Sensors", "Detailed Surface Scanner",
                     "Life Support", "Auto Field-Maintenance Unit", "Refinery"),
        access="Reach Friendly status with the Alliance (Alioth is Alliance space).",
        unlock="Supply 50 units of Bromellite.",
        permit="Alioth system requires an Alliance permit."),
    Engineer(
        name="Didi Vatermann", system="Leesti", station="Vatermann LLC", region="bubble",
        specialties=("Shield Booster", "Shield Generator"),
        access="Reach trade rank Merchant.",
        unlock="Supply 50 units of Lavian Brandy."),
    Engineer(
        name="Liz Ryder", system="Eurybia", station="Demolition Unlimited", region="bubble",
        specialties=("Missile Rack", "Seeker Missile Rack", "Torpedo Pylon",
                     "Mine Launcher", "Hull Reinforcement Package", "Armour"),
        access="Reach Cordial or better with the Eurybia Blue Mafia.",
        unlock="Supply 200 units of Landmines."),
    Engineer(
        name="Hera Tani", system="Kuwemaki", station="The Jet's Hole", region="bubble",
        specialties=("Power Plant", "Power Distributor", "Detailed Surface Scanner",
                     "Sensors"),
        access="Reach Empire rank Outsider or better.",
        unlock="Supply 50 units of Kamitra Cigars."),
    Engineer(
        name="Broo Tarquin", system="Muang", station="Broo's Legacy", region="bubble",
        specialties=("Beam Laser", "Burst Laser", "Pulse Laser"),
        access="Reach combat rank Competent or better.",
        unlock="Supply 50 units of Fujin Tea."),
    Engineer(
        name="Zacariah Nemo", system="Yoru", station="Nemo Cyber Party Base", region="bubble",
        specialties=("Fragment Cannon", "Multi-Cannon", "Missile Rack"),
        access="Earn an invitation from the Party of Yoru.",
        unlock="Supply 25 units of Xihe Companions."),
    Engineer(
        name="Juri Ishmaak", system="Giryak", station="Pater's Memorial", region="bubble",
        specialties=("Sensors", "Detailed Surface Scanner", "Mine Launcher", "Missile Rack",
                     "Torpedo Pylon", "Frame Shift Wake Scanner"),
        access="Earn 50 or more Combat Bonds.",
        unlock="Supply 100,000+ credits' worth of Combat Bonds."),
    Engineer(
        name="Lori Jameson", system="Shinrarta Dezhra", station="Jameson Base", region="bubble",
        specialties=("Fuel Scoop", "Auto Field-Maintenance Unit", "Life Support",
                     "Shield Cell Bank", "Refinery", "Detailed Surface Scanner", "Sensors",
                     "Kill Warrant Scanner", "Frame Shift Wake Scanner"),
        access="Reach combat rank Dangerous.",
        unlock="Supply 25 units of Kongga Ale.",
        permit="Shinrarta Dezhra requires the Founders World permit (reach Elite in any rank)."),
    Engineer(
        name="Ram Tah", system="Meene", station="Phoenix Base", region="bubble",
        specialties=("Collector Limpet Controller", "Fuel Transfer Limpet Controller",
                     "Hatch Breaker Limpet Controller", "Prospector Limpet Controller",
                     "Point Defence", "Electronic Countermeasure", "Chaff Launcher",
                     "Heat Sink Launcher"),
        access="Reach exploration rank Surveyor or better.",
        unlock="Supply 50 units of Classified Scan Databanks."),
    Engineer(
        name="Tiana Fortune", system="Achenar", station="Fortune's Loss", region="bubble",
        specialties=("Sensors", "Detailed Surface Scanner", "FSD Interdictor",
                     "Collector Limpet Controller", "Hatch Breaker Limpet Controller",
                     "Kill Warrant Scanner", "Manifest Scanner"),
        access="Reach Friendly status with the Empire.",
        unlock="Supply 50 units of Decoded Emission Data.",
        permit="Achenar system is permit-locked (Empire)."),
    Engineer(
        name="Colonel Bris Dekker", system="Sol", station="Dekker's Yard", region="bubble",
        specialties=("FSD Interdictor", "Frame Shift Drive"),
        access="Reach Friendly status with the Federation.",
        unlock="Supply 1,000,000+ credits' worth of Federal Combat Bonds.",
        permit="Sol system requires a Federation permit."),
    Engineer(
        name="The Sarge", system="Beta-3 Tucani", station="The Beach", region="bubble",
        specialties=("Cannon", "Rail Gun", "Multi-Cannon"),
        access="Reach Federal Navy rank Midshipman.",
        unlock="Supply 50 units of Aberrant Shield Pattern Analysis."),
    # --- Colonia region (~22,000 ly from the bubble; unlock these to engineer out there) ---
    Engineer(
        name="Etienne Dorn", system="Los", station="Kraken's Retreat", region="colonia",
        specialties=("Power Plant", "Power Distributor", "Sensors", "Life Support",
                     "Shield Cell Bank", "Detailed Surface Scanner"),
        access="Get a referral from Liz Ryder and reach trade rank Dealer.",
        unlock="Supply 25 units of Occupied Escape Pods."),
    Engineer(
        name="Marsha Hicks", system="Tir", station="The Watchtower", region="colonia",
        specialties=("Multi-Cannon", "Cannon", "Fragment Cannon", "Fuel Scoop", "Refinery",
                     "Fuel Transfer Limpet Controller", "Collector Limpet Controller"),
        access="Get a referral from The Dweller and reach exploration rank Surveyor.",
        unlock="Supply 10 units of Osmium."),
    Engineer(
        name="Mel Brandon", system="Luchtaine", station="The Brig", region="colonia",
        specialties=("Beam Laser", "Burst Laser", "Pulse Laser", "Shield Generator",
                     "Shield Booster", "Frame Shift Drive", "Thrusters", "Shield Cell Bank"),
        access="Get a referral from Elvira Martuuk and an invitation from the Colonia Council.",
        unlock="Supply 100,000+ credits' worth of Bounty Vouchers."),
    Engineer(
        name="Petra Olmanova", system="Asura", station="Sanctuary", region="colonia",
        specialties=("Armour", "Hull Reinforcement Package", "Chaff Launcher",
                     "Heat Sink Launcher", "Point Defence", "Electronic Countermeasure",
                     "Mine Launcher", "Missile Rack", "Torpedo Pylon"),
        access="Get a referral from Tod McQuinn and reach combat rank Expert.",
        unlock="Supply 200 units of Progenitor Cells."),
    Engineer(
        name="Chloe Sedesi", system="Shenve", station="Cinder Dock", region="colonia",
        specialties=("Thrusters", "Frame Shift Drive"),
        access="Unlocked like Professor Palin (referral from Elvira Martuuk, 5,000 ly out).",
        unlock="Supply 25 units of Sensor Fragments."),
)


# --- name / specialty matching --------------------------------------------------------
# Synonyms so a spoken module word ("FSD", "shields", "jump range") reaches the canonical
# specialty text. Keys and values are matched normalized (lowercase, alnum-collapsed).
_SPECIALTY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "frame shift drive": ("fsd", "jump range", "jump drive", "hyperdrive", "frameshiftdrive"),
    "fsd interdictor": ("interdictor", "frame shift drive interdictor"),
    "thrusters": ("thruster", "engines", "drive"),
    "power plant": ("powerplant", "reactor"),
    "power distributor": ("powerdistributor", "distributor", "distro", "weps"),
    "shield generator": ("shields", "shield", "shield gen"),
    "shield booster": ("boosters",),
    "shield cell bank": ("scb", "shield cell", "cell bank"),
    "hull reinforcement package": ("hull reinforcement", "hrp", "hull"),
    "armour": ("armor", "bulkheads", "plating"),
    "multi-cannon": ("multicannon", "multi cannon", "mc"),
    "fragment cannon": ("frag cannon", "shotgun", "fragmentcannon"),
    "rail gun": ("railgun", "rails"),
    "beam laser": ("beam", "beams"),
    "burst laser": ("burst", "bursts"),
    "pulse laser": ("pulse", "pulses", "laser", "lasers"),
    "plasma accelerator": ("plasma", "pa", "plasma accelerators"),
    "missile rack": ("missiles", "missile", "missile launcher"),
    "seeker missile rack": ("seeker missiles", "seekers"),
    "torpedo pylon": ("torpedo", "torpedoes", "torps"),
    "mine launcher": ("mines", "mine"),
    "detailed surface scanner": ("dss", "surface scanner"),
    "frame shift wake scanner": ("wake scanner", "fsd wake scanner"),
    "kill warrant scanner": ("kws", "kill warrant"),
    "manifest scanner": ("cargo scanner",),
    "fuel scoop": ("scoop", "fuelscoop"),
    "auto field-maintenance unit": ("afmu", "afm", "field maintenance"),
    "life support": ("lifesupport",),
    "refinery": ("refineries",),
    "point defence": ("point defense", "pd", "point defence turret"),
    "electronic countermeasure": ("ecm",),
    "chaff launcher": ("chaff",),
    "heat sink launcher": ("heat sink", "heatsink"),
    "collector limpet controller": ("collector limpet", "collector controller"),
    "fuel transfer limpet controller": ("fuel transfer limpet",),
    "hatch breaker limpet controller": ("hatch breaker limpet",),
    "prospector limpet controller": ("prospector limpet",),
    "sensors": ("sensor",),
}


def _norm(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for tolerant matching of spoken
    engineer names and module words."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text).lower())).strip()


def find_engineer(query: str) -> Engineer | None:
    """Resolve a spoken engineer name to a table entry, or None. Matches on the full name,
    any single name token (so 'farseer' or 'mcquinn' work), and tolerates punctuation/quotes.
    Never invents — an unrecognised name returns None so the caller can say what IS known."""
    q = _norm(query)
    if not q:
        return None
    best: Engineer | None = None
    for eng in ENGINEERS:
        name = _norm(eng.name)
        if name == q:
            return eng                       # exact wins outright
        tokens = set(name.split())
        # A query that is a full token ('farseer') or a substring of the name ('mcqu').
        if q in tokens or (len(q) >= 3 and q in name):
            best = best or eng
        elif set(q.split()) & (tokens - {"the", "professor", "colonel"}):
            best = best or eng
    return best


def find_by_specialty(query: str) -> list[Engineer]:
    """Every engineer who can engineer the module/blueprint named in `query` (e.g. 'FSD',
    'shields', 'multi-cannon'), in table order. Empty when nothing matches — the caller then
    says so rather than guessing. Bubble engineers come before Colonia ones for a nearer
    default when the Commander hasn't said where they are."""
    canon = _canonical_specialties(query)
    if not canon:
        return []
    hits = [e for e in ENGINEERS if any(_norm(s) in canon for s in e.specialties)]
    hits.sort(key=lambda e: 0 if e.region == "bubble" else 1)
    return hits


def _canonical_specialties(query: str) -> set[str]:
    """The set of normalized canonical specialty strings a spoken module word refers to.
    Resolves synonyms ('fsd' -> 'frame shift drive') and also matches when the query text is
    contained in a specialty name (so 'laser' reaches beam/burst/pulse laser)."""
    q = _norm(query)
    if not q:
        return set()
    canon: set[str] = set()
    for spec, syns in _SPECIALTY_SYNONYMS.items():
        if q == _norm(spec) or q in {_norm(s) for s in syns}:
            canon.add(_norm(spec))
    # Fall back to substring against the real specialty strings on every engineer, so an
    # un-synonymed but literal word ('cannon', 'limpet') still finds its engineers.
    for eng in ENGINEERS:
        for spec in eng.specialties:
            ns = _norm(spec)
            if q == ns or (len(q) >= 3 and (q in ns or ns in q)):
                canon.add(ns)
    return canon


# --- journal grounding: EngineerProgress ----------------------------------------------

@dataclass(frozen=True)
class EngineerStatus:
    """The Commander's live progress with one engineer, from the journal `EngineerProgress`
    event. `progress` is 'Known' | 'Invited' | 'Unlocked' | 'Barred'; `rank` is the unlocked
    grade 1-5 (None until unlocked)."""
    progress: str
    rank: int | None = None

    @property
    def unlocked(self) -> bool:
        return self.progress == "Unlocked"


def parse_engineer_progress(event: dict) -> dict[str, EngineerStatus]:
    """Fold an `EngineerProgress` journal event into a {engineer-name: EngineerStatus} map.

    ED writes two shapes, both handled here:
      * the startup SUMMARY — an `Engineers` array of every engineer you have any progress
        with (a full snapshot);
      * a single-engineer UPDATE — `Engineer`/`Progress`/`Rank` at the top level, emitted when
        one changes.

    Keyed by the exact journal name so it joins straight onto the reference table. A row with
    no usable name or progress is skipped (fail soft), never guessed."""
    rows = event.get("Engineers")
    if not isinstance(rows, list):
        rows = [event]                       # single-engineer update form
    out: dict[str, EngineerStatus] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Engineer") or "").strip()
        progress = str(row.get("Progress") or "").strip()
        if not name or not progress:
            continue
        rank = row.get("Rank")
        out[name] = EngineerStatus(
            progress=progress,
            rank=int(rank) if isinstance(rank, int) else None)
    return out


def status_for(engineer: Engineer,
               progress: dict[str, EngineerStatus] | None) -> EngineerStatus | None:
    """The Commander's status for `engineer` from a progress map (name-matched), or None when
    unknown — meaning the journal shows no progress at all with them yet."""
    if not progress:
        return None
    return progress.get(engineer.name)


# --- dashboard view-model (issue #133) ------------------------------------------------
# A PURE join of the static table with the live progress map, shaped for the control-panel
# Engineering dashboard. Kept here (next to the data it reads) and JSON-serializable so the web
# route stays a thin adapter and `pytest` can cover the join offline. Voice recites engineers one
# at a time; this makes "everything left across all 20+ engineers" scannable in one grid.

# The four scannable buckets a row falls in. "in_progress" folds Invited + Known (Discovered);
# "locked" folds a missing entry, Barred, and any unrecognised progress value (fail soft).
_GROUP_BY_PROGRESS: dict[str, str] = {
    "Unlocked": "unlocked",
    "Invited": "in_progress",
    "Known": "in_progress",
    "Barred": "locked",
}


def _outstanding(engineer: Engineer, status: EngineerStatus | None) -> str:
    """The requirement still standing between the Commander and this engineer's workshop, as
    one line. Empty once unlocked. Mirrors the voice capability's status prose: Invited needs
    only the unlock gift; Discovered (Known) needs the invitation task then the gift; not-started
    (or unknown) needs both."""
    if status is not None and status.progress == "Unlocked":
        return ""
    if status is not None and status.progress == "Invited":
        return engineer.unlock
    if status is not None and status.progress == "Barred":
        return "Currently barred from this engineer."
    if status is not None and status.progress == "Known":
        # Discovered but not yet invited: the invitation task remains, then the unlock gift.
        return f"{engineer.access} Then {engineer.unlock[:1].lower() + engineer.unlock[1:]}"
    # Not started (no journal entry) or an unrecognised value: both halves remain.
    return f"{engineer.access} Then {engineer.unlock[:1].lower() + engineer.unlock[1:]}"


def _dashboard_row(engineer: Engineer, status: EngineerStatus | None) -> dict:
    """One JSON-serializable dashboard row for `engineer` given the Commander's status (or None)."""
    progress = status.progress if status is not None else ""
    group = _GROUP_BY_PROGRESS.get(progress, "locked")
    return {
        "name": engineer.name,
        "system": engineer.system,
        "station": engineer.station,
        "region": engineer.region,
        "specialties": list(engineer.specialties),
        "permit": engineer.permit,
        # Live status: canonical journal value ("Unlocked"/"Invited"/"Known"/"Barred") or "" when
        # the journal shows no progress with them yet; `group` is the scannable bucket.
        "progress": progress,
        "group": group,
        "grade": status.rank if status is not None else None,
        "access": engineer.access,
        "unlock": engineer.unlock,
        "outstanding": _outstanding(engineer, status),
    }


def engineer_dashboard(progress: dict[str, EngineerStatus] | None) -> dict:
    """Build the full Engineering-dashboard view-model: every engineer joined with the Commander's
    live progress, plus per-bucket counts. `has_progress` is False until an `EngineerProgress`
    event has been read — the page shows a "no journal data yet" note but still lists every
    engineer with what each requires (all shown locked). Pure and JSON-serializable, so the web
    route is a thin adapter and the join is unit-tested offline."""
    rows = [_dashboard_row(e, status_for(e, progress)) for e in ENGINEERS]
    counts = {"unlocked": 0, "in_progress": 0, "locked": 0}
    for row in rows:
        counts[row["group"]] += 1
    return {
        "has_progress": bool(progress),
        "total": len(rows),
        "counts": counts,
        "engineers": rows,
    }
