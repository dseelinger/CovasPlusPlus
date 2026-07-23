"""Ship loadout & engineering capability (N9) — answer "what's on my FSD" by voice.

LLM-native tools over the `LoadoutSnapshot` the journal watcher keeps on `EDContext`
(ed/loadout.py): engineering on a named module, all experimental effects, and the fitted
modules themselves. Everything spoken is derived from the snapshot plus the offline
symbol->name tables in `ed/module_names.py` — a module can only be named if it is genuinely
fitted, and stats come from the journal's own Modifiers, so nothing is ever invented.

Cross-capability by design, with NO new plumbing: the checklist tools are advertised on
every turn, so the tool descriptions simply invite the model to reason over the loadout,
suggest upgrades conversationally, and OFFER to record specific ones via the existing
`add_objective` tool. For "what's best" meta advice the descriptions steer it to web search
and honesty about uncertainty — never invented module stats or blueprint effects.

Local journal data only (no CAPI, no auth, no network). `get_loadout` is injected so the
default `pytest` run stubs a snapshot (DESIGN §9). Fail soft: any error is spoken.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from ..ed.loadout import LoadoutSnapshot, ShipModule
from ..ed.module_names import (
    blueprint_name,
    experimental_name,
    find_modules,
    modifier_label,
    module_name,
    slot_name,
)
from .base import HelpMeta, Slot

_ENG_TOOL = "ship_engineering"
_EXP_TOOL = "list_experimental_effects"
_MODS_TOOL = "ship_modules"

# Shared coda: the reasoning + checklist invitation (prompt task 5). Descriptions are the
# LLM-native seam, so the cross-capability behavior lives here, not in new plumbing.
_ADVICE = (
    " When the Commander asks for advice ('what should I upgrade', 'is this any good'), "
    "reason over what this tool returns and suggest improvements conversationally; OFFER to "
    "record specific upgrades on the checklist via the add_objective tool (don't add them "
    "unasked). For current 'best engineering' meta, use web search and say when you're "
    "unsure — NEVER invent module stats or blueprint effects."
)

_ENG_DESC = (
    "Report the ENGINEERING on the Commander's current ship, from the live journal loadout. "
    "Call with `module` for a specific one ('what's on my FSD / power plant / thrusters') — "
    "it returns the blueprint, grade, experimental effect, and the key modified stats. Call "
    "with no arguments for a rundown of every engineered module. A free local read — ALWAYS "
    "call it rather than answering from memory; if it says no loadout has been seen yet, "
    "relay that." + _ADVICE
)
_EXP_DESC = (
    "List every EXPERIMENTAL EFFECT fitted across the Commander's current ship ('what "
    "experimental effects do I have'), from the live journal loadout. A free local read."
    + _ADVICE
)
_MODS_DESC = (
    "List what's FITTED on the Commander's current ship, from the live journal loadout: no "
    "arguments for the full rundown (hardpoints, utilities, core, optional internals), or "
    "`module` for one module's detail ('what shield generator am I running', 'what's in my "
    "optional slots'). A free local read." + _ADVICE
)

_MODULE_ARG = {
    "module": {
        "type": "string",
        "description": "The module to look at, as spoken: a core name ('FSD', 'power "
                       "plant', 'thrusters', 'distributor'), a fitted item ('shield "
                       "generator', 'fuel scoop', 'multi-cannon'), or a slot ('medium "
                       "hardpoint 2'). Omit for the full rundown.",
    },
}

# Slots whose modules are cosmetic/structural noise in a spoken listing (still findable by
# name — they're only excluded from the no-argument rundown).
_LISTING_NOISE = {"shipcockpit", "cargohatch", "vesselvoice", "planetaryapproachsuite"}

_NO_LOADOUT = ("I haven't read your ship's loadout yet — board your ship or open "
               "outfitting and I'll pick it up from the journal.")


class LoadoutCapability:
    """Advertises the loadout/engineering tools and answers them from the injected
    snapshot getter (the live `EDContext.loadout_snapshot` in the app; a stub in tests)."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "commander_state"

    def __init__(self, *, get_loadout: Callable[[], LoadoutSnapshot | None],
                 log: Callable[[str], None] | None = None) -> None:
        self._get_loadout = get_loadout
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        no_args = {"type": "object", "properties": {}, "required": []}
        with_module = {"type": "object", "properties": dict(_MODULE_ARG), "required": []}
        return [
            {"name": _ENG_TOOL, "description": _ENG_DESC, "input_schema": with_module},
            {"name": _EXP_TOOL, "description": _EXP_DESC, "input_schema": no_args},
            {"name": _MODS_TOOL, "description": _MODS_DESC, "input_schema": with_module},
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="ship loadout",
            group="your ship",
            one_liner=("I read your ship's fitted modules and engineering from the journal "
                       "— blueprints, grades, experimental effects — and can suggest "
                       "upgrades."),
            example="what's the engineering on my FSD",
            slots=(
                Slot(param="module",
                     phrasings=("a module", "my FSD, power plant, or thrusters"),
                     example="what experimental effect is on my power distributor",
                     help_text="Name the module — FSD, power plant, thrusters, shield "
                               "generator, a weapon — and I'll read what's fitted and how "
                               "it's engineered."),
            ),
            help_when_active=("Ask about a specific module, your experimental effects, or "
                              "the whole loadout — and I can add upgrade ideas to your "
                              "checklist if you want."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _ENG_TOOL:
                return self._engineering(inp)
            if name == _EXP_TOOL:
                return self._experimentals()
            if name == _MODS_TOOL:
                return self._modules(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Loadout error: {e}"

    # -- engineering --------------------------------------------------------------------
    def _engineering(self, inp: dict) -> str:
        snap = self._snapshot()
        if snap is None:
            return _NO_LOADOUT
        query = str(inp.get("module") or "").strip()
        if query:
            return self._engineering_for(snap, query)

        engineered = snap.engineered_modules()
        if not engineered:
            return (f"No engineering on your {self._ship_label(snap)} — every fitted "
                    "module is stock.")
        lines = [self._engineering_line(m) for m in engineered]
        return (f"Engineering on your {self._ship_label(snap)}: " + " ".join(lines))

    def _engineering_for(self, snap: LoadoutSnapshot, query: str) -> str:
        matches = find_modules(snap, query)
        if not matches:
            return self._unknown_module(snap, query)
        parts = []
        for m in matches[:4]:                      # cap a broad match ("laser") for speech
            if m.engineering is None:
                parts.append(f"Your {module_name(m.item)} has no engineering — it's stock.")
            else:
                parts.append(self._engineering_line(m, detail=True))
        return " ".join(parts)

    def _engineering_line(self, m: ShipModule, *, detail: bool = False) -> str:
        eng = m.engineering
        assert eng is not None
        grade = f" grade {eng.level}" if eng.level else ""
        line = f"{module_name(m.item)}: {blueprint_name(eng.blueprint)}{grade}"
        exp = experimental_name(eng.experimental, eng.experimental_localised)
        if exp:
            line += f", with {exp}"
        if eng.engineer and detail:
            line += f" (by {eng.engineer})"
        line += "."
        if detail:
            mods = self._top_modifiers(m)
            if mods:
                line += f" Key changes: {mods}."
        return line

    def _top_modifiers(self, m: ShipModule, n: int = 3) -> str:
        """The largest stat changes, as spoken '+51% weapons recharge' fragments. Values come
        straight from the journal's Modifiers — reported, never computed from game knowledge."""
        eng = m.engineering
        if eng is None:
            return ""
        scored = [(mod, mod.pct_change()) for mod in eng.modifiers]
        scored = [(mod, pct) for mod, pct in scored if pct is not None and abs(pct) >= 0.5]
        scored.sort(key=lambda t: abs(t[1]), reverse=True)
        return ", ".join(f"{pct:+.0f}% {modifier_label(mod.label)}"
                         for mod, pct in scored[:n])

    # -- experimental effects -------------------------------------------------------------
    def _experimentals(self) -> str:
        snap = self._snapshot()
        if snap is None:
            return _NO_LOADOUT
        lines = []
        for m in snap.engineered_modules():
            eng = m.engineering
            exp = experimental_name(eng.experimental, eng.experimental_localised)
            if exp:
                lines.append(f"{exp} on the {module_name(m.item)}")
        if not lines:
            return f"No experimental effects fitted on your {self._ship_label(snap)}."
        return ("Experimental effects: " + "; ".join(lines) + ".")

    # -- modules ---------------------------------------------------------------------------
    def _modules(self, inp: dict) -> str:
        snap = self._snapshot()
        if snap is None:
            return _NO_LOADOUT
        query = str(inp.get("module") or "").strip()
        if query:
            matches = find_modules(snap, query)
            if not matches:
                return self._unknown_module(snap, query)
            return " ".join(self._module_line(m) for m in matches[:6])

        groups = self._grouped(snap)
        parts = [f"Your {self._ship_label(snap)}:"]
        for label, mods in groups:
            if mods:
                parts.append(f"{label}: {self._collapsed(mods)}.")
        if snap.max_jump_range:
            parts.append(f"Max jump range {snap.max_jump_range:.1f} light-years.")
        return " ".join(parts)

    def _module_line(self, m: ShipModule) -> str:
        line = f"{module_name(m.item)} in the {slot_name(m.slot)}"
        notes = []
        if not m.on:
            notes.append("switched off")
        if m.health is not None and m.health < 0.95:
            notes.append(f"damaged, {m.health * 100:.0f}%")
        if notes:
            line += f" ({', '.join(notes)})"
        line += "."
        if m.engineering is not None:
            line += " " + self._engineering_line(m, detail=True)
        return line

    def _grouped(self, snap: LoadoutSnapshot) -> list[tuple[str, list[ShipModule]]]:
        hard: list[ShipModule] = []
        utility: list[ShipModule] = []
        core: list[ShipModule] = []
        optional: list[ShipModule] = []
        for m in snap.modules:
            s = m.slot.lower()
            if s in _LISTING_NOISE:
                continue
            if s.startswith("tinyhardpoint"):
                utility.append(m)
            elif "hardpoint" in s:
                hard.append(m)
            elif s.startswith(("slot", "military")):
                optional.append(m)
            else:
                core.append(m)
        return [("Hardpoints", hard), ("Utilities", utility),
                ("Core", core), ("Optional internals", optional)]

    def _collapsed(self, mods: list[ShipModule]) -> str:
        """'3 medium gimballed Multi-Cannon, 0A Shield Booster' — duplicates counted so the
        spoken list stays short. An engineered module gets a spoken marker."""
        counts = Counter()
        for m in mods:
            name = module_name(m.item)
            if m.engineered:
                name += " (engineered)"
            counts[name] += 1
        return ", ".join(name if n == 1 else f"{n} {name}" for name, n in counts.items())

    # -- helpers ------------------------------------------------------------------------
    def _snapshot(self) -> LoadoutSnapshot | None:
        snap = self._get_loadout()
        return snap if isinstance(snap, LoadoutSnapshot) and snap.modules else None

    def _ship_label(self, snap: LoadoutSnapshot) -> str:
        ship = (snap.ship or "ship").replace("_", " ").title()
        return f"{ship} {snap.ship_name}" if snap.ship_name else ship

    def _unknown_module(self, snap: LoadoutSnapshot, query: str) -> str:
        """Validated fallback: never pretend the queried module exists — say what IS fitted
        (a few real names) so the Commander can re-ask."""
        fitted = []
        for m in snap.modules:
            if m.slot.lower() in _LISTING_NOISE:
                continue
            name = module_name(m.item)
            if name not in fitted:
                fitted.append(name)
        sample = ", ".join(fitted[:6])
        return (f"I don't see '{query}' on your {self._ship_label(snap)}. Fitted modules "
                f"include: {sample}.")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
