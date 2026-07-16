"""Odyssey ON-FOOT engineering reference — suits, weapons, modifications, engineers (#73).

The on-foot sibling of the SHIP engineering data path (`engineers.py` + `blueprints.py`).
Everything here is a BUNDLED, OFFLINE reference table — static Odyssey game knowledge, no
network at runtime — so "how do I engineer my Maverick suit" or "who unlocks Greater Range"
answers from DATA, never a vague LLM guess.

Four tables, kept apart on purpose:

  * `SUITS`        — the three engineerable suits (Maverick / Dominator / Artemis), their role,
                     and the material recipe to raise each GRADE 1->5.
  * `WEAPONS`      — the eleven handheld weapons grouped by family (Karma kinetic / TK laser /
                     Manticore plasma), each with its grade-upgrade recipe.
  * `MODIFICATIONS`— the suit + weapon modification catalogue (the perks engineers apply), each
                     with what it does and whether it targets a suit or a weapon.
  * `ENGINEERS`    — the 13 on-foot engineers: location, how you gain access, the unlock task,
                     the referral they hand you, and which modifications they offer.

Grade upgrades vs modifications (the two halves of Odyssey engineering):
  * A **grade upgrade** (1->5) raises a suit/weapon's base stats and is applied at any concourse
    with a Pioneer Supplies vendor — it is NOT engineer-gated. The recipe follows a fixed pattern
    per item class (see `_GRADE_TRIO_COUNTS` / `_GRADE_COMPONENT_COUNTS`).
  * A **modification** is a perk (e.g. Greater Range, Extra Backpack Capacity) applied by a
    specific ENGINEER once you have unlocked them. Which engineer offers which perk is the
    `ENGINEERS[*].suit_mods / weapon_mods` mapping.

Regenerating / refreshing
-------------------------
Hand-maintained snapshot of public Odyssey data (last refreshed 2026-07). Frontier can tweak
requirements or relocate engineers between patches, so treat it as point-in-time and refresh
against these community sources when it drifts:

  * https://inara.cz/elite/engineers/                 (per-engineer: location, mods, unlock)
  * https://inara.cz/elite/equipment-blueprint/2/     (suit/weapon grade-upgrade recipes)
  * https://elite-dangerous.fandom.com/wiki/Engineers (Odyssey engineer overview)

Engineer `name` values are the exact ED journal names (the `EngineerProgress` `Engineer`
field), verbatim — on-foot engineers share that event with ship engineers — so the capability
can join live unlock status the same way the ship path does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- grade-upgrade recipe pattern -----------------------------------------------------------
# Every suit/weapon shares ONE upgrade shape, verified against Inara's equipment-blueprint
# pages (2026-07): three "trio" goods (a schematic, a class consumable, Manufacturing
# Instructions) plus two class-specific components. Only the counts change with grade, and
# they change identically for every item — so we store each item's variable material NAMES and
# generate the per-grade recipe from these two count tables. Grade 1 is the base (no upgrade).
_GRADE_TRIO_COUNTS: dict[int, int] = {2: 1, 3: 2, 4: 4, 5: 5}
_GRADE_COMPONENT_COUNTS: dict[int, int] = {2: 2, 3: 5, 4: 9, 5: 12}
MAX_GRADE = 5


@dataclass(frozen=True)
class GradeStep:
    """One grade transition's shopping list: `(material name, quantity)` pairs consumed to raise
    the item to `grade`. Order is trio goods first, then the two class components."""
    grade: int
    materials: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Suit:
    """An engineerable Odyssey suit.

      * `name`      — spoken/display name (also the shop name).
      * `aliases`   — mishear-tolerant alternates ("maverick", "mining suit").
      * `role`      — one spoken sentence: what the suit is for.
      * `trio`      — the three grade-upgrade goods (schematic, monitor, instructions).
      * `components`— the two class-specific components consumed each grade step.
    """
    name: str
    aliases: tuple[str, ...]
    role: str
    trio: tuple[str, str, str]
    components: tuple[str, str]

    def grade_step(self, grade: int) -> GradeStep | None:
        return _grade_step(grade, self.trio, self.components)


@dataclass(frozen=True)
class Weapon:
    """An engineerable Odyssey handheld weapon.

      * `family`     — "Kinematic Armaments" (Karma), "Takada" (TK), or "Manticore".
      * `damage`     — the damage type the family deals (Kinetic / Thermal / Plasma).
      * `kind`       — pistol / SMG / assault rifle / marksman rifle / shotgun.
      * `trio`/`components` — the grade-upgrade recipe pieces (see `Suit`).
    """
    name: str
    aliases: tuple[str, ...]
    family: str
    damage: str
    kind: str
    trio: tuple[str, str, str]
    components: tuple[str, str]

    def grade_step(self, grade: int) -> GradeStep | None:
        return _grade_step(grade, self.trio, self.components)


@dataclass(frozen=True)
class Modification:
    """One suit or weapon modification (an engineer-applied perk).

      * `name`    — canonical modification name (matches the engineer offerings).
      * `target`  — "suit" or "weapon".
      * `effect`  — one spoken sentence: what it does.
      * `aliases` — tolerant alternates for spoken matching.
    """
    name: str
    target: str
    effect: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class OnFootEngineer:
    """One on-foot engineer's static reference data (NOT the Commander's live progress).

      * `name`        — EXACT ED journal name (`EngineerProgress` `Engineer`), verbatim.
      * `system` / `settlement` — where to fly + the workshop name.
      * `region`      — "bubble" or "colonia".
      * `access`      — how they become known to you (the discovery requirement).
      * `unlock`      — the task/delivery that opens their workshop.
      * `referral`    — the gift that unlocks the engineer they point you to (or None).
      * `refers_to`   — the engineer this one refers you to (or None).
      * `suit_mods` / `weapon_mods` — the modifications they offer.
    """
    name: str
    system: str
    settlement: str
    region: str
    access: str
    unlock: str
    referral: str | None
    refers_to: str | None
    suit_mods: tuple[str, ...]
    weapon_mods: tuple[str, ...]

    @property
    def modifies(self) -> str:
        """A short 'suits and weapons' / 'suits' / 'weapons' summary for speech."""
        s, w = bool(self.suit_mods), bool(self.weapon_mods)
        return "suits and weapons" if s and w else "suits" if s else "weapons" if w else ""


def _grade_step(grade: int, trio: tuple[str, str, str],
                components: tuple[str, str]) -> GradeStep | None:
    """Build the `(material, count)` recipe for one grade transition (2..5) from the shared
    count pattern. Grade 1 (the base item) and out-of-range grades return None."""
    tn = _GRADE_TRIO_COUNTS.get(int(grade))
    cn = _GRADE_COMPONENT_COUNTS.get(int(grade))
    if tn is None or cn is None:
        return None
    mats = tuple((m, tn) for m in trio) + tuple((c, cn) for c in components)
    return GradeStep(grade=int(grade), materials=mats)


# --- suits ----------------------------------------------------------------------------------
# Recipe goods verified on Inara equipment-blueprint pages 2/3/4 (2026-07). All three suits
# share the trio; only the plating differs (Maverick=carbon fibre, Dominator=titanium,
# Artemis=aerogel). The basic Flight Suit is deliberately absent — it can't be engineered.
_SUIT_TRIO = ("Suit Schematic", "Health Monitor", "Manufacturing Instructions")

SUITS: tuple[Suit, ...] = (
    Suit(name="Maverick Suit", aliases=("maverick", "mining suit", "scavenger suit", "salvage suit"),
         role="The scavenger/utility suit — carries the Arc Cutter and an extra backpack, best "
              "for on-foot mining, salvage and settlement raids.",
         trio=_SUIT_TRIO, components=("Carbon Fibre Plating", "Graphene")),
    Suit(name="Dominator Suit", aliases=("dominator", "combat suit", "assault suit"),
         role="The combat suit — extra weapon slots and grenade/shield capacity, built for "
              "conflict zones and settlement assaults.",
         trio=_SUIT_TRIO, components=("Titanium Plating", "Graphene")),
    Suit(name="Artemis Suit", aliases=("artemis", "exploration suit", "explorer suit", "science suit"),
         role="The exploration/science suit — carries the Genetic Sampler for exobiology and "
              "favours scanning and long surface excursions.",
         trio=_SUIT_TRIO, components=("Aerogel", "Graphene")),
)


# --- weapons --------------------------------------------------------------------------------
# Recipe verified on Inara equipment-blueprint pages (2026-07): each family shares a gas +
# component pair. Kinematic (Karma) = Compression-Liquefied Gas / Tungsten Carbide + Weapon
# Component; Takada (TK) = Ionised Gas / Microelectrode + Optical Fibre; Manticore = Ionised
# Gas / Chemical Superbase + Microelectrode.
def _wtrio(gas: str) -> tuple[str, str, str]:
    return ("Weapon Schematic", gas, "Manufacturing Instructions")


_KARMA = (_wtrio("Compression-Liquefied Gas"), ("Tungsten Carbide", "Weapon Component"))
_TAKADA = (_wtrio("Ionised Gas"), ("Microelectrode", "Optical Fibre"))
_MANTICORE = (_wtrio("Ionised Gas"), ("Chemical Superbase", "Microelectrode"))

WEAPONS: tuple[Weapon, ...] = (
    # Kinematic Armaments (Karma) — kinetic/ballistic.
    Weapon("Karma AR-50", ("karma ar 50", "ar50", "karma rifle", "kinetic assault rifle"),
           "Kinematic Armaments", "Kinetic", "assault rifle", *_KARMA),
    Weapon("Karma C-44", ("karma c 44", "c44", "karma pistol", "kinetic pistol"),
           "Kinematic Armaments", "Kinetic", "pistol", *_KARMA),
    Weapon("Karma L-6", ("karma l 6", "l6", "karma smg", "kinetic smg"),
           "Kinematic Armaments", "Kinetic", "submachine gun", *_KARMA),
    Weapon("Karma P-15", ("karma p 15", "p15", "karma marksman", "kinetic marksman rifle"),
           "Kinematic Armaments", "Kinetic", "marksman rifle", *_KARMA),
    # Takada (TK) — thermal/laser.
    Weapon("TK Aphelion", ("aphelion", "tk rifle", "laser rifle", "laser assault rifle"),
           "Takada", "Thermal", "assault rifle", *_TAKADA),
    Weapon("TK Eclipse", ("eclipse", "tk smg", "laser smg"),
           "Takada", "Thermal", "submachine gun", *_TAKADA),
    Weapon("TK Zenith", ("zenith", "tk pistol", "laser pistol"),
           "Takada", "Thermal", "pistol", *_TAKADA),
    # Manticore — plasma/caustic.
    Weapon("Manticore Executioner", ("executioner", "manticore marksman", "plasma marksman rifle"),
           "Manticore", "Plasma", "marksman rifle", *_MANTICORE),
    Weapon("Manticore Intimidator", ("intimidator", "manticore shotgun", "plasma shotgun"),
           "Manticore", "Plasma", "shotgun", *_MANTICORE),
    Weapon("Manticore Oppressor", ("oppressor", "manticore rifle", "plasma assault rifle"),
           "Manticore", "Plasma", "assault rifle", *_MANTICORE),
    Weapon("Manticore Tormentor", ("tormentor", "manticore pistol", "plasma pistol"),
           "Manticore", "Plasma", "pistol", *_MANTICORE),
)


# --- modification catalogue -----------------------------------------------------------------
# Canonical names + effects (Inara / Odyssey tooltips). Some weapon mods are offered per weapon
# family (Greater Range / Headshot Damage / Improved Hip Fire Accuracy) — we model the perk once
# and let the engineer table say who offers it; the effect is the same whatever weapon you bring.
MODIFICATIONS: tuple[Modification, ...] = (
    # Suit modifications.
    Modification("Added Melee Damage", "suit",
                 "Increases the damage of melee (fist and weapon-strike) attacks.",
                 ("melee damage", "increased melee damage")),
    Modification("Combat Movement Speed", "suit",
                 "Removes the movement/turn-speed penalty while aiming down sights.",
                 ("combat speed", "movement speed")),
    Modification("Damage Resistance", "suit",
                 "Increases resistance to all incoming damage types.",
                 ("armour rating", "damage resist", "resistance")),
    Modification("Enhanced Tracking", "suit",
                 "Increases the range and speed of the suit's target scanning.",
                 ("tracking",)),
    Modification("Extra Ammo Capacity", "suit",
                 "Increases the reserve ammunition the suit carries for your weapons.",
                 ("extra ammo", "increased ammo reserves", "ammo capacity")),
    Modification("Extra Backpack Capacity", "suit",
                 "Increases backpack capacity for goods, assets, data and consumables.",
                 ("backpack capacity", "extra backpack", "more backpack")),
    Modification("Faster Shield Regen", "suit",
                 "Suit shields recharge faster and to a higher level.",
                 ("shield regen", "shield regeneration", "faster shields")),
    Modification("Improved Battery Capacity", "suit",
                 "Increases the suit's energy-cell (battery) capacity.",
                 ("battery capacity", "improved battery", "bigger battery")),
    Modification("Improved Jump Assist", "suit",
                 "Extends the thrust duration of the suit's jump assist.",
                 ("jump assist",)),
    Modification("Increased Air Reserves", "suit",
                 "Extends breathable-air / life-support duration.",
                 ("air reserves", "more air", "oxygen")),
    Modification("Increased Sprint Duration", "suit",
                 "Reduces stamina drain so you can sprint for longer.",
                 ("sprint duration", "sprint", "stamina")),
    Modification("Night Vision", "suit",
                 "Enables suit night vision for dark interiors and planet nights.",
                 ("nightvision",)),
    Modification("Quieter Footsteps", "suit",
                 "Dampens the noise your footsteps make, so NPCs hear you from less far away.",
                 ("quiet footsteps", "footsteps", "silent footsteps")),
    Modification("Reduced Tool Battery Consumption", "suit",
                 "Handheld tools (energylink, profile analyser) drain the suit battery slower.",
                 ("tool battery", "reduced battery drain")),
    # Weapon modifications.
    Modification("Audio Masking", "weapon",
                 "Reduces the sound of firing so shots carry less far.",
                 ("audio mask", "sound masking")),
    Modification("Faster Handling", "weapon",
                 "Faster weapon draw/stow and quicker to aim down sights.",
                 ("handling", "faster draw")),
    Modification("Greater Range", "weapon",
                 "Increases the weapon's effective (damage-falloff) range.",
                 ("range", "more range", "increased range")),
    Modification("Headshot Damage", "weapon",
                 "Increases the damage multiplier on headshots.",
                 ("headshot", "head damage")),
    Modification("Improved Hip Fire Accuracy", "weapon",
                 "Tightens hip-fire spread for better accuracy without aiming down sights.",
                 ("hip fire", "hip fire accuracy", "higher accuracy", "accuracy")),
    Modification("Magazine Size", "weapon",
                 "Increases the weapon's magazine capacity.",
                 ("magazine", "mag size", "clip size", "bigger magazine")),
    Modification("Noise Suppressor", "weapon",
                 "Reduces firing noise in pressurized environments.",
                 ("suppressor", "silencer", "noise suppression")),
    Modification("Reload Speed", "weapon",
                 "Shortens the weapon's reload time.",
                 ("reload", "faster reload")),
    Modification("Scope", "weapon",
                 "Adds a magnified aiming optic (scope) to the weapon.",
                 ("sight", "optic", "zoom")),
    Modification("Stability", "weapon",
                 "Reduces recoil for tighter sustained fire.",
                 ("recoil", "stable", "recoil reduction")),
    Modification("Stowed Reloading", "weapon",
                 "Automatically reloads the weapon while it is stowed.",
                 ("stowed reload", "auto reload")),
)


# --- engineers ------------------------------------------------------------------------------
# Location / access / unlock / referral verified on Inara per-engineer pages (2026-07). The
# referral chains: Domino Green -> Kit Fowler -> Yarden Bond; Hero Ferrari -> Wellington Beck
# -> Uma Laszlo; Jude Navarro -> Terra Velasquez -> Oden Geiger. Colonia: Baltanos / Eleanor
# Bresa / Rosa Dayette each refer to Yi Shen.
ENGINEERS: tuple[OnFootEngineer, ...] = (
    OnFootEngineer(
        name="Domino Green", system="Orishis", settlement="The Jackrabbit", region="bubble",
        access="Common knowledge — no referral needed to discover her.",
        unlock="Travel at least 100 light-years in Apex shuttles.",
        referral="Provide 5 units of Push (to refer you to Kit Fowler).",
        refers_to="Kit Fowler",
        suit_mods=("Enhanced Tracking", "Extra Backpack Capacity",
                   "Reduced Tool Battery Consumption"),
        weapon_mods=("Greater Range", "Stability")),
    OnFootEngineer(
        name="Kit Fowler", system="Capoya", settlement="The Last Call", region="bubble",
        access="Referral from Domino Green.",
        unlock="Sell 5 Opinion Polls to bartenders.",
        referral="Provide 5 units of Surveillance Equipment (to refer you to Yarden Bond).",
        refers_to="Yarden Bond",
        suit_mods=("Added Melee Damage", "Extra Ammo Capacity", "Faster Shield Regen"),
        weapon_mods=("Magazine Size", "Stowed Reloading")),
    OnFootEngineer(
        name="Yarden Bond", system="Bayan", settlement="Salamander Bank", region="bubble",
        access="Referral from Kit Fowler.",
        unlock="Sell 5 Smear Campaign Plans to bartenders.",
        referral=None, refers_to=None,
        suit_mods=("Combat Movement Speed", "Improved Jump Assist", "Quieter Footsteps"),
        weapon_mods=("Audio Masking", "Faster Handling", "Improved Hip Fire Accuracy")),
    OnFootEngineer(
        name="Hero Ferrari", system="Siris", settlement="Nevermore Terrace", region="bubble",
        access="Common knowledge — no referral needed to discover her.",
        unlock="Complete 10 (low-threat) on-foot surface conflict zones.",
        referral="Provide 5 units of Settlement Defence Plans (to refer you to Wellington Beck).",
        refers_to="Wellington Beck",
        suit_mods=("Improved Jump Assist", "Increased Air Reserves", "Increased Sprint Duration"),
        weapon_mods=("Faster Handling", "Noise Suppressor")),
    OnFootEngineer(
        name="Wellington Beck", system="Jolapa", settlement="Beck Facility", region="bubble",
        access="Referral from Hero Ferrari.",
        unlock="Sell 15 total of Multimedia Entertainment, Classic Entertainment and Cat Media "
               "to bartenders.",
        referral="Provide 5 units of Insight Entertainment Suite (to refer you to Uma Laszlo).",
        refers_to="Uma Laszlo",
        suit_mods=("Extra Backpack Capacity", "Improved Battery Capacity",
                   "Reduced Tool Battery Consumption"),
        weapon_mods=("Greater Range", "Scope")),
    OnFootEngineer(
        name="Uma Laszlo", system="Xuane", settlement="Laszlo's Resolve", region="bubble",
        access="Referral from Wellington Beck.",
        unlock="Reach Unfriendly reputation (or lower) with Sirius Corporation.",
        referral=None, refers_to=None,
        suit_mods=("Damage Resistance", "Faster Shield Regen"),
        weapon_mods=("Headshot Damage", "Reload Speed", "Stowed Reloading")),
    OnFootEngineer(
        name="Jude Navarro", system="Aurai", settlement="Marshall's Drift", region="bubble",
        access="Common knowledge — no referral needed to discover him.",
        unlock="Complete 10 Restore or Reactivation missions.",
        referral="Provide 5 units of Genetic Repair Meds (to refer you to Terra Velasquez).",
        refers_to="Terra Velasquez",
        suit_mods=("Added Melee Damage", "Damage Resistance", "Extra Ammo Capacity"),
        weapon_mods=("Magazine Size", "Reload Speed")),
    OnFootEngineer(
        name="Terra Velasquez", system="Shou Xing", settlement="Rascal's Choice", region="bubble",
        access="Referral from Jude Navarro.",
        unlock="Complete 6 Covert Theft and Covert Heist missions.",
        referral="Provide 15 units of Financial Projections (to refer you to Oden Geiger).",
        refers_to="Oden Geiger",
        suit_mods=("Combat Movement Speed", "Increased Air Reserves", "Increased Sprint Duration"),
        weapon_mods=("Improved Hip Fire Accuracy", "Noise Suppressor")),
    OnFootEngineer(
        name="Oden Geiger", system="Candiaei", settlement="Ankh's Promise", region="bubble",
        access="Referral from Terra Velasquez.",
        unlock="Sell 20 total of Biological Sample, Employee Genetic Data and Genetic Research "
               "to bartenders.",
        referral=None, refers_to=None,
        suit_mods=("Enhanced Tracking", "Improved Battery Capacity", "Night Vision"),
        weapon_mods=("Scope", "Stability")),
    # --- Colonia region engineers ---
    OnFootEngineer(
        name="Baltanos", system="Deriso", settlement="The Divine Apparatus", region="colonia",
        access="Common knowledge (Colonia region).",
        unlock="Reach Friendly reputation with the Colonia Council.",
        referral="Provide 10 units of Faction Associates data (to refer you to Yi Shen).",
        refers_to="Yi Shen",
        suit_mods=("Combat Movement Speed", "Improved Jump Assist", "Increased Air Reserves",
                   "Increased Sprint Duration"),
        weapon_mods=("Faster Handling", "Improved Hip Fire Accuracy", "Noise Suppressor")),
    OnFootEngineer(
        name="Eleanor Bresa", system="Desy", settlement="Bresa Modifications", region="colonia",
        access="Common knowledge (Colonia region).",
        unlock="Disembark at 5 different planetary settlements in the Colonia system.",
        referral="Provide 10 units of Digital Designs (to refer you to Yi Shen).",
        refers_to="Yi Shen",
        suit_mods=("Added Melee Damage", "Damage Resistance", "Extra Ammo Capacity",
                   "Faster Shield Regen"),
        weapon_mods=("Magazine Size", "Reload Speed", "Stowed Reloading")),
    OnFootEngineer(
        name="Rosa Dayette", system="Kojeara", settlement="Rosa's Shop", region="colonia",
        access="Common knowledge (Colonia region).",
        unlock="Exchange 10 total of Culinary Recipes or Cocktail Recipes at Colonia stations.",
        referral="Provide 10 units of Manufacturing Instructions (to refer you to Yi Shen).",
        refers_to="Yi Shen",
        suit_mods=("Enhanced Tracking", "Extra Backpack Capacity", "Improved Battery Capacity",
                   "Reduced Tool Battery Consumption"),
        weapon_mods=("Greater Range", "Scope", "Stability")),
    OnFootEngineer(
        name="Yi Shen", system="Einheriar", settlement="Eidolon Hold", region="colonia",
        access="Referrals from Baltanos, Eleanor Bresa and Rosa Dayette.",
        unlock="Complete the referral tasks for Baltanos, Eleanor Bresa and Rosa Dayette.",
        referral=None, refers_to=None,
        suit_mods=("Night Vision", "Quieter Footsteps"),
        weapon_mods=("Audio Masking", "Headshot Damage")),
)


# --- material sourcing ----------------------------------------------------------------------
# A short "where do I get it" hint per grade-upgrade material (the Odyssey asset/good/data
# groups). Keyed by exact material name so the read tool can annotate a recipe without
# inventing a source. Missing key -> no hint (fail soft).
MATERIAL_SOURCES: dict[str, str] = {
    "Suit Schematic": "Good — loot from settlement lockers/POIs or buy/trade at a bar.",
    "Weapon Schematic": "Good — loot from settlement lockers/POIs or buy/trade at a bar.",
    "Health Monitor": "Good — medical facilities and settlement lockers, or trade at a bar.",
    "Manufacturing Instructions": "Data — download from settlement data ports, or trade at a bar.",
    "Graphene": "Asset (chemical) — settlement containers, disassembled items, or bar trade.",
    "Carbon Fibre Plating": "Asset (tech) — industrial/settlement containers and lockers.",
    "Titanium Plating": "Asset (tech) — industrial/settlement containers and lockers.",
    "Aerogel": "Asset (chemical) — settlement containers; common at research/medical sites.",
    "Tungsten Carbide": "Asset (tech) — settlement containers and disassembled weapons.",
    "Weapon Component": "Asset (tech) — settlement weapon lockers and disassembled weapons.",
    "Microelectrode": "Asset (circuit) — settlement containers and electronics lockers.",
    "Optical Fibre": "Asset (circuit) — settlement containers and electronics lockers.",
    "Chemical Superbase": "Asset (chemical) — settlement chemical stores and containers.",
    "Ionised Gas": "Asset (chemical) — settlement containers and industrial sites.",
    "Compression-Liquefied Gas": "Asset (chemical) — settlement containers and industrial sites.",
}


# --- normalized matching --------------------------------------------------------------------
_STOP = {"the", "a", "an", "my", "suit", "weapon", "gun", "mod", "mods", "modification",
         "modifications", "engineer", "engineering", "upgrade", "grade", "for", "to"}


def _norm(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — tolerant spoken matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text).lower())).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t and t not in _STOP}


def find_suit(query: str) -> Suit | None:
    """Resolve a spoken suit name to a `Suit`, or None (never guesses). Matches the full name,
    the leading keyword ('maverick'), or any alias."""
    q = _norm(query)
    if not q:
        return None
    for suit in SUITS:
        names = (_norm(suit.name), *(_norm(a) for a in suit.aliases))
        if q in names or any(q == n for n in names):
            return suit
        # leading keyword ('maverick', 'artemis') contained in the query
        key = suit.name.split()[0].lower()
        if key in q.split():
            return suit
    return None


def find_weapon(query: str) -> Weapon | None:
    """Resolve a spoken weapon name to a `Weapon`, or None. Matches name, aliases, and the
    model token ('aphelion', 'ar50', 'oppressor')."""
    q = _norm(query)
    if not q:
        return None
    for wep in WEAPONS:
        names = (_norm(wep.name), *(_norm(a) for a in wep.aliases))
        if q in names:
            return wep
    # token overlap fallback (so "karma ar 50" or "the oppressor" still resolve)
    qt = _tokens(query)
    if not qt:
        return None
    best: Weapon | None = None
    best_score = 0
    for wep in WEAPONS:
        pool = _tokens(wep.name)
        for a in wep.aliases:
            pool |= _tokens(a)
        score = len(qt & pool)
        if score > best_score:
            best, best_score = wep, score
    return best


def find_modification(query: str) -> Modification | None:
    """Resolve a spoken modification name to a `Modification`, or None. Exact/alias first, then
    best token overlap so 'more backpack space' reaches Extra Backpack Capacity."""
    q = _norm(query)
    if not q:
        return None
    for mod in MODIFICATIONS:
        names = (_norm(mod.name), *(_norm(a) for a in mod.aliases))
        if q in names:
            return mod
    qt = _tokens(query)
    if not qt:
        return None
    best: Modification | None = None
    best_score = 0
    for mod in MODIFICATIONS:
        pool = _tokens(mod.name)
        for a in mod.aliases:
            pool |= _tokens(a)
        score = len(qt & pool)
        if score > best_score:
            best, best_score = mod, score
    return best


def engineers_for_modification(mod_name: str) -> list[OnFootEngineer]:
    """Every engineer who offers the named modification, bubble engineers first (nearer default).
    Empty when nothing matches — the caller then says so rather than guessing."""
    want = _norm(mod_name)
    if not want:
        return []
    hits = [e for e in ENGINEERS
            if want in {_norm(m) for m in (*e.suit_mods, *e.weapon_mods)}]
    hits.sort(key=lambda e: 0 if e.region == "bubble" else 1)
    return hits


def find_engineer(query: str) -> OnFootEngineer | None:
    """Resolve a spoken on-foot engineer name to a table entry, or None. Matches full name and
    any single name token ('domino', 'ferrari', 'beck')."""
    q = _norm(query)
    if not q:
        return None
    best: OnFootEngineer | None = None
    for eng in ENGINEERS:
        name = _norm(eng.name)
        if name == q:
            return eng
        tokens = set(name.split())
        if q in tokens or (len(q) >= 3 and q in name):
            best = best or eng
        elif set(q.split()) & tokens:
            best = best or eng
    return best


def upgrade_recipe(trio: tuple[str, str, str], components: tuple[str, str],
                   grade: int) -> GradeStep | None:
    """Public helper: the grade step for any (trio, components) pair — used by the capability
    when it already holds a suit's/weapon's recipe pieces."""
    return _grade_step(grade, trio, components)
