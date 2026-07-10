"""Community Goals capability (N6) — LLM-native voice CG queries.

Three tools:
  * `list_community_goals`      — the active CGs (title + system + expiry), MERGING the
                                  external feed (complete list) with the journal (which ones
                                  you're contributing to), and calling out the ones NEW to you.
  * `community_goal_system`     — resolve a CG by title (fuzzy) -> its system, copy it to the
                                  clipboard (N3 rule: if it's your current system, say so and
                                  DON'T copy).
  * `community_goal_standing`   — your standing in a CG (Top N Commanders / top band %), from
                                  the journal only, flagged "as of your last board visit".

Journal-primary; the external feed is the completeness source and is optional (needs an Inara
key). Fail-soft: no feed -> journal-only with a clear note. All I/O injected (DESIGN §9).
"""
from __future__ import annotations

from typing import Callable, Optional

from ..cg.feed import CGFeedError
from ..cg.models import CommunityGoal, match_goal, merge, standing_phrase, summarize
from .base import HelpMeta

_LIST_TOOL = "list_community_goals"
_SYSTEM_TOOL = "community_goal_system"
_STANDING_TOOL = "community_goal_standing"

_LIST_DESC = (
    "List the currently ACTIVE Elite Dangerous Community Goals — each goal's title, system, "
    "and when it ends — and point out any the Commander hasn't visited yet. Use for 'what "
    "community goals are active' / 'any CGs running' / 'what's happening in the galaxy'. "
    "Relay the tool's reply."
)
_SYSTEM_DESC = (
    "Say which STAR SYSTEM a Community Goal is in, and copy that system to the clipboard (to "
    "paste into the galaxy map). `goal` is the Commander's (possibly fuzzy) name for the CG. "
    "Use for 'what system is the <X> community goal in' / 'where's the <X> CG'. Relay the "
    "reply, including that the system was copied."
)
_STANDING_DESC = (
    "Report the Commander's STANDING in a Community Goal — top-rank or percentile band — from "
    "their journal (as of their last visit to that CG's board). `goal` is their name for the "
    "CG. Use for 'what's my standing in the <X> CG' / 'how am I doing in <X>'. Relay the reply."
)

_GOAL_PROP = {"goal": {"type": "string",
                       "description": "The Commander's name for the community goal (fuzzy is "
                                      "fine — it's matched to a real CG title)."}}
_NO_ARGS = {"type": "object", "properties": {}, "required": []}
_GOAL_ARGS = {"type": "object", "properties": dict(_GOAL_PROP), "required": ["goal"]}


class CGCapability:
    """Advertises the three CG tools; merges journal + external feed and answers from them."""

    def __init__(
        self,
        *,
        get_journal_goals: Callable[[], list[CommunityGoal]],
        get_current_system: Callable[[], Optional[str]],
        clipboard: Callable[[str], None],
        fetch_external: Optional[Callable[[], list[CommunityGoal]]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._get_journal = get_journal_goals
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._fetch_external = fetch_external      # None -> journal-only (no key/source)
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [
            {"name": _LIST_TOOL, "description": _LIST_DESC, "input_schema": dict(_NO_ARGS)},
            {"name": _SYSTEM_TOOL, "description": _SYSTEM_DESC, "input_schema": dict(_GOAL_ARGS)},
            {"name": _STANDING_TOOL, "description": _STANDING_DESC, "input_schema": dict(_GOAL_ARGS)},
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="community goals",
            one_liner=("I list the active community goals, tell you what system a goal is in, "
                       "and how you're standing in one."),
            example="what community goals are active",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _LIST_TOOL:
                return self._list()
            if name == _SYSTEM_TOOL:
                return self._system(inp)
            if name == _STANDING_TOOL:
                return self._standing(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Community-goal lookup error: {e}"

    # -- gather (journal + optional external) -----------------------------------------
    def _gather(self) -> tuple[list[CommunityGoal], Optional[str]]:
        """The active-CG set and a feed-status note: None (feed merged in), 'unconfigured'
        (no external source), or 'failed' (feed configured but unreachable)."""
        journal = list(self._get_journal() or [])
        if self._fetch_external is None:
            return journal, "unconfigured"
        try:
            external = list(self._fetch_external() or [])
        except CGFeedError as e:
            self._logline(f"feed unavailable: {e}")
            return journal, "failed"
        return merge(external, journal), None

    def _feed_note(self, note: Optional[str]) -> str:
        if note == "unconfigured":
            return (" I can only see the goals you've visited — add an Inara API key in the "
                    "community-goal settings to see every active one.")
        if note == "failed":
            return " I couldn't reach the goal feed just now, so that's only the ones you've visited."
        return ""

    # -- list -------------------------------------------------------------------------
    def _list(self) -> str:
        goals, note = self._gather()
        self._logline(f"list: {len(goals)} goals, feed={note or 'ok'}")
        return summarize(goals) + self._feed_note(note)

    # -- system -----------------------------------------------------------------------
    def _system(self, inp: dict) -> str:
        spoken = str(inp.get("goal") or "").strip()
        if not spoken:
            return "Which community goal do you mean?"
        goals, note = self._gather()
        match, ambiguous = match_goal(goals, spoken)
        if ambiguous:
            return f"Did you mean {_or_list([g.title for g in ambiguous])}? Which one?"
        if match is None:
            return (f"I don't see a community goal like '{spoken}'." + self._feed_note(note)).strip()
        if not match.system:
            return f"I found {match.title}, but I don't have its system."
        current = self._current_system()
        if _same_system(match.system, current):
            return (f"{match.title} is in {match.system} — that's your current system, so I "
                    "haven't copied anything.")
        copied = self._copy(match.system)
        self._logline(f"system: {match.title} -> {match.system} (copied={copied})")
        return (f"{match.title} is in {match.system}." +
                (f" I've copied {match.system} to your clipboard." if copied
                 else f" (Couldn't copy to the clipboard — the system is {match.system}.)"))

    # -- standing ---------------------------------------------------------------------
    def _standing(self, inp: dict) -> str:
        spoken = str(inp.get("goal") or "").strip()
        if not spoken:
            return "Which community goal do you want your standing in?"
        journal = list(self._get_journal() or [])   # standing is journal-only
        match, ambiguous = match_goal(journal, spoken)
        if ambiguous:
            return f"Did you mean {_or_list([g.title for g in ambiguous])}? Which one?"
        if match is None:
            return (f"I don't have your standing for '{spoken}' — visit its board in-game and "
                    "I'll have it. (Standing comes from your journal, not the goal feed.)")
        phrase = standing_phrase(match)
        if not phrase:
            return (f"I don't have a standing for {match.title} yet — visit the board and "
                    "contribute, and I'll track it.")
        return f"In {match.title}, {phrase} — as of your last board visit."

    # -- helpers ----------------------------------------------------------------------
    def _copy(self, text: str) -> bool:
        try:
            self._clipboard(text)
            return True
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return False

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _same_system(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b and a.strip().lower() == b.strip().lower())


def _or_list(items: list[str]) -> str:
    items = [str(i) for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"
