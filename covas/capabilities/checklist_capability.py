"""Checklist capability — exposes the 'ultimate checklist' to the LLM as tools.

The tool schemas (CHECKLIST_TOOLS) and their handler (run_tool) used to live in
llm.py / app.py respectively; they moved here so the checklist is a self-contained
capability behind the registry (DESIGN §3.3). Behavior is unchanged.
"""
from __future__ import annotations

from ..checklist import Checklist

# Client-side tools that let Claude read/update the Commander's checklist.
# The list can be large, so these are scoped: fetch just the next items, search,
# or set one item — never dump the whole list.
CHECKLIST_TOOLS = [
    {
        "name": "get_next_objectives",
        "description": (
            "Return the Commander's next pending (unfinished) Elite Dangerous "
            "checklist objectives, in order, with overall progress. Use this for "
            "'what's next' / 'what should I do'. Returns each as '#<number>: <text>'."
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
            "tool changes nothing and returns the candidates so you can disambiguate."
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

    def __init__(self, checklist: Checklist) -> None:
        self.checklist = checklist

    def tools(self) -> list[dict]:
        return CHECKLIST_TOOLS

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
                return (f"Done. #{n} '{text}' is now {verb}."
                        if text else "I don't know which objective you mean.")

            if name == "add_objective":
                new_num, text = cl.add(inp.get("text", ""),
                                       inp.get("position", "after"),
                                       inp.get("anchor_number"))
                return f"Added #{new_num}: '{text}' (now the current line)."

            if name == "modify_objective":
                res = cl.modify(inp.get("new_text", ""), inp.get("number"))
                if res is None:
                    return "I don't know which line to modify — find it first."
                n, text = res
                return f"Updated #{n} to: '{text}'."

            if name == "delete_objective":
                text = cl.delete(inp.get("number"))
                if text is None:
                    return "I don't know which line to delete — find it first."
                return f"Deleted: '{text}'."

            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001
            return f"Tool error: {e}"
