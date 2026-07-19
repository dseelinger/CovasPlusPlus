"""Unit tests for Community Goals (N6) — offline, free (DESIGN §9).

Journal parsing + standing phrasing, fuzzy title matching, the journal↔feed merge, the Inara
feed over a recorded response (fake http), and the capability's three tools (incl. the N3
'already there -> don't copy' rule and the journal-only fallback notes). No network.
"""
from __future__ import annotations

import json

import pytest

from covas.capabilities.base import help_meta_problems
from covas.capabilities.cg_capability import CGCapability
from covas.cg.feed import CGConfig, CGFeedError, fetch_inara_goals
from covas.cg.journal import _safe_mtime, cg_from_journals, parse_cg_event
from covas.cg.models import CommunityGoal, match_goal, merge, standing_phrase, summarize


_JOURNAL_EVENT = {
    "event": "CommunityGoal",
    "CurrentGoals": [
        {"CGID": 726, "Title": "Alliance Research Initiative", "SystemName": "Sol",
         "MarketName": "Galileo", "Expiry": "2026-07-15T07:00:00Z", "IsComplete": False,
         "CurrentTotal": 9_000_000, "TierReached": "Tier 5", "TopTier": {"Name": "Tier 8"},
         "PlayerContribution": 120_000, "PlayerPercentileBand": 25,
         "PlayerInTopRank": False, "TopRankSize": 10},
        {"CGID": 727, "Title": "Thargoid War Effort", "SystemName": "HIP 22460",
         "Expiry": "2026-07-12T07:00:00Z", "IsComplete": False,
         "PlayerInTopRank": True, "TopRankSize": 10, "PlayerContribution": 500_000},
    ],
}


def _journal_goals():
    return parse_cg_event(_JOURNAL_EVENT)


# --- journal parsing -------------------------------------------------------

def test_parse_cg_event_extracts_fields_and_marks_engaged():
    goals = _journal_goals()
    assert [g.title for g in goals] == ["Alliance Research Initiative", "Thargoid War Effort"]
    ari = goals[0]
    assert ari.system == "Sol" and ari.cgid == 726 and ari.engaged is True
    assert ari.player_percentile_band == 25 and ari.top_rank_size == 10


def test_parse_non_cg_event_is_empty():
    assert parse_cg_event({"event": "FSDJump", "StarSystem": "Sol"}) == []


def test_cg_from_journals_reads_latest(tmp_path):
    old = {"event": "CommunityGoal", "CurrentGoals": [
        {"CGID": 1, "Title": "Old CG", "SystemName": "Lave"}]}
    (tmp_path / "Journal.2026-07-01T10.01.log").write_text(json.dumps(old) + "\n", encoding="utf-8")
    (tmp_path / "Journal.2026-07-05T10.01.log").write_text(
        json.dumps({"event": "Fileheader"}) + "\n" + json.dumps(_JOURNAL_EVENT) + "\n",
        encoding="utf-8")
    goals = cg_from_journals(tmp_path)
    assert {g.title for g in goals} == {"Alliance Research Initiative", "Thargoid War Effort"}


def test_cg_from_journals_empty_when_none(tmp_path):
    (tmp_path / "Journal.2026-07-01T10.01.log").write_text(
        json.dumps({"event": "FSDJump", "StarSystem": "Sol"}) + "\n", encoding="utf-8")
    assert cg_from_journals(tmp_path) == []


# --- issue #164: a file vanishing between glob and stat must not escape (fail-soft) --------------

def test_safe_mtime_of_vanished_file_is_zero_not_raise(tmp_path):
    assert _safe_mtime(tmp_path / "gone.log") == 0.0     # no OSError escapes


def test_cg_from_journals_skips_a_vanished_file(tmp_path, monkeypatch):
    # Simulate a file present at glob time but gone by the stat/sort (a backup/cleanup tool). The old
    # `files.sort(key=p.stat)` let that stat() escape cg_from_journals; the guard skips it instead.
    real = tmp_path / "Journal.2026-07-05T10.01.log"
    real.write_text(json.dumps(_JOURNAL_EVENT) + "\n", encoding="utf-8")
    vanished = tmp_path / "Journal.2026-07-06T10.01.log"   # never created -> stat() would raise

    real_glob = type(tmp_path).glob

    def _glob_with_ghost(self, pattern):
        return list(real_glob(self, pattern)) + [vanished]

    monkeypatch.setattr(type(tmp_path), "glob", _glob_with_ghost)
    goals = cg_from_journals(tmp_path)                     # must not raise
    assert {g.title for g in goals} == {"Alliance Research Initiative", "Thargoid War Effort"}


# --- standing phrasing -----------------------------------------------------

def test_standing_top_rank():
    _, twe = _journal_goals()
    assert standing_phrase(twe) == "you're in the top 10 Commanders"


def test_standing_percentile_band():
    ari, _ = _journal_goals()
    assert standing_phrase(ari) == "you're in the top 25%"


def test_standing_none_when_not_engaged():
    assert standing_phrase(CommunityGoal(title="x", system="y", engaged=False)) is None


def test_standing_contribution_only():
    g = CommunityGoal(title="x", system="y", engaged=True, player_contribution=42)
    assert "42" in standing_phrase(g)


# --- fuzzy matching --------------------------------------------------------

def test_match_exact_and_fuzzy():
    goals = _journal_goals()
    assert match_goal(goals, "Alliance Research Initiative")[0].cgid == 726
    assert match_goal(goals, "thargoid")[0].cgid == 727        # substring
    assert match_goal(goals, "aliance reserch initative")[0].cgid == 726  # fuzzy mishear


def test_match_ambiguous_and_miss():
    goals = [CommunityGoal(title="Weapons Drive Alpha", system="A"),
             CommunityGoal(title="Weapons Drive Beta", system="B")]
    m, cands = match_goal(goals, "weapons drive")
    assert m is None and len(cands) == 2
    assert match_goal(goals, "completely unrelated thing xyz") == (None, [])


# --- merge -----------------------------------------------------------------

def test_merge_folds_journal_standing_and_flags_new():
    journal = _journal_goals()
    external = [
        CommunityGoal(title="Alliance Research Initiative", system="Sol", cgid=726),
        CommunityGoal(title="Empire Weapons Drive", system="Facece", cgid=999),
    ]
    merged = merge(external, journal)
    by_title = {g.title: g for g in merged}
    assert by_title["Alliance Research Initiative"].engaged is True          # journal folded in
    assert by_title["Alliance Research Initiative"].player_percentile_band == 25
    assert by_title["Empire Weapons Drive"].engaged is False                 # new to the player
    # a journal CG missing from the feed is still kept
    assert "Thargoid War Effort" in by_title


def test_summarize_flags_unvisited():
    journal = _journal_goals()
    external = [CommunityGoal(title="Empire Weapons Drive", system="Facece", cgid=999)]
    out = summarize(merge(external, journal))
    assert "active community goal" in out and "Facece" in out
    assert "haven't visited" in out


def test_summarize_empty():
    assert "no active community goals" in summarize([]).lower()


# --- Inara feed (recorded fixture, fake http) ------------------------------

class FakeHttp:
    def __init__(self, status=200, body=None):
        self.status, self.body = status, body
        self.calls = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "payload": payload})
        return self.status, self.body


_INARA_OK = {"header": {"eventStatus": 200}, "events": [{"eventStatus": 200, "eventData": [
    {"communitygoalGameID": 726, "communitygoalName": "Alliance Research Initiative",
     "starsystemName": "Sol", "stationName": "Galileo", "goalExpiry": "2026-07-15T07:00:00Z",
     "tierReached": 5, "tierMax": 8, "isCompleted": False},
    {"communitygoalGameID": 999, "communitygoalName": "Empire Weapons Drive",
     "starsystemName": "Facece", "goalExpiry": "2026-07-20T07:00:00Z", "isCompleted": False},
]}]}


def test_fetch_inara_parses_goals_and_sends_event():
    http = FakeHttp(body=_INARA_OK)
    goals = fetch_inara_goals(http, api_key="KEY", timestamp="2026-07-10T00:00:00")
    assert [g.title for g in goals] == ["Alliance Research Initiative", "Empire Weapons Drive"]
    assert all(g.engaged is False for g in goals)     # feed carries no personal standing
    ev = http.calls[0]["payload"]["events"][0]
    assert ev["eventName"] == "getCommunityGoalsRecent"
    assert http.calls[0]["payload"]["header"]["APIkey"] == "KEY"


def test_fetch_inara_no_key_raises():
    with pytest.raises(CGFeedError):
        fetch_inara_goals(FakeHttp(body=_INARA_OK), api_key="", timestamp="t")


def test_fetch_inara_http_error_raises():
    with pytest.raises(CGFeedError):
        fetch_inara_goals(FakeHttp(status=500, body=None), api_key="K", timestamp="t")


def test_fetch_inara_api_status_error_raises():
    bad = {"header": {"eventStatus": 400}, "events": [{"eventStatus": 400}]}
    with pytest.raises(CGFeedError):
        fetch_inara_goals(FakeHttp(body=bad), api_key="K", timestamp="t")


def test_cg_config_external_enabled():
    assert CGConfig.from_cfg({"cg": {"source": "inara", "inara_api_key": "K"}}).external_enabled
    assert not CGConfig.from_cfg({"cg": {"source": "inara"}}).external_enabled     # no key
    assert not CGConfig.from_cfg({"cg": {"source": "none", "inara_api_key": "K"}}).external_enabled


# --- capability ------------------------------------------------------------

class Clip:
    def __init__(self):
        self.copied = []

    def __call__(self, text):
        self.copied.append(text)


def _cap(*, system="Deciat", fetch_external="ok", clip=None):
    clip = clip or Clip()
    external = [CommunityGoal(title="Empire Weapons Drive", system="Facece", cgid=999,
                             expiry="2026-07-20T07:00:00Z")]
    if fetch_external == "ok":
        def fx():
            return external
    elif fetch_external == "fail":
        def fx():
            raise CGFeedError("Inara down")
    else:
        fx = None
    cap = CGCapability(get_journal_goals=_journal_goals, get_current_system=lambda: system,
                       clipboard=clip, fetch_external=fx)
    return cap, clip


def test_list_merges_and_flags_new():
    cap, _ = _cap()
    out = cap.run_tool("list_community_goals", {})
    assert "Empire Weapons Drive" in out and "haven't visited" in out


def test_list_journal_only_note_when_unconfigured():
    cap, _ = _cap(fetch_external=None)
    out = cap.run_tool("list_community_goals", {})
    assert "Inara API key" in out and "Empire Weapons Drive" not in out   # can't see unvisited


def test_list_feed_failure_note():
    cap, _ = _cap(fetch_external="fail")
    out = cap.run_tool("list_community_goals", {})
    assert "couldn't reach" in out.lower()


def test_system_copies_unvisited_cg():
    cap, clip = _cap(system="Deciat")
    out = cap.run_tool("community_goal_system", {"goal": "empire weapons"})
    assert "Facece" in out and clip.copied == ["Facece"]


def test_system_skips_copy_when_current():
    cap, clip = _cap(system="Sol")           # ARI is in Sol
    out = cap.run_tool("community_goal_system", {"goal": "alliance research"})
    assert clip.copied == [] and "current system" in out.lower()


def test_system_unknown_goal():
    cap, clip = _cap()
    out = cap.run_tool("community_goal_system", {"goal": "nonexistent goal zzz"})
    assert clip.copied == [] and "don't see" in out.lower()


def test_standing_from_journal():
    cap, _ = _cap()
    out = cap.run_tool("community_goal_standing", {"goal": "thargoid"})
    assert "top 10 Commanders" in out and "last board visit" in out.lower()


def test_standing_for_unvisited_cg_says_visit_board():
    cap, _ = _cap()
    out = cap.run_tool("community_goal_standing", {"goal": "empire weapons"})   # feed-only
    assert "visit" in out.lower() and "board" in out.lower()


def test_help_meta_complete_and_tools():
    cap, _ = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    assert {t["name"] for t in cap.tools()} == {
        "list_community_goals", "community_goal_system", "community_goal_standing"}
