"""On-foot (Odyssey suit/weapon) engineering capability (#73) — "how do I engineer my Maverick
suit", "who unlocks Greater Range", "give me the full on-foot engineering breakdown".

The on-foot sibling of the ship EngineersCapability + BlueprintCapability. Answers from the
BUNDLED offline reference (`ed/odyssey_engineering.py`) — suits, weapons, the modification
catalogue, and the 13 on-foot engineers with their locations, unlock tasks and offered mods —
so a suit/weapon/modification/engineer question is grounded in DATA, never a vague LLM guess.

One tool, `on_foot_engineering`, with four optional selectors (suit / weapon / modification /
engineer); with none it speaks a short overview. When an engineer is named it can join the
Commander's LIVE unlock status from the journal `EngineerProgress` event (on-foot engineers
share that event with ship engineers) and copy their system to the clipboard for plotting —
both are INJECTED so the default `pytest` run is offline and free (DESIGN §9).

NOTE (deferred): cross-referencing the Commander's live suit/weapon MATERIAL stock (ShipLocker /
BackPack) is a stretch goal — there is no ShipLocker/Backpack parsing yet — so recipes report
what's NEEDED, not what you're short on. Fail soft: any error is spoken, never raised.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..ed import odyssey_engineering as ody
from ..ed.engineers import EngineerStatus, status_for
from .base import HelpMeta, Slot

_TOOL = "on_foot_engineering"

_DESC = (
    "Answer ODYSSEY ON-FOOT (suit & weapon) engineering questions from bundled data — suits "
    "(Maverick / Dominator / Artemis), handheld weapons (Karma / TK / Manticore), the "
    "modification catalogue, and the 13 on-foot engineers. Pass whichever selector the "
    "Commander named:\n"
    "  * `suit` ('how do I engineer my Maverick', 'upgrade my Dominator') -> the grade 1-5 "
    "upgrade recipe (materials per grade + where to source them) and which modifications suit "
    "engineers offer.\n"
    "  * `weapon` ('engineer my Karma AR-50', 'upgrade my Manticore Oppressor') -> the same for "
    "that weapon, plus its family and damage type.\n"
    "  * `modification` ('who gives Greater Range', 'which engineer does extra backpack') -> the "
    "engineers who offer that perk, each tagged with the Commander's unlock status.\n"
    "  * `engineer` ('where is Domino Green', 'how do I unlock Hero Ferrari') -> their location, "
    "how to access + unlock them, who they refer you to, the mods they offer, and it copies "
    "their system to the clipboard for plotting.\n"
    "With NO selector it gives a short overview of on-foot engineering. ALWAYS call this rather "
    "than answering from memory — locations, recipes and unlock tasks all come from real data."
)

_ARGS = {
    "type": "object",
    "properties": {
        "suit": {"type": "string",
                 "description": "A suit as spoken: 'Maverick', 'Dominator', 'Artemis'."},
        "weapon": {"type": "string",
                   "description": "A weapon as spoken: 'Karma AR-50', 'TK Aphelion', "
                                  "'Manticore Oppressor', 'plasma pistol'."},
        "modification": {"type": "string",
                         "description": "A modification/perk: 'Greater Range', 'Extra Backpack "
                                        "Capacity', 'Night Vision', 'Magazine Size'."},
        "engineer": {"type": "string",
                     "description": "An on-foot engineer: 'Domino Green', 'Hero Ferrari', "
                                    "'Wellington Beck', 'Yi Shen'."},
        "grade": {"type": "integer",
                  "description": "For a suit/weapon, the target grade 1-5 (default 5)."},
    },
    "required": [],
}

_NO_PROGRESS = ("I haven't read your engineer progress yet — it comes from the journal's "
                "EngineerProgress event, which Elite writes at login. Start the game with "
                "monitoring on and I'll pick it up.")


class OnFootEngineeringCapability:
    """Advertises the on-foot engineering read tool and answers it from the bundled Odyssey
    reference, optionally joined with the injected journal-progress getter (live EDContext in
    the app; a stub/None in tests). Static data — no network."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "engineering"

    def __init__(
        self,
        *,
        get_progress: Callable[[], dict] | None = None,
        get_current_system: Callable[[], Optional[str]] | None = None,
        clipboard: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._get_progress = get_progress
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _TOOL, "description": _DESC, "input_schema": dict(_ARGS)}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="on-foot engineering",
            group="your ship",
            one_liner=("I know Odyssey on-foot engineering — suit and weapon grade upgrades and "
                       "their materials, the modification perks, and which of the 13 on-foot "
                       "engineers unlocks each, where they are and how to reach them."),
            example="how do I engineer my Maverick suit",
            slots=(
                Slot(param="suit",
                     phrasings=("a suit", "Maverick, Dominator, Artemis"),
                     example="what materials to upgrade my Dominator to grade 5",
                     help_text="Name a suit — Maverick, Dominator or Artemis — for its grade "
                               "1-5 upgrade materials and the mods you can add."),
                Slot(param="weapon",
                     phrasings=("a weapon", "Karma AR-50, TK Aphelion, Manticore Oppressor"),
                     example="how do I engineer my Karma AR-50",
                     help_text="Name a handheld weapon for its grade-upgrade materials, family "
                               "and damage type."),
                Slot(param="modification",
                     phrasings=("a modification", "Greater Range, Extra Backpack, Night Vision"),
                     example="which engineer gives Greater Range",
                     help_text="Name a suit or weapon modification and I'll tell you which "
                               "engineers offer it."),
                Slot(param="engineer",
                     phrasings=("an on-foot engineer", "Domino Green, Hero Ferrari, Yi Shen"),
                     example="how do I unlock Domino Green",
                     help_text="Name an on-foot engineer for their location, unlock task, "
                               "referral and the mods they offer."),
            ),
            help_when_active=("Ask by suit, weapon, modification or engineer — or just ask for "
                              "the full on-foot engineering breakdown."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL:
            return f"Unknown tool: {name}"
        try:
            suit = str(inp.get("suit") or "").strip()
            weapon = str(inp.get("weapon") or "").strip()
            mod = str(inp.get("modification") or "").strip()
            engineer = str(inp.get("engineer") or "").strip()
            if engineer:
                return self._engineer(engineer)
            if suit:
                return self._suit(suit, inp)
            if weapon:
                return self._weapon(weapon, inp)
            if mod:
                return self._modification(mod)
            return self._overview()
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"On-foot engineering lookup error: {e}"

    # -- suit / weapon grade upgrades -------------------------------------------------
    def _suit(self, query: str, inp: dict) -> str:
        suit = ody.find_suit(query)
        if suit is None:
            names = ", ".join(s.name for s in ody.SUITS)
            return (f"I don't recognise the suit '{query}'. The engineerable suits are: {names} "
                    "(the basic Flight Suit can't be engineered).")
        grade = self._grade(inp)
        head = f"{suit.name}: {suit.role}"
        recipe = self._recipe_line(suit.grade_step(grade), grade)
        mods = self._suit_mod_offers()
        return f"{head} {recipe} {mods}"

    def _weapon(self, query: str, inp: dict) -> str:
        wep = ody.find_weapon(query)
        if wep is None:
            fam = ", ".join(sorted({w.family for w in ody.WEAPONS}))
            return (f"I don't recognise the weapon '{query}'. On-foot weapons come from the "
                    f"{fam} families — try 'Karma AR-50', 'TK Aphelion' or 'Manticore Oppressor'.")
        grade = self._grade(inp)
        head = (f"The {wep.name} is a {wep.family} {wep.kind} dealing {wep.damage} damage.")
        recipe = self._recipe_line(wep.grade_step(grade), grade)
        mods = self._weapon_mod_offers()
        return f"{head} {recipe} {mods}"

    def _recipe_line(self, step: Optional[ody.GradeStep], grade: int) -> str:
        if step is None:
            return (f"Grade {grade} is the base item — no upgrade materials. Upgrades run "
                    "grade 2 to 5, applied at any Pioneer Supplies vendor.")
        mats = ", ".join(f"{n}x {m}" for m, n in step.materials)
        srcs = self._sources(step)
        tail = f" Sourcing — {srcs}" if srcs else ""
        return (f"To reach grade {grade} you need, per the upgrade: {mats}. "
                f"(Apply it at any concourse with a Pioneer Supplies vendor.){tail}")

    def _sources(self, step: ody.GradeStep) -> str:
        # A grade step's materials are all distinct, so no dedup needed.
        parts = [f"{mat}: {src}"
                 for mat, _n in step.materials
                 if (src := ody.MATERIAL_SOURCES.get(mat))]
        return " ".join(parts)

    def _grade(self, inp: dict) -> int:
        raw = inp.get("grade")
        grade = int(raw) if isinstance(raw, (int, float)) else ody.MAX_GRADE
        return max(1, min(grade, ody.MAX_GRADE))

    def _suit_mod_offers(self) -> str:
        names = sorted({m.name for m in ody.MODIFICATIONS if m.target == "suit"})
        return ("Suit modifications you can add (from on-foot engineers): "
                + ", ".join(names) + ". Ask 'which engineer gives <mod>' for who unlocks one.")

    def _weapon_mod_offers(self) -> str:
        names = sorted({m.name for m in ody.MODIFICATIONS if m.target == "weapon"})
        return ("Weapon modifications you can add: " + ", ".join(names)
                + ". Ask 'which engineer gives <mod>' for who unlocks one.")

    # -- modification -> engineers ----------------------------------------------------
    def _modification(self, query: str) -> str:
        mod = ody.find_modification(query)
        if mod is None:
            return (f"I don't have '{query}' as an on-foot modification. Try things like Greater "
                    "Range, Extra Backpack Capacity, Night Vision, Magazine Size or Stability.")
        engs = ody.engineers_for_modification(mod.name)
        head = f"{mod.name} ({mod.target}): {mod.effect}"
        if not engs:
            return f"{head} I don't have an engineer listed for it."
        progress = self._progress()
        tags = []
        for e in engs:
            tag = self._short_status(e, progress)
            tags.append(f"{e.name} ({e.system}{', ' + tag if tag else ''})")
        return f"{head} Offered by: " + "; ".join(tags) + "."

    # -- engineer ---------------------------------------------------------------------
    def _engineer(self, query: str) -> str:
        eng = ody.find_engineer(query)
        if eng is None:
            names = ", ".join(e.name for e in ody.ENGINEERS[:6])
            return (f"I don't recognise the on-foot engineer '{query}'. Some I know: {names} — "
                    "and others. Try another name.")
        progress = self._progress()
        status = status_for(eng, progress) if progress else None
        region = " (Colonia region)" if eng.region == "colonia" else ""
        parts = [f"{eng.name} is at {eng.settlement} in {eng.system}{region}, and modifies "
                 f"{eng.modifies}."]
        parts.append(self._status_sentence(eng, status, progress))
        if eng.suit_mods:
            parts.append("Suit mods: " + ", ".join(eng.suit_mods) + ".")
        if eng.weapon_mods:
            parts.append("Weapon mods: " + ", ".join(eng.weapon_mods) + ".")
        if eng.refers_to and eng.referral:
            parts.append(f"They refer you to {eng.refers_to} — {eng.referral}")
        parts.append(self._deliver(eng))
        return " ".join(p for p in parts if p)

    def _status_sentence(self, eng: ody.OnFootEngineer, status: Optional[EngineerStatus],
                         progress: dict) -> str:
        """The grounded 'where you stand + what's left' sentence for one engineer."""
        if not progress:
            return f"To access them: {eng.access} To unlock: {eng.unlock}"
        if status is None or status.progress not in ("Known", "Invited", "Unlocked", "Barred"):
            return f"You haven't started with them. Access: {eng.access} Unlock: {eng.unlock}"
        if status.progress == "Unlocked":
            grade = f" up to grade {status.rank}" if status.rank else ""
            return f"You've UNLOCKED them{grade} — visit to modify your suits and weapons."
        if status.progress == "Invited":
            return f"You've been INVITED — you can visit them. To unlock: {eng.unlock}"
        if status.progress == "Barred":
            return "You're currently barred from this engineer."
        return f"You've DISCOVERED them but not unlocked them. Next: {eng.unlock}"

    def _short_status(self, eng: ody.OnFootEngineer, progress: dict) -> str:
        """A terse per-engineer tag for the by-modification list."""
        if not progress:
            return ""
        st = status_for(eng, progress)
        if st is None or st.progress not in ("Known", "Invited", "Unlocked", "Barred"):
            return "not unlocked"
        if st.progress == "Unlocked":
            return "UNLOCKED"
        return st.progress.lower()

    # -- overview ---------------------------------------------------------------------
    def _overview(self) -> str:
        bubble = sum(1 for e in ody.ENGINEERS if e.region == "bubble")
        colonia = len(ody.ENGINEERS) - bubble
        return (
            "Odyssey on-foot engineering has two halves. First, SUIT and WEAPON grade upgrades "
            "(grade 1 to 5) that raise base stats — applied at any Pioneer Supplies vendor with "
            "materials; the suits are the Maverick, Dominator and Artemis, the weapons are the "
            "Karma (kinetic), TK (laser) and Manticore (plasma) families. Second, MODIFICATIONS "
            "— perks like Greater Range, Extra Backpack Capacity or Night Vision — applied by "
            f"on-foot ENGINEERS: {bubble} in the bubble and {colonia} in Colonia. Ask about a "
            "suit or weapon for its upgrade materials, a modification for who unlocks it, or an "
            "engineer for their location and unlock task.")

    # -- helpers ----------------------------------------------------------------------
    def _deliver(self, eng: ody.OnFootEngineer) -> str:
        """Copy the engineer's system for plotting, unless already there / no clipboard."""
        if self._clipboard is None:
            return ""
        current = self._current_system() if self._current_system else None
        if current and current.strip().lower() == eng.system.strip().lower():
            return "You're already in that system."
        try:
            self._clipboard(eng.system)
            return f"I've copied {eng.system} to your clipboard to plot a route."
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return ""

    def _progress(self) -> dict:
        if self._get_progress is None:
            return {}
        try:
            prog = self._get_progress()
            return dict(prog) if isinstance(prog, dict) else {}
        except Exception as e:  # noqa: BLE001 — a bad getter must not break the answer
            self._logline(f"progress read failed: {e}")
            return {}

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
