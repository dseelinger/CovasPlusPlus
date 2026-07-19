"""Engineers finder capability (issue #65) — "who unlocks X, where are they, what's left".

LLM-native tools over the bundled engineers reference table (`ed/engineers.py`) JOINED with
the Commander's live `EngineerProgress` journal state (kept on `EDContext`). That join is the
whole point: the table supplies the location + requirement, the journal supplies where *this*
Commander actually stands — Known / Invited / Unlocked — so "what do I still need" is a real,
grounded answer rather than generic wiki recitation.

  * `find_engineer`          — locate an engineer (by name, or by the module you want to
                               engineer), report their journal unlock status + what's left,
                               and copy their system to the clipboard for plotting.
  * `engineer_unlock_status` — a journal-grounded rundown: what's unlocked, what's in
                               progress, and what's still locked.

All I/O is injected — the progress getter, current-system getter, and clipboard — so the
default `pytest` run is offline and free (DESIGN §9). Static reference data, no network at
runtime. Fail soft: any error is spoken, never raised into the voice loop.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..ed.engineers import (ENGINEERS, Engineer, EngineerStatus, find_by_specialty,
                            find_engineer, status_for)
from .base import HelpMeta, Slot

_FIND_TOOL = "find_engineer"
_STATUS_TOOL = "engineer_unlock_status"

_FIND_DESC = (
    "Find an Elite Dangerous ENGINEER and report the Commander's own unlock progress with "
    "them from the live journal. Call with `engineer` for a specific one ('where is Felicity "
    "Farseer', 'how do I unlock The Dweller') — it gives their system and base, what they "
    "engineer, the Commander's journal status (not yet started / invited / unlocked and at "
    "what grade), what's still needed, and copies their system to the clipboard for plotting. "
    "Call with `module` to answer 'which engineer upgrades my X' ('who does FSDs / shields / "
    "multi-cannons') — it lists the engineers for that module, each tagged with whether the "
    "Commander has unlocked them yet. ALWAYS call this rather than answering engineer "
    "questions from memory: unlock status is journal-grounded and changes as they play."
)
_STATUS_DESC = (
    "Give the Commander a journal-grounded rundown of their ENGINEER unlock progress: how many "
    "are unlocked, which are part-way (invited or discovered), and which are still locked with "
    "what each still needs. Use for 'which engineers have I unlocked', 'what engineers do I "
    "still need', 'how's my engineering unlocking going'. Reads the live journal EngineerProgress "
    "— ALWAYS call it rather than guessing; if no progress has been read yet, it says so."
)

_NO_ARGS = {"type": "object", "properties": {}, "required": []}
_FIND_ARGS = {
    "type": "object",
    "properties": {
        "engineer": {
            "type": "string",
            "description": "An engineer's name as spoken ('Farseer', 'Tod McQuinn', 'The "
                           "Dweller'). Omit if asking by module instead.",
        },
        "module": {
            "type": "string",
            "description": "A module / weapon type to find engineers for ('FSD', 'shields', "
                           "'thrusters', 'multi-cannon', 'power plant'). Omit if naming an "
                           "engineer instead.",
        },
    },
    "required": [],
}

_NO_PROGRESS = ("I haven't read your engineer progress yet — it comes from the journal's "
                "EngineerProgress event, which Elite writes at login. Start the game (with "
                "monitoring on) and I'll pick it up.")


class EngineersCapability:
    """Advertises the engineer-finder tools and answers them from the bundled reference table
    joined with the injected journal-progress getter (live EDContext in the app; a stub in
    tests). Static data — no network."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "engineering"

    def __init__(
        self,
        *,
        get_progress: Callable[[], dict],
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
        return [
            {"name": _FIND_TOOL, "description": _FIND_DESC, "input_schema": dict(_FIND_ARGS)},
            {"name": _STATUS_TOOL, "description": _STATUS_DESC, "input_schema": dict(_NO_ARGS)},
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="engineers",
            group="your ship",
            one_liner=("I find Elite's engineers — who upgrades a given module, where they "
                       "are — and read your journal to tell you what you've unlocked and "
                       "what's still needed."),
            example="which engineer unlocks my FSD",
            slots=(
                Slot(param="engineer",
                     phrasings=("an engineer by name", "Farseer, The Dweller, Tod McQuinn"),
                     example="where is Felicity Farseer and how do I unlock her",
                     help_text="Name an engineer and I'll give their location, what they "
                               "engineer, your unlock status from the journal, and copy their "
                               "system for plotting."),
                Slot(param="module",
                     phrasings=("a module or weapon", "FSD, shields, thrusters, multi-cannons"),
                     example="who engineers shield boosters",
                     help_text="Name a module or weapon and I'll list the engineers who "
                               "upgrade it and whether you've unlocked each."),
            ),
            help_when_active=("Ask by engineer name, by the module you want to engineer, or "
                              "ask what you've unlocked so far and what's left."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _FIND_TOOL:
                return self._find(inp)
            if name == _STATUS_TOOL:
                return self._status_overview()
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Engineer lookup error: {e}"

    # -- find_engineer ----------------------------------------------------------------
    def _find(self, inp: dict) -> str:
        engineer = str(inp.get("engineer") or "").strip()
        module = str(inp.get("module") or "").strip()
        if engineer:
            return self._find_by_name(engineer)
        if module:
            return self._find_by_module(module)
        return ("Tell me an engineer's name ('where's Farseer') or a module ('who engineers "
                "shields'), or ask what you've unlocked so far.")

    def _find_by_name(self, query: str) -> str:
        eng = find_engineer(query)
        if eng is None:
            names = ", ".join(e.name for e in ENGINEERS[:6])
            return (f"I don't recognise the engineer '{query}'. Some I know: {names} — and "
                    "others. Try another name or ask by module.")
        progress = self._progress()
        status = status_for(eng, progress)
        parts = [self._location_line(eng)]
        parts.append(f"They engineer {self._specialties(eng)}.")
        parts.append(self._status_sentence(eng, status, progress))
        if eng.permit:
            parts.append(eng.permit)
        parts.append(self._deliver(eng))
        return " ".join(p for p in parts if p)

    def _find_by_module(self, query: str) -> str:
        hits = find_by_specialty(query)
        if not hits:
            return (f"I don't have '{query}' as an engineerable module. Try things like FSD, "
                    "thrusters, shields, power plant, multi-cannon, or lasers.")
        progress = self._progress()
        lines = []
        for eng in hits:
            tag = self._short_status(eng, status_for(eng, progress), progress)
            lines.append(f"{eng.name} ({eng.system}) — {tag}")
        head = f"Engineers who upgrade {query}: "
        return head + "; ".join(lines) + "."

    # -- engineer_unlock_status -------------------------------------------------------
    def _status_overview(self) -> str:
        progress = self._progress()
        if not progress:
            return _NO_PROGRESS
        unlocked, in_progress, locked = [], [], []
        for eng in ENGINEERS:
            st = status_for(eng, progress)
            # "Barred" is a real journal state, not "not yet started": keep it out of the locked
            # bucket so the overview agrees with _status_sentence/_short_status (which report it
            # distinctly). It falls through to in_progress, tagged "(barred)".
            if st is None or st.progress not in ("Known", "Invited", "Unlocked", "Barred"):
                locked.append(eng)
            elif st.unlocked:
                grade = f" (grade {st.rank})" if st.rank else ""
                unlocked.append(f"{eng.name}{grade}")
            else:
                in_progress.append(f"{eng.name} ({st.progress.lower()})")
        parts = [f"You've unlocked {len(unlocked)} of {len(ENGINEERS)} engineers."]
        if unlocked:
            parts.append("Unlocked: " + ", ".join(unlocked) + ".")
        if in_progress:
            parts.append("In progress: " + ", ".join(in_progress) + ".")
        if locked:
            sample = ", ".join(e.name for e in locked[:5])
            more = f", and {len(locked) - 5} more" if len(locked) > 5 else ""
            parts.append(f"Not yet started: {sample}{more}. Ask about any one for what it needs.")
        return " ".join(parts)

    # -- status prose -----------------------------------------------------------------
    def _status_sentence(self, eng: Engineer, status: Optional[EngineerStatus],
                         progress: dict) -> str:
        """The grounded 'where you stand + what's left' sentence for one engineer."""
        if not progress:
            return (f"I haven't read your progress with them yet. To unlock: {eng.access} "
                    f"Then {self._lower_first(eng.unlock)}")
        if status is None or status.progress not in ("Known", "Invited", "Unlocked", "Barred"):
            return (f"You haven't started with them. To get the invitation: {eng.access} "
                    f"Then {self._lower_first(eng.unlock)}")
        if status.progress == "Unlocked":
            grade = f" up to grade {status.rank}" if status.rank else ""
            return f"You've UNLOCKED them{grade} — visit to engineer your modules."
        if status.progress == "Invited":
            return (f"You've been INVITED — you can visit them. To unlock their workshop: "
                    f"{eng.unlock}")
        if status.progress == "Barred":
            return "You're currently barred from this engineer."
        # Known
        return (f"You've DISCOVERED them but not yet earned the invitation. Next: {eng.access} "
                f"Then {self._lower_first(eng.unlock)}")

    def _short_status(self, eng: Engineer, status: Optional[EngineerStatus],
                      progress: dict) -> str:
        """A terse per-engineer tag for the by-module list."""
        if not progress:
            return f"at {eng.station}"
        if status is None or status.progress not in ("Known", "Invited", "Unlocked", "Barred"):
            return "not yet unlocked"
        if status.progress == "Unlocked":
            grade = f", grade {status.rank}" if status.rank else ""
            return f"UNLOCKED{grade}"
        return status.progress.lower()

    # -- helpers ----------------------------------------------------------------------
    def _location_line(self, eng: Engineer) -> str:
        region = " (in the Colonia region)" if eng.region == "colonia" else ""
        return f"{eng.name} is at {eng.station} in the {eng.system} system{region}."

    def _specialties(self, eng: Engineer, n: int = 5) -> str:
        specs = list(eng.specialties)
        head = ", ".join(specs[:n])
        return head + (f", and more" if len(specs) > n else "")

    def _deliver(self, eng: Engineer) -> str:
        """Copy the engineer's system for plotting, unless the Commander is already there."""
        if self._clipboard is None:
            return ""
        current = self._current()
        if current and current.strip().lower() == eng.system.strip().lower():
            return "You're already in that system."
        if self._copy(eng.system):
            return f"I've copied {eng.system} to your clipboard to plot a route."
        return ""

    def _current(self) -> Optional[str]:
        """The Commander's current system, guarded like `_progress` — a raising getter must not
        turn a good answer (location, status, what's left) into a generic error."""
        if self._current_system is None:
            return None
        try:
            return self._current_system()
        except Exception as e:  # noqa: BLE001 — a bad getter must not break the answer
            self._logline(f"current-system read failed: {e}")
            return None

    def _copy(self, text: str) -> bool:
        try:
            self._clipboard(text)
            return True
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return False

    def _progress(self) -> dict:
        try:
            prog = self._get_progress()
            return dict(prog) if isinstance(prog, dict) else {}
        except Exception as e:  # noqa: BLE001 — a bad getter must not break the answer
            self._logline(f"progress read failed: {e}")
            return {}

    @staticmethod
    def _lower_first(text: str) -> str:
        return text[:1].lower() + text[1:] if text else text

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
