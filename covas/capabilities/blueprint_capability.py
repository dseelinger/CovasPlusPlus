"""Blueprint / material-sourcing capability (#66) — "what do I need for a grade-5 FSD, and
where do I farm what I'm short on?"

Journal-grounded and offline. It crosses the bundled engineering tables (`ed/blueprints.py`,
regenerated from EDCD community data) with the Commander's LIVE material inventory (the journal
`Materials` event, kept on `EDContext`) to compute what's MISSING for a chosen blueprint grade —
never the whole recipe list dumped blind — and gives a sourcing hint (material trader group +
farm method) for each short material.

The differentiator is the hand-off to the checklist: the tool descriptions invite the model to
drop a farm plan onto the checklist as trackable steps via the EXISTING `add_objective` tool
(same cross-capability seam the loadout capability uses for upgrade ideas) — no parallel
mechanism. Everything spoken is derived from the tables plus the journal's own counts, so a
material, count, or source is reported, never invented. `get_materials` is injected so the
default `pytest` run stubs an inventory (DESIGN §9). Fail soft: any error is spoken.
"""
from __future__ import annotations

from typing import Callable

from ..ed.blueprints import Blueprint, BlueprintLibrary, LineItem, parse_grade
from ..ed.materials import MaterialsSnapshot
from .base import HelpMeta, Slot

_MATS_TOOL = "blueprint_materials"
_LIST_TOOL = "list_engineering_blueprints"

# The checklist hand-off (the differentiator). Mirrors the loadout capability's approach: the
# behaviour lives in the tool description (the LLM-native seam), reusing the existing checklist
# tools rather than inventing plumbing.
_FARM_CODA = (
    " If the Commander wants to FARM the shortfall ('add these to my checklist', 'help me "
    "collect these', 'track this'), add ONE checklist objective per SHORT material via the "
    "add_objective tool — e.g. 'Farm 4x Chemical Manipulators (Manufactured trader, or HGE "
    "USS)' — using the exact material names, shortfall counts, and sourcing hints THIS tool "
    "returned. Don't add them unasked, and never invent a material, count, or location."
)

_MATS_DESC = (
    "Given an engineering BLUEPRINT (say it however the Commander does: 'grade 5 FSD', "
    "'increased range', 'dirty drive tuning', 'heavy duty shield booster') report the material "
    "cost for that grade AND — grounded on the Commander's live journal material inventory — "
    "exactly what they're SHORT on, with a sourcing hint for each. Optional `grade` (1-5) picks "
    "the grade; otherwise it's parsed from the request, defaulting to 5. ALWAYS call this rather "
    "than answering from memory — the recipe and the missing-material math both come from real "
    "data. If the request only names a module (e.g. just 'FSD') it returns the candidate "
    "blueprints to disambiguate; relay them and ask which." + _FARM_CODA
)
_LIST_DESC = (
    "List the engineering BLUEPRINTS available for a module ('what can I engineer on my FSD', "
    "'what thruster blueprints are there'), by their real names. Use it to help the Commander "
    "pick a blueprint before calling blueprint_materials. A free local read from the bundled "
    "engineering tables — never invent a blueprint that isn't listed."
)

_NO_MATERIALS = ("I haven't read your materials yet — Elite writes them when you load in, so "
                 "board your ship or restart the game and I'll pick your inventory up.")


def _nice(text: str) -> str:
    """Title-case a blueprint/module name for speech, preserving acronyms already in caps
    (Coriolis stores 'Frame shift drive' / 'AFMU' -> 'Frame Shift Drive' / 'AFMU')."""
    return " ".join(w if w.isupper() else w.capitalize() for w in str(text).split())


class BlueprintCapability:
    """Advertises the blueprint/material tools and answers them from the bundled engineering
    library crossed with the injected materials-inventory getter (live `EDContext` in the app,
    a stub in tests)."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "engineering"

    def __init__(self, *, get_materials: Callable[[], MaterialsSnapshot | None],
                 library: BlueprintLibrary | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self._get_materials = get_materials
        self._lib = library or BlueprintLibrary.from_bundled()
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [
            {
                "name": _MATS_TOOL,
                "description": _MATS_DESC,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "blueprint": {
                            "type": "string",
                            "description": "The blueprint as spoken — a name ('increased range', "
                                           "'dirty drives'), or a module + grade ('grade 5 FSD').",
                        },
                        "grade": {
                            "type": "integer",
                            "description": "Blueprint grade 1-5. Omit to parse it from the request "
                                           "(defaults to 5).",
                        },
                    },
                    "required": ["blueprint"],
                },
            },
            {
                "name": _LIST_TOOL,
                "description": _LIST_DESC,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "module": {
                            "type": "string",
                            "description": "The module to list blueprints for ('FSD', 'thrusters', "
                                           "'shield booster', 'power plant').",
                        },
                    },
                    "required": ["module"],
                },
            },
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="engineering blueprints",
            group="your ship",
            one_liner=("I work out the materials an engineering blueprint needs, check your live "
                       "inventory to see what you're short on, tell you where to farm it, and can "
                       "drop the farm plan onto your checklist."),
            example="what do I need for a grade 5 FSD",
            slots=(
                Slot(param="blueprint",
                     phrasings=("a blueprint", "increased range, dirty drives, heavy duty"),
                     example="what am I missing for grade 5 dirty drive tuning",
                     help_text="Name the blueprint — 'increased range', 'dirty drives', 'heavy "
                               "duty shield booster' — or a module and grade like 'grade 5 FSD'."),
                Slot(param="grade",
                     phrasings=("a grade", "grade 1 to 5"),
                     example="what does a grade 3 power plant overcharge need",
                     help_text="Say the grade, 1 to 5; I default to grade 5 if you don't."),
            ),
            help_when_active=("Ask what a blueprint needs and I'll tell you what you're short on "
                              "and where to farm it — say 'add these to my checklist' to track it."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _MATS_TOOL:
                return self._blueprint_materials(inp)
            if name == _LIST_TOOL:
                return self._list_blueprints(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Blueprint lookup error: {e}"

    # -- blueprint materials ------------------------------------------------------------
    def _blueprint_materials(self, inp: dict) -> str:
        query = str(inp.get("blueprint") or "").strip()
        if not query:
            return "Which blueprint? Name one, like 'grade 5 FSD' or 'dirty drive tuning'."

        scored = self._lib.resolve_scored(query)
        if not scored:
            return self._unknown_blueprint(query)
        # Equal top scores == the request only pinned a MODULE (e.g. "FSD"); disambiguate rather
        # than guess which of its blueprints was meant.
        top = scored[0][0]
        tied = [bp for s, bp in scored if s == top]
        if len(tied) > 1:
            names = ", ".join(f"{_nice(bp.name)} ({_nice(bp.module)})" for bp in tied[:6])
            return (f"Which blueprint did you mean? For that I have: {names}. "
                    "Say the blueprint name.")

        bp = tied[0]
        grade = self._grade(inp, query, bp)
        items = self._lib.line_items(bp, grade, self._snapshot())
        if not items:
            return f"I don't have a material recipe for {self._bp_label(bp)}."
        return self._speak_requirements(bp, grade, items)

    def _speak_requirements(self, bp: Blueprint, grade: int,
                            items: list[LineItem]) -> str:
        label = self._bp_label(bp)
        recipe = ", ".join(f"{li.need}x {li.info.name}" for li in items)
        head = (f"A grade {grade} {label} needs, per upgrade roll: {recipe}. "
                "(Reaching the grade takes several rolls, so stock up.)")

        if self._snapshot() is None:
            return f"{head} {_NO_MATERIALS}"

        missing = [li for li in items if li.missing]
        if not missing:
            return f"{head} You have everything for a roll — nothing to farm."

        short_lines = []
        for li in missing:
            src = f" — {li.info.source}" if li.info.source else ""
            short_lines.append(f"{li.short}x more {li.info.name} (you have {li.have}){src}")
        body = " ".join(f"{i + 1}. {ln}" for i, ln in enumerate(short_lines))
        return (f"{head} You're SHORT on {len(missing)} of them: {body} "
                "Want me to add this farm plan to your checklist?")

    # -- list blueprints ----------------------------------------------------------------
    def _list_blueprints(self, inp: dict) -> str:
        module = str(inp.get("module") or "").strip()
        if not module:
            return "Which module? Name one, like 'FSD', 'thrusters', or 'shield booster'."
        found = self._lib.blueprints_for_module(module)
        if not found:
            return (f"I don't have blueprints listed for '{module}'. Try a module like 'FSD', "
                    "'thrusters', 'power plant', or 'shield booster'.")
        modlabel = _nice(found[0].module or module)
        names = ", ".join(_nice(bp.name) for bp in found)
        return f"Blueprints for the {modlabel}: {names}."

    # -- helpers ------------------------------------------------------------------------
    def _snapshot(self) -> MaterialsSnapshot | None:
        snap = self._get_materials()
        return snap if isinstance(snap, MaterialsSnapshot) else None

    def _grade(self, inp: dict, query: str, bp: Blueprint) -> int:
        """The requested grade: explicit `grade` arg wins, else parsed from the spoken request
        (default 5). Clamped to 1..the blueprint's own max grade so we never ask for a grade the
        blueprint doesn't have."""
        raw = inp.get("grade")
        grade = int(raw) if isinstance(raw, (int, float)) else parse_grade(query)
        top = bp.max_grade or 5
        return max(1, min(grade, top))

    def _bp_label(self, bp: Blueprint) -> str:
        """'Increased Range on the Frame Shift Drive' — the blueprint plus its module."""
        return f"{_nice(bp.name)} on the {_nice(bp.module)}" if bp.module else _nice(bp.name)

    def _unknown_blueprint(self, query: str) -> str:
        """Validated fallback: never pretend a blueprint exists — offer a few REAL names."""
        sample = [self._lib.blueprint(k) for k in (
            "FSD_LongRange", "Engine_Dirty", "ShieldBooster_HeavyDuty", "PowerPlant_Boosted")]
        names = ", ".join(f"{_nice(bp.name)} ({_nice(bp.module)})" for bp in sample if bp is not None)
        return (f"I don't have a blueprint matching '{query}'. I know ones like: {names}. "
                "Name a blueprint or a module and grade.")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
