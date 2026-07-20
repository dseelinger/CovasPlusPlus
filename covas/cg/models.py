"""Community Goal model + spoken-phrasing helpers (pure, offline-testable).

One `CommunityGoal` type spans both sources: the journal (which carries the Commander's
personal standing) and the external feed (which carries the complete active list). `engaged`
marks the ones the journal knows about — i.e. the Commander has visited that board — which is
also the only case standing is available.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True)
class CommunityGoal:
    title: str
    system: str
    station: str | None = None
    expiry: str | None = None            # ISO timestamp string
    cgid: int | None = None
    is_complete: bool = False
    tier_reached: object | None = None   # int (journal) or str
    top_tier: str | None = None
    current_total: int | None = None
    # --- personal standing (journal only) ---
    player_contribution: int | None = None
    player_percentile_band: int | None = None
    player_in_top_rank: bool | None = None
    top_rank_size: int | None = None
    engaged: bool = False                # sourced from the journal (Commander visited the board)

    def has_standing(self) -> bool:
        """Whether the journal gives us a personal standing to report."""
        return self.engaged and (
            bool(self.player_in_top_rank)
            or self.player_percentile_band is not None
            or self.player_contribution is not None
        )


def standing_phrase(goal: CommunityGoal) -> str | None:
    """A spoken standing fragment ('you're in the top 10 Commanders' / 'you're in the top
    25%'), or None when the journal has no standing for this CG."""
    if not goal.engaged:
        return None
    if goal.player_in_top_rank:
        size = goal.top_rank_size or 10
        return f"you're in the top {size} Commanders"
    if goal.player_percentile_band is not None:
        return f"you're in the top {goal.player_percentile_band}%"
    if goal.player_contribution:
        from ..i18n import fmt_int
        return f"you've contributed {fmt_int(goal.player_contribution)}"   # locale grouping (#199)
    return None


def _short_date_safe(iso: str | None) -> str | None:
    """'2026-07-15T07:00:00Z' -> 'Jul 15'. None/unparseable -> None. Deterministic (no 'now').
    %-d isn't portable (fails on Windows), so the day is formatted manually."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    from ..i18n import fmt_date
    return fmt_date(dt)   # locale short date, e.g. "Jul 15" / "15. Juli" (#199)


def goal_line(goal: CommunityGoal) -> str:
    """One spoken clause for a CG in a list: title, system, and (if known) when it ends."""
    line = f"{goal.title} in {goal.system}"
    date = _short_date_safe(goal.expiry)
    if date:
        line += f" (ends {date})"
    return line


def summarize(active: list[CommunityGoal], *, max_spoken: int = 4) -> str:
    """A spoken summary of the active CGs, flagging the ones NEW to the Commander (active but
    not in their journal — the whole point of the external feed). Assumes `active` is already
    merged (journal engagement folded in)."""
    live = [g for g in active if not g.is_complete]
    if not live:
        return "There are no active community goals right now."
    shown = live[:max_spoken]
    parts = "; ".join(goal_line(g) for g in shown)
    tail = f" (and {len(live) - len(shown)} more)" if len(live) > len(shown) else ""
    line = f"{len(live)} active community goal{'s' if len(live) != 1 else ''}: {parts}{tail}."
    new = [g for g in live if not g.engaged]
    if new:
        if len(new) == 1:
            line += f" You haven't visited the one in {new[0].system} yet."
        else:
            line += f" {len(new)} of them you haven't visited yet."
    return line


# --- fuzzy title matching --------------------------------------------------
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NON_ALNUM.sub(" ", str(text or "").lower()).strip()


def match_goal(goals: list[CommunityGoal], spoken: str,
               *, cutoff: float = 0.5) -> tuple[CommunityGoal | None, list[CommunityGoal]]:
    """Resolve a spoken CG title to a goal. Returns ``(goal, [])`` on a confident match,
    ``(None, candidates)`` when several are close (ambiguous), or ``(None, [])`` when nothing
    matches. Exact / substring wins before fuzzy, so 'the thargoid one' can hit a title that
    contains it without snagging an unrelated CG."""
    q = _norm(spoken)
    if not q or not goals:
        return None, []

    # de-dupe by (title, system) so a journal+external pair for the same CG counts once
    seen: dict[str, CommunityGoal] = {}
    for g in goals:
        seen.setdefault(f"{_norm(g.title)}|{_norm(g.system)}", g)
    uniq = list(seen.values())

    exact = [g for g in uniq if _norm(g.title) == q]
    if len(exact) == 1:
        return exact[0], []
    contains = [g for g in uniq if q in _norm(g.title) or _norm(g.title) in q]
    if len(contains) == 1:
        return contains[0], []
    if len(contains) > 1:
        return None, contains
    titles = {_norm(g.title): g for g in uniq}
    near = difflib.get_close_matches(q, list(titles), n=3, cutoff=cutoff)
    if len(near) == 1:
        return titles[near[0]], []
    if len(near) > 1:
        return None, [titles[n] for n in near]
    return None, []


def merge(external: list[CommunityGoal],
          journal: list[CommunityGoal]) -> list[CommunityGoal]:
    """Fold journal engagement (and standing) into the external COMPLETE list: an external CG
    that the journal also knows becomes `engaged` with the journal's standing fields; a
    journal CG missing from the feed is appended (best-effort). Match by CGID, else by title."""
    by_id = {g.cgid: g for g in journal if g.cgid is not None}
    by_title = {_norm(g.title): g for g in journal}
    out: list[CommunityGoal] = []
    used: set[int] = set()
    for ext in external:
        jm = by_id.get(ext.cgid) if ext.cgid is not None else None
        if jm is None:
            jm = by_title.get(_norm(ext.title))
        if jm is not None:
            if jm.cgid is not None:
                used.add(jm.cgid)
            out.append(replace(
                ext,
                engaged=True,
                is_complete=ext.is_complete or jm.is_complete,
                player_contribution=jm.player_contribution,
                player_percentile_band=jm.player_percentile_band,
                player_in_top_rank=jm.player_in_top_rank,
                top_rank_size=jm.top_rank_size,
                tier_reached=jm.tier_reached if jm.tier_reached is not None else ext.tier_reached,
            ))
        else:
            out.append(ext)
    # journal CGs the feed didn't include (stale feed / edge) — keep them so nothing is lost
    for jm in journal:
        if jm.cgid is not None and jm.cgid in used:
            continue
        if _norm(jm.title) in {_norm(g.title) for g in out}:
            continue
        out.append(jm)
    return out
