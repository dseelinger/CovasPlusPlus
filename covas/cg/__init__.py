"""Community Goals (N6) — voice CG queries.

Journal-primary: the ED journal `CommunityGoal` event is authoritative for the Commander's
own standing and the CGs they've engaged with (written when they visit a CG board). An
optional external feed (Inara) supplies the COMPLETE active list so CGs the Commander hasn't
visited can still surface. All I/O is injected so the default test run is offline (DESIGN §9).

Note (build-time verification, per the prompt): EDSM has no public community-goals API
endpoint anymore (every candidate path 404s), so the external source is Inara's
`getCommunityGoalsRecent`, which needs a free generic Inara API key. Without a key, CG
commands run journal-only — your standing and visited CGs, but not ones you haven't seen.
"""
from .models import CommunityGoal, match_goal, standing_phrase, summarize
from .journal import cg_from_journals, parse_cg_event
from .feed import CGConfig, CGFeedError, fetch_inara_goals

__all__ = [
    "CommunityGoal",
    "CGConfig",
    "CGFeedError",
    "cg_from_journals",
    "fetch_inara_goals",
    "match_goal",
    "parse_cg_event",
    "standing_phrase",
    "summarize",
]
