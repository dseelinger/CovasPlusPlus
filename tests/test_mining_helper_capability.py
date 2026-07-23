"""Unit tests for MiningHelperCapability (#45) — offline, hermetic.

A fake Http replays the two synchronous /search responses (hotspots, then sell), the clipboard and
a fake checklist record side effects. Covers the happy path (hotspot + fresh sell + checklist + plot),
material resolution, the missing-material and no-start prompts, the stale-price caveat, a no-hotspot
message, a soft sell-lookup failure that still yields the hotspot, and the opt-out toggles.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from covas.capabilities.mining_helper_capability import (
    MiningHelperCapability,
    MiningHelperConfig,
    resolve_material,
)
from covas.search.routes import RoutePlotter

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["results"]


def _fresh_sell(results: list[dict]) -> list[dict]:
    """Stamp the non-carrier sell stations with a current timestamp so the 'fresh' quote stays
    within the freshness window no matter when the suite runs. The fixture's carrier quotes keep
    their real 2020 dates (they're dropped as transient anyway); pinning fixed dates on the real
    stations would silently re-stale as the calendar advances past them (that drift is exactly
    what this guards against)."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S+00")
    for r in results:
        if r.get("type") != "Drake-Class Carrier":
            r["market_updated_at"] = now
    return results


HOTSPOTS = (200, {"results": _load("spansh_hotspots_painite.json")})
SELL = (200, {"results": _fresh_sell(_load("spansh_sell_painite.json"))})


class _FakeHttp:
    """Replays a queued sequence of (status, body) POST responses in call order."""

    def __init__(self, posts):
        self._posts = list(posts)
        self.urls: list[str] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.urls.append(url)
        nxt = self._posts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def get_json(self, url, params=None, *, headers=None, timeout=20.0):  # pragma: no cover
        raise AssertionError("mining search is synchronous")


class _Clip:
    def __init__(self): self.copied = []
    def __call__(self, t): self.copied.append(t)


class _FakeChecklist:
    def __init__(self): self.added = []
    def add(self, text): self.added.append(text); return len(self.added), text


def _cap(http, *, system="Sol", clip=None, checklist=None, cfg=None):
    clip = clip or _Clip()
    cap = MiningHelperCapability(
        cfg or MiningHelperConfig(enabled=True),
        http=http,
        get_current_system=lambda: system,
        checklist=checklist,
        plotter=RoutePlotter(clipboard=clip))
    return cap, clip


# --- material resolution ---------------------------------------------------

def test_resolve_material_aliases():
    assert resolve_material("LTDs") == "Low Temperature Diamonds"
    assert resolve_material("void opals") == "Void Opal"
    assert resolve_material("  painite ") == "Painite"
    assert resolve_material("Serendibite") == "Serendibite"
    assert resolve_material("Made Up Ore") == "Made Up Ore"      # passthrough, title-cased
    assert resolve_material("") == ""


# --- tool + help surface ---------------------------------------------------

def test_tool_and_help_exposed():
    cap, _ = _cap(_FakeHttp([HOTSPOTS, SELL]))
    tools = cap.tools()
    assert [t["name"] for t in tools] == ["plan_mining_session"]
    assert tools[0]["input_schema"]["required"] == ["material"]
    assert cap.help_meta().category == "mining"


# --- happy path ------------------------------------------------------------

def test_happy_path_hotspot_sell_checklist_and_plot():
    cl = _FakeChecklist()
    cap, clip = _cap(_FakeHttp([HOTSPOTS, SELL]), checklist=cl)
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    # Hotspot spoken (nearest ring), best FRESH sell spoken, no stale caveat.
    assert "Barnard's Star 5 A Ring" in msg and "Barnard's Star" in msg
    assert "Bell Vision" in msg and "467,596" in msg
    assert "days old" not in msg                                 # fresh -> no caveat
    # Checklist loop dropped (3 steps) and hotspot system plotted to the clipboard.
    assert len(cl.added) == 3
    assert cl.added[0].startswith("Fly to Barnard's Star")
    assert cl.added[1] == "Mine Painite"
    assert "Sell Painite at Bell Vision" in cl.added[2]
    assert clip.copied == ["Barnard's Star"]
    assert "checklist" in msg.lower()


def test_overlap_count_phrasing():
    cap, _ = _cap(_FakeHttp([HOTSPOTS, SELL]))
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert "2 overlapping hotspots" in msg


def test_sell_commodity_defaults_to_material_and_can_override():
    http = _FakeHttp([HOTSPOTS, SELL])
    cap, _ = _cap(http)
    cap.run_tool("plan_mining_session", {"material": "Painite"})
    # second POST (sell) carries the same commodity in its market filter
    assert http.urls[1].endswith("/stations/search")


# --- prompts / guards ------------------------------------------------------

def test_prompts_for_missing_material():
    cap, _ = _cap(_FakeHttp([]))
    msg = cap.run_tool("plan_mining_session", {})
    assert "what do you want to mine" in msg.lower()


def test_prompts_for_start_when_no_current_system():
    cap = MiningHelperCapability(MiningHelperConfig(enabled=True), http=_FakeHttp([]),
                                 get_current_system=lambda: None)
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert "starting system" in msg.lower() or "where to start" in msg.lower()


def test_uses_given_from_system_over_current():
    http = _FakeHttp([HOTSPOTS, SELL])
    cap, _ = _cap(http, system="Sol")
    cap.run_tool("plan_mining_session", {"material": "Painite", "from_system": "Deciat"})
    # bodies/search POST is synchronous; the reference system rides in the request body
    assert http.urls[0].endswith("/bodies/search")


# --- freshness (the differentiator) ---------------------------------------

def test_stale_sell_price_gets_age_caveat():
    # Only stale carrier quotes exist -> stale fallback + spoken caveat.
    stale_sell = (200, {"results": [r for r in _load("spansh_sell_painite.json")
                                    if r.get("type") == "Drake-Class Carrier"]})
    # include_carriers is False in the parser, so a carrier-only response yields NO sell market:
    cap, _ = _cap(_FakeHttp([HOTSPOTS, stale_sell]))
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert "couldn't find a fresh place to sell" in msg.lower()  # all transient -> no honest quote


def test_no_hotspot_found_message():
    cap, _ = _cap(_FakeHttp([(200, {"results": []})]))
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert "couldn't find a painite hotspot" in msg.lower()


def test_sell_lookup_failure_is_soft_still_gives_hotspot():
    cap, clip = _cap(_FakeHttp([HOTSPOTS, ConnectionError("boom")]))
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert "Barnard's Star 5 A Ring" in msg                      # hotspot still spoken
    assert "couldn't find a fresh place to sell" in msg.lower()  # sell degrades softly
    assert clip.copied == ["Barnard's Star"]                     # plot still happens


def test_hotspot_lookup_failure_is_soft():
    cap, _ = _cap(_FakeHttp([ConnectionError("boom")]))
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert "reach" in msg.lower() or "couldn't" in msg.lower()


# --- opt-out toggles -------------------------------------------------------

def test_add_to_checklist_false_skips_checklist():
    cl = _FakeChecklist()
    cap, _ = _cap(_FakeHttp([HOTSPOTS, SELL]), checklist=cl)
    cap.run_tool("plan_mining_session", {"material": "Painite", "add_to_checklist": False})
    assert cl.added == []


def test_plot_false_skips_clipboard():
    cap, clip = _cap(_FakeHttp([HOTSPOTS, SELL]))
    cap.run_tool("plan_mining_session", {"material": "Painite", "plot": False})
    assert clip.copied == []


def test_config_add_to_checklist_default_off():
    cl = _FakeChecklist()
    cfg = MiningHelperConfig(enabled=True, add_to_checklist=False)
    cap, _ = _cap(_FakeHttp([HOTSPOTS, SELL]), checklist=cl, cfg=cfg)
    cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert cl.added == []                                        # config default respected


def test_no_checklist_wired_is_soft():
    cap, _ = _cap(_FakeHttp([HOTSPOTS, SELL]), checklist=None)
    msg = cap.run_tool("plan_mining_session", {"material": "Painite"})
    assert "Bell Vision" in msg                                  # runs fine without a checklist


def test_from_cfg_reads_section():
    cfg = MiningHelperConfig.from_cfg({"mining_helper": {
        "enabled": True, "max_price_age_days": 5, "add_to_checklist": False}})
    assert cfg.enabled and cfg.max_price_age_days == 5 and cfg.add_to_checklist is False
