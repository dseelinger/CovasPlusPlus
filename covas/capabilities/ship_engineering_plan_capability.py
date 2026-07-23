"""Per-ship engineering planning capability (issue #135) — "what should I engineer next on my
Python", "what's left to G5 my FSD" — grounded on the ship's REMEMBERED build.

This is the conversational payoff of the engineering mega-feature. It joins four grounded sources,
reusing existing seams rather than duplicating them:

  * the PER-SHIP loadout memory (`ed/ship_loadouts.py`, keyed by the #134 ShipID) — HOW EACH OWNED
    SHIP is built, so it can plan a ship you're NOT currently flying, and never guesses a module;
  * the owned-ships registry (#134) — to resolve a spoken ship name ("my Python") to its ShipID;
  * the bundled blueprint library + live material inventory (#66) — to compute the material
    shortfall for a target grade (reusing `BlueprintLibrary.line_items`, never a second recipe table);
  * live `EngineerProgress` (#65) — which engineer applies a module's blueprint, and whether the
    Commander has unlocked them.

Everything spoken is DERIVED from parsed data — a remembered module, the journal's own Modifiers,
the bundled tables, the live inventory. Nothing is invented: a ship with no remembered loadout is
told so plainly, an un-engineered module is NOT given a made-up blueprint, and a material shortfall
is real math over real counts. The checklist bridge is the LLM-native seam the sibling capabilities
already use: the tool descriptions invite the model to record a plan via the EXISTING `add_objective`
tool — no parallel checklist mechanism.

All I/O is injected (owned-ships getter, per-ship loadout getter, active loadout getter, materials
getter, engineer-progress getter, blueprint library) so the default `pytest` run is offline and free
(DESIGN §9). Fail soft: any error is spoken, never raised into the voice loop.
"""
from __future__ import annotations

from collections.abc import Callable

from ..ed.blueprints import Blueprint, BlueprintLibrary
from ..ed.engineers import Engineer, find_by_specialty, status_for
from ..ed.loadout import LoadoutSnapshot, ShipModule
from ..ed.module_names import blueprint_name, find_modules, module_name
from ..ed.owned_ships import display_name, match_ships
from .base import HelpMeta, Slot

_STATUS_TOOL = "remembered_ship_build"
_PLAN_TOOL = "plan_engineering_upgrade"

# Core-internal slots worth surfacing as "engineerable but still stock" — the modules a Commander
# usually engineers. Matched on the raw Loadout Item symbol so it's stable/non-localised.
_ENGINEERABLE_CORE = (
    ("hyperdrive", "frame shift drive"),
    ("engine", "thrusters"),
    ("powerplant", "power plant"),
    ("powerdistributor", "power distributor"),
    ("shieldgenerator", "shield generator"),
    ("sensors", "sensors"),
    ("lifesupport", "life support"),
    ("fuelscoop", "fuel scoop"),
)

# Cap the spoken lists so a fully-kitted ship doesn't read out forever.
_MAX_ENG = 6
_MAX_STOCK = 5

# The checklist hand-off (the differentiator, shared with blueprint/loadout capabilities). Behaviour
# lives in the tool description — the LLM-native seam — reusing the existing checklist CRUD.
_CHECKLIST_CODA = (
    " If the Commander wants to TRACK this ('add it to my checklist', 'help me plan this'), add ONE "
    "checklist objective per step via the add_objective tool — naming the ship, the module, the "
    "target grade, the engineer, and any material shortfall THIS tool reported. Use the exact names "
    "and counts returned; don't add anything unasked, and never invent a module, blueprint, "
    "material, count, or engineer."
)


class ShipEngineeringPlanCapability:
    """Advertises the per-ship engineering status + planning tools, answering them from the injected
    remembered-loadout / owned-ships / materials / engineer-progress seams crossed with the bundled
    blueprint library (live EDContext in the app; stubs in tests)."""
    # Tiering group (issue #84): shares the engineering token-budget cluster with blueprints,
    # engineers, loadout and owned-ships.
    TIERING_GROUP = "engineering"

    def __init__(
        self,
        *,
        get_owned: Callable[[], list[dict]],
        get_ship_loadout: Callable[[str], LoadoutSnapshot | None],
        get_active_loadout: Callable[[], LoadoutSnapshot | None],
        get_materials: Callable[[], object],
        get_progress: Callable[[], dict],
        library: BlueprintLibrary | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._get_owned = get_owned
        self._get_ship_loadout = get_ship_loadout
        self._get_active_loadout = get_active_loadout
        self._get_materials = get_materials
        self._get_progress = get_progress
        self._lib = library or BlueprintLibrary.from_bundled()
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        ship_arg = {
            "ship": {
                "type": "string",
                "description": "Which owned ship, as spoken ('my Python', 'the Anaconda', a custom "
                               "name or ident). Omit to use the ship the Commander is currently "
                               "flying.",
            },
        }
        return [
            {
                "name": _STATUS_TOOL,
                "description": (
                    "Report the REMEMBERED engineering build of one of the Commander's owned ships "
                    "— which modules are engineered (blueprint + grade) and which are still stock — "
                    "from the per-ship config memory (persisted per ShipID, so it works for a ship "
                    "they're NOT currently flying and survives ship switches / restarts). Use for "
                    "'what's the engineering on my Python', 'what should I engineer next on my "
                    "Anaconda', 'which of my ships still need work'. ALWAYS call this rather than "
                    "answering from memory. If no build has been remembered for that ship yet, it "
                    "says so — do not guess a loadout." + _CHECKLIST_CODA
                ),
                "input_schema": {"type": "object", "properties": dict(ship_arg), "required": []},
            },
            {
                "name": _PLAN_TOOL,
                "description": (
                    "Plan engineering a specific MODULE on an owned ship up to a target grade, "
                    "grounded on the ship's remembered build + the Commander's live material "
                    "inventory + engineer unlock status. Report the module's CURRENT engineering "
                    "(from memory), the material shortfall for the target grade, and which engineer "
                    "applies it (with unlock status). Use for 'what's left to G5 my FSD', 'plan "
                    "grade 5 dirty drives on my Python'. Pass `module` (required) and optional "
                    "`ship` / `target_grade` (1-5, default 5). If the module is still stock, it "
                    "asks which blueprint rather than guessing. For a generic recipe comparison not "
                    "tied to a remembered ship, use blueprint_materials instead." + _CHECKLIST_CODA
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        **ship_arg,
                        "module": {
                            "type": "string",
                            "description": "The module to plan, as spoken ('FSD', 'thrusters', "
                                           "'power distributor', 'shield generator').",
                        },
                        "target_grade": {
                            "type": "integer",
                            "description": "Target blueprint grade 1-5. Omit for grade 5.",
                        },
                    },
                    "required": ["module"],
                },
            },
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="engineering planning",
            group="your ship",
            one_liner=("I remember how each of your ships is engineered and help you plan what to "
                       "upgrade next — the materials you're short on, which engineer to see — and "
                       "can drop the plan onto your checklist."),
            example="what should I engineer next on my Python",
            slots=(
                Slot(param="ship",
                     phrasings=("one of your ships", "my Python, the Anaconda"),
                     example="what's the engineering on my Anaconda",
                     help_text="Name an owned ship and I'll recall its build — or leave it out for "
                               "the ship you're flying."),
                Slot(param="module",
                     phrasings=("a module to plan", "my FSD, thrusters, power distributor"),
                     example="what's left to grade 5 my FSD",
                     help_text="Name a module and (optionally) a target grade, and I'll tell you "
                               "what you're short on and which engineer applies it."),
            ),
            help_when_active=("Ask what's engineered on one of your ships, or ask me to plan an "
                              "upgrade — I'll ground it on the ship's real build and add it to your "
                              "checklist if you want."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _STATUS_TOOL:
                return self._status(inp)
            if name == _PLAN_TOOL:
                return self._plan(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Engineering-planning error: {e}"

    # -- remembered_ship_build --------------------------------------------------------
    def _status(self, inp: dict) -> str:
        resolved = self._resolve_ship(str(inp.get("ship") or "").strip())
        if isinstance(resolved, str):        # a disambiguation / "no ship" message
            return resolved
        label, snap = resolved
        engineered = snap.engineered_modules()
        parts = [f"{label} — remembered build:"]
        if engineered:
            frags = [self._eng_fragment(m) for m in engineered[:_MAX_ENG]]
            if len(engineered) > _MAX_ENG:
                frags.append(f"and {len(engineered) - _MAX_ENG} more engineered")
            parts.append("engineered — " + "; ".join(frags) + ".")
        else:
            parts.append("nothing engineered yet — every module is stock.")
        stock = self._stock_core(snap)
        if stock:
            names = ", ".join(stock[:_MAX_STOCK])
            more = f", and {len(stock) - _MAX_STOCK} more" if len(stock) > _MAX_STOCK else ""
            parts.append(f"Still stock: {names}{more}.")
        parts.append("Ask me to plan an upgrade on any of them.")
        return " ".join(parts)

    def _eng_fragment(self, m: ShipModule) -> str:
        eng = m.engineering
        assert eng is not None
        grade = f" grade {eng.level}" if eng.level else ""
        return f"{module_name(m.item)}: {blueprint_name(eng.blueprint)}{grade}"

    def _stock_core(self, snap: LoadoutSnapshot) -> list[str]:
        """Fitted core modules that are commonly engineered but still stock, by spoken name — a
        grounded 'what's left to do' list (only modules the ship really has, really un-engineered)."""
        out: list[str] = []
        for m in snap.modules:
            if m.engineered:
                continue
            item = m.item.lower()
            for needle, spoken in _ENGINEERABLE_CORE:
                if needle in item and spoken not in out:
                    out.append(spoken)
                    break
        return out

    # -- plan_engineering_upgrade -----------------------------------------------------
    def _plan(self, inp: dict) -> str:
        module_q = str(inp.get("module") or "").strip()
        if not module_q:
            return "Which module should I plan? Name one, like 'FSD' or 'thrusters'."
        resolved = self._resolve_ship(str(inp.get("ship") or "").strip())
        if isinstance(resolved, str):
            return resolved
        label, snap = resolved

        matches = find_modules(snap, module_q)
        if not matches:
            return self._unknown_module(label, snap, module_q)
        m = matches[0]
        target = self._target_grade(inp)
        eng_line = self._engineer_line(module_q)

        if m.engineering is None:
            # Never invent a blueprint for a stock module — ask which, offering the real options.
            options = self._lib.blueprints_for_module(module_q)
            opt = (" Options: " + ", ".join(blueprint_name(bp.key) for bp in options[:6]) + "."
                   if options else "")
            return (f"Your {module_name(m.item)} on the {label} is still stock — which blueprint "
                    f"did you want to plan?{opt}{eng_line}")

        eng = m.engineering
        current = f"grade {eng.level}" if eng.level else "engineered"
        bp_label = blueprint_name(eng.blueprint)
        head = f"Your {module_name(m.item)} on the {label} has {bp_label} ({current})."
        if eng.level and eng.level >= target:
            return (f"{head} That's already at grade {target} or better — nothing to plan there."
                    f"{eng_line}")

        bp = self._lib.blueprint(eng.blueprint)
        if bp is None:
            return (f"{head} I don't have a bundled recipe for {bp_label}, so I can't work out the "
                    f"materials for grade {target}.{eng_line}")
        return self._plan_body(head, bp, target, eng_line)

    def _plan_body(self, head: str, bp: Blueprint, target: int, eng_line: str) -> str:
        snapshot = self._materials()
        items = self._lib.line_items(bp, target, snapshot)
        if not items:
            return f"{head} I don't have a grade {target} recipe for it.{eng_line}"
        recipe = ", ".join(f"{li.need}x {li.info.name}" for li in items)
        body = (f"{head} To grade {target}, a roll needs: {recipe}.")
        if snapshot is None:
            body += (" I haven't read your materials yet — board your ship and I'll tell you what "
                     "you're short on.")
        else:
            missing = [li for li in items if li.missing]
            if not missing:
                body += " You have everything for a roll."
            else:
                shorts = "; ".join(f"{li.short}x more {li.info.name}" for li in missing)
                body += f" You're SHORT on: {shorts}."
        body += eng_line
        body += " Want me to add this to your checklist?"
        return body

    def _engineer_line(self, module_q: str) -> str:
        """' Engineer: Farseer (unlocked, grade 5).' — who applies this module's blueprints, with
        the Commander's live unlock status. Empty when no engineer is known for the module."""
        engs = find_by_specialty(module_q)
        if not engs:
            return ""
        progress = self._progress()
        best = self._best_engineer(engs, progress)
        st = status_for(best, progress)
        if st is None or st.progress not in ("Known", "Invited", "Unlocked"):
            return f" Engineer: {best.name} at {best.station} (not yet unlocked)."
        if st.progress == "Unlocked":
            grade = f", grade {st.rank}" if st.rank else ""
            return f" Engineer: {best.name} (unlocked{grade})."
        return f" Engineer: {best.name} ({st.progress.lower()})."

    def _best_engineer(self, engs: list[Engineer], progress: dict) -> Engineer:
        """Prefer an unlocked engineer at the highest grade, else the first listed (bubble-first)."""
        def rank(e: Engineer) -> tuple[int, int]:
            st = status_for(e, progress)
            if st is not None and st.progress == "Unlocked":
                return (1, st.rank or 0)
            return (0, 0)
        return max(engs, key=rank)

    # -- ship resolution --------------------------------------------------------------
    def _resolve_ship(self, ship_q: str):
        """Resolve a spoken ship to `(label, remembered LoadoutSnapshot)`, or a spoken message
        (str) to relay when it can't. Blank query -> the active ship; a name -> the owned-ships
        registry match. A resolved ship with no remembered build yields the honest 'board it' line."""
        owned = self._get_owned() or []
        if ship_q:
            entries = {str(r["ship_id"]): r for r in owned if r.get("ship_id") is not None}
            matches = match_ships(entries, ship_q)
            if not matches:
                return self._no_match(ship_q, owned)
            if len(matches) > 1:
                names = ", ".join(display_name(rec) for _sid, rec in matches[:6])
                return (f"More than one of your ships matches '{ship_q}' — {names}. Which one?")
            sid, rec = matches[0]
            return self._with_build(sid, display_name(rec))
        # No ship named -> the ship being flown (its ShipID from the live active loadout).
        active = self._get_active_loadout()
        sid = getattr(active, "ship_id", None) if active is not None else None
        if sid is None:
            # No active ship known; fall back to a single remembered ship if that's unambiguous.
            active_rec = next((r for r in owned if r.get("active")), None)
            if active_rec and active_rec.get("ship_id") is not None:
                return self._with_build(str(active_rec["ship_id"]), display_name(active_rec))
            return ("I'm not sure which ship you mean — board one, or name it "
                    "(like 'my Python').")
        label = self._label_for(str(sid), owned) or self._active_label(active)
        return self._with_build(str(sid), label)

    def _with_build(self, sid: str, label: str):
        snap = self._get_ship_loadout(sid)
        if not isinstance(snap, LoadoutSnapshot) or not snap.modules:
            return (f"I don't have a remembered build for your {label} yet — board it (or open "
                    "outfitting) and I'll capture its modules and engineering.")
        return label, snap

    def _label_for(self, sid: str, owned: list[dict]) -> str | None:
        rec = next((r for r in owned if str(r.get("ship_id")) == sid), None)
        return display_name(rec) if rec else None

    def _active_label(self, active: LoadoutSnapshot) -> str:
        ship = (active.ship or "ship").replace("_", " ").title()
        return f'{ship} "{active.ship_name}"' if active.ship_name else ship

    def _no_match(self, ship_q: str, owned: list[dict]) -> str:
        if not owned:
            return ("I haven't recorded any of your ships yet — board your ships and I'll remember "
                    "how each is built.")
        sample = ", ".join(display_name(r) for r in owned[:8])
        return f"I don't see an owned ship matching '{ship_q}'. You own: {sample}."

    def _unknown_module(self, label: str, snap: LoadoutSnapshot, query: str) -> str:
        fitted: list[str] = []
        for m in snap.modules:
            name = module_name(m.item)
            if name not in fitted:
                fitted.append(name)
        sample = ", ".join(fitted[:6])
        return (f"I don't see '{query}' on your {label}. Fitted modules include: {sample}.")

    # -- helpers ----------------------------------------------------------------------
    def _target_grade(self, inp: dict) -> int:
        raw = inp.get("target_grade")
        grade = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 5
        return max(1, min(grade, 5))

    def _materials(self):
        from ..ed.materials import MaterialsSnapshot
        snap = self._get_materials()
        return snap if isinstance(snap, MaterialsSnapshot) else None

    def _progress(self) -> dict:
        try:
            prog = self._get_progress()
            return dict(prog) if isinstance(prog, dict) else {}
        except Exception as e:  # noqa: BLE001 — a bad getter must not break the answer
            self._logline(f"progress read failed: {e}")
            return {}

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
