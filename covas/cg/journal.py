"""Community Goals from the journal.

The ED `CommunityGoal` event carries a `CurrentGoals` array — every CG the Commander has
engaged with, each with the personal standing fields (PlayerPercentileBand, PlayerInTopRank,
TopRankSize, PlayerContribution). It's written when they visit a CG board, so it reflects
state "as of the last board visit". `cg_from_journals` reconstructs the latest such event by
scanning recent journals (the same fail-soft, one-shot approach as the carrier fallback).
"""
from __future__ import annotations

from pathlib import Path

from ..ed.journal import _JOURNAL_GLOB, parse_journal_line
from .models import CommunityGoal

_MAX_FILES = 12   # recent journals to scan for the latest CommunityGoal event


def _safe_mtime(p: Path) -> float:
    """A file's mtime, or 0.0 if it vanished between the glob and the stat (fail-soft — a deleted
    file sorts oldest and is skipped by the read guard below, issue #164)."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def parse_cg_event(event: dict) -> list[CommunityGoal]:
    """Parse a `CommunityGoal` journal event's `CurrentGoals` into CommunityGoal records
    (engaged=True — these are the CGs the Commander has visited). Non-CG events -> []."""
    if not isinstance(event, dict) or event.get("event") != "CommunityGoal":
        return []
    goals: list[CommunityGoal] = []
    for g in (event.get("CurrentGoals") or []):
        if not isinstance(g, dict) or not g.get("Title"):
            continue
        top_tier = g.get("TopTier")
        top_tier_name = top_tier.get("Name") if isinstance(top_tier, dict) else None
        goals.append(CommunityGoal(
            title=str(g["Title"]),
            system=str(g.get("SystemName") or ""),
            station=g.get("MarketName"),
            expiry=g.get("Expiry"),
            cgid=g.get("CGID"),
            is_complete=bool(g.get("IsComplete")),
            tier_reached=g.get("TierReached"),
            top_tier=top_tier_name,
            current_total=g.get("CurrentTotal"),
            player_contribution=g.get("PlayerContribution"),
            player_percentile_band=g.get("PlayerPercentileBand"),
            player_in_top_rank=g.get("PlayerInTopRank"),
            top_rank_size=g.get("TopRankSize"),
            engaged=True,
        ))
    return goals


def cg_from_journals(journal_dir: str | Path) -> list[CommunityGoal]:
    """The most recent `CommunityGoal` event's goals from the recent journals, or [] if none
    (the Commander hasn't visited a CG board recently). Best-effort and fail-soft."""
    try:
        files = list(Path(journal_dir).glob(_JOURNAL_GLOB))
    except OSError:
        return []
    files.sort(key=lambda p: (_safe_mtime(p), p.name))   # oldest -> newest (vanished file -> 0.0)
    for path in reversed(files[-_MAX_FILES:]):              # newest file first
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):                       # newest event first
            ev = parse_journal_line(line)
            if ev and ev.get("event") == "CommunityGoal":
                return parse_cg_event(ev)
    return []
