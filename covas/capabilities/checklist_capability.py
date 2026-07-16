"""Checklist capability — exposes the 'ultimate checklist' to the LLM as tools.

The tool schemas (CHECKLIST_TOOLS) and their handler (run_tool) used to live in
llm.py / app.py respectively; they moved here so the checklist is a self-contained
capability behind the registry (DESIGN §3.3). Behavior is unchanged.
"""
from __future__ import annotations

from typing import Callable

from ..checklist import Checklist, checklist_event
from .base import HelpMeta

# Client-side tools that let Claude read/update the Commander's checklist.
# The list can be large, so these are scoped: fetch just the next items, search,
# or set one item — never dump the whole list.
CHECKLIST_TOOLS = [
    {
        "name": "get_next_objectives",
        "description": (
            "Return the Commander's next pending (unfinished) Elite Dangerous "
            "checklist objectives, in order, with overall progress. Use this for "
            "'what's next' / 'what should I do'. Returns each as '#<number>: <text>'. "
            "This tool (and other checklist tool results) is the ONLY source of truth for "
            "what's next or pending — NEVER state, guess, or paraphrase a next/pending "
            "objective that didn't come from a checklist tool result. If you don't have a "
            "fresh one, call this tool; do not invent an objective."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "How many upcoming pending objectives to return (default 1, max 10).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "find_objectives",
        "description": (
            "Search the checklist for objectives matching a word or phrase. Returns "
            "matches as '#<number> [done|pending] <text>'. Use to locate an item "
            "before marking it, or to disambiguate an unclear request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Word or phrase to search for."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_objective",
        "description": (
            "Mark a checklist objective completed or reopened. Identify it by 'number' "
            "(from get_next_objectives / find_objectives), by 'query' text, or omit "
            "both to use the current line. If a query matches multiple objectives the "
            "tool changes nothing and returns the candidates so you can disambiguate. "
            "When you complete an item, the result also names the REAL next pending "
            "objective (or says all are done) — relay THAT and only that. NEVER state, "
            "guess, or paraphrase a 'next' objective yourself; the tool result is the only "
            "truth. If it says all objectives are complete, do not invent a next one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "number": {"type": "integer", "description": "The objective's number, if known."},
                "query": {"type": "string", "description": "Text identifying the objective (used if number is omitted)."},
                "completed": {"type": "boolean", "description": "true to check off, false to reopen."},
            },
            "required": ["completed"],
        },
    },
    {
        "name": "add_objective",
        "description": (
            "Add a new (pending) objective to the checklist, positioned relative to an "
            "anchor line. By default it is inserted right after the current line. It "
            "inherits the anchor's indentation so it nests correctly. The new line "
            "becomes the current line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The objective text (without the '- [ ]')."},
                "position": {"type": "string", "enum": ["after", "before"],
                             "description": "Place the new line after or before the anchor. Default 'after'."},
                "anchor_number": {"type": "integer",
                                  "description": "Anchor objective number. Omit to use the current line."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "modify_objective",
        "description": (
            "Replace the text of an existing objective (its checkbox state is kept). "
            "Pass the full new text. Target it by 'number', or omit to modify the "
            "current line. Use for edits like changing a destination or quantity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_text": {"type": "string", "description": "The complete replacement text."},
                "number": {"type": "integer", "description": "Objective number. Omit to modify the current line."},
            },
            "required": ["new_text"],
        },
    },
    {
        "name": "delete_objective",
        "description": (
            "Delete an objective from the checklist. Target it by 'number', or omit to "
            "delete the current line. This is destructive — only do it when the "
            "Commander clearly asks to remove/delete a line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "number": {"type": "integer", "description": "Objective number. Omit to delete the current line."},
            },
            "required": [],
        },
    },
]


class ChecklistCapability:
    """Wraps a Checklist model and serves the checklist tools to the LLM."""

    def __init__(self, checklist: Checklist,
                 on_change: Callable[[dict], None] | None = None) -> None:
        self.checklist = checklist
        # Called with a `checklist` bus event after EVERY successful mutation so a live
        # Checklist page reflects voice/tool CRUD without a manual reload (#82). The app
        # wires this to bus.publish; tests that don't care leave it None.
        self._on_change = on_change

    def _notify(self) -> None:
        """Publish a checklist snapshot after a mutation. Fail-soft — a UI-sync hiccup must
        never turn a successful checklist edit into a tool error."""
        if self._on_change is None:
            return
        try:
            self._on_change(checklist_event(self.checklist))
        except Exception:  # noqa: BLE001 — sync is best-effort; the edit already succeeded
            pass

    def tools(self) -> list[dict]:
        return CHECKLIST_TOOLS

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="checklist",
            group="your checklist",
            one_liner=("I track your objective checklist — I can read what's next, mark items "
                       "done or reopened, and add, change, or delete lines."),
            example="what should I knock out next",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        """Execute a client-side checklist tool Claude called."""
        cl = self.checklist
        try:
            if name == "get_next_objectives":
                count = max(1, min(10, int(inp.get("count") or 1)))
                pend, done, total = cl.next_pending(count)
                if not pend:
                    return (f"All {total} objectives are complete."
                            if total else "The checklist is empty.")
                cl.current = pend[0][0]  # first pending becomes the current line
                body = "\n".join(f"#{n}: {t}" for n, t in pend)
                return f"Progress: {done} of {total} complete. Next pending:\n{body}"

            if name == "find_objectives":
                matches = cl.find(inp.get("query", ""))
                if not matches:
                    return f"No objectives match '{inp.get('query', '')}'."
                if len(matches) == 1:
                    cl.current = matches[0][0]  # unique match becomes current line
                return "Matches:\n" + "\n".join(
                    f"#{n} [{'done' if d else 'pending'}] {t}" for n, d, t in matches)

            if name == "set_objective":
                completed = bool(inp.get("completed", True))
                verb = "completed" if completed else "reopened"
                number, query = inp.get("number"), inp.get("query")
                if number is None and query:
                    matches = cl.find(query)
                    if len(matches) > 1:
                        listing = "\n".join(f"#{n} {t}" for n, _d, t in matches)
                        return (f"Several objectives match '{query}'. Ask which one, or "
                                f"call set_objective with a specific number:\n{listing}")
                    if not matches:
                        return f"No objective matches '{query}'."
                    number = matches[0][0]
                text = cl.set_number(int(number), completed) if number else \
                    (cl.set_number(cl.current, completed) if cl.current else None)
                n = number or cl.current
                if not text:
                    return "I don't know which objective you mean."
                self._notify()  # completion toggle landed — reflect it live
                result = f"Done. #{n} '{text}' is now {verb}."
                # On a COMPLETION, hand the model the REAL next objective so it never has to
                # (and must never) invent one. Skip on reopen — 'next' doesn't apply there.
                if completed:
                    pend, _done, total = cl.next_pending(1)
                    if pend:
                        nn, nt = pend[0]
                        result += f" Next pending is #{nn}: '{nt}'."
                    else:
                        result += (f" That was the last one — all {total} "
                                   f"objectives are complete.")
                return result

            if name == "add_objective":
                new_num, text = cl.add(inp.get("text", ""),
                                       inp.get("position", "after"),
                                       inp.get("anchor_number"))
                self._notify()  # new line added — reflect it live
                return f"Added #{new_num}: '{text}' (now the current line)."

            if name == "modify_objective":
                res = cl.modify(inp.get("new_text", ""), inp.get("number"))
                if res is None:
                    return "I don't know which line to modify — find it first."
                n, text = res
                self._notify()  # line text changed — reflect it live
                return f"Updated #{n} to: '{text}'."

            if name == "delete_objective":
                text = cl.delete(inp.get("number"))
                if text is None:
                    return "I don't know which line to delete — find it first."
                self._notify()  # line removed — reflect it live
                return f"Deleted: '{text}'."

            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001
            return f"Tool error: {e}"
