"""Unit tests for SystemSearchCapability (offline, DESIGN §9).

Fake http (recorded systems fixture) + fake clipboard + a stubbed current system, so nothing
touches the network. Covers the Search Prompt 4 acceptance points: conversational slot-filling
across turns, Any-defaults (an unspoken slot is absent from the query), a result copies the
system, and an invalid slot value is CAUGHT (validated, corrected, not sent to Spansh).
"""
from __future__ import annotations

import json
from pathlib import Path

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.system_search_capability import (SystemSearchCapability,
                                                         SystemSearchConfig)

_FIXTURE = Path(__file__).parent / "fixtures" / "spansh_systems_federation_sol.json"


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class FakeHttp:
    """Returns the recorded Spansh systems body and records requests (to assert payloads /
    'searched exactly once' / 'never searched')."""

    def __init__(self, status=200, body=None) -> None:
        self._status = status
        self._body = body if body is not None else _fixture()
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "payload": payload})
        return self._status, self._body


class FakeClipboard:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def __call__(self, text: str) -> None:
        self.copied.append(text)


def _cap(*, http=None, clip=None, system="Sol"):
    http = http or FakeHttp()
    clip = clip or FakeClipboard()
    cap = SystemSearchCapability(
        SystemSearchConfig(enabled=True),
        http=http,
        get_current_system=(lambda: system),
        clipboard=clip,
    )
    return cap, http, clip


def _filters(http) -> dict:
    return http.calls[-1]["payload"]["filters"]


# --- happy path: a slot search returns the nearest match + copies it -----------------------

def test_single_slot_search_returns_and_copies_nearest():
    # Nearest match is a few ly away (not the current system) -> spoken AND copied.
    body = {"results": [{"name": "Wolf 359", "distance": 7.78, "allegiance": "Federation",
                         "government": "Democracy", "security": "High"}]}
    cap, http, clip = _cap(http=FakeHttp(body=body))
    out = cap.run_tool("search_star_systems", {"allegiance": "Federation"})
    assert len(http.calls) == 1
    assert _filters(http) == {"allegiance": {"value": ["Federation"]}}
    assert "Wolf 359" in out and clip.copied == ["Wolf 359"]
    assert "clipboard" in out.lower()


def test_result_that_is_current_system_is_not_copied():
    # Task 4: the nearest match in the fixture is Sol (distance 0.0), the current system ->
    # spoken as such, and NOT copied (you're already there).
    cap, http, clip = _cap()
    out = cap.run_tool("search_star_systems", {"allegiance": "Federation"})
    assert clip.copied == [] and "already there" in out.lower()


def test_any_defaults_only_spoken_slots_appear():
    cap, http, _ = _cap()
    cap.run_tool("search_star_systems", {"security": "High"})
    assert _filters(http) == {"security": {"value": ["High"]}}   # nothing else defaulted in


# --- refinement across turns: re-call with accumulated slots (stateless) --------------------

def test_slots_accumulate_across_turns():
    cap, http, _ = _cap()
    cap.run_tool("search_star_systems", {"allegiance": "Empire"})
    # Second turn carries the accumulated slots plus a new one — a fresh, independent call.
    cap.run_tool("search_star_systems",
                 {"allegiance": "Empire", "security": "High", "government": "Corporate"})
    assert len(http.calls) == 2
    assert _filters(http) == {
        "allegiance": {"value": ["Empire"]},
        "security": {"value": ["High"]},
        "government": {"value": ["Corporate"]},
    }


# --- loose spoken values are normalized to canonical Spansh values --------------------------

def test_spoken_values_normalized_before_query():
    cap, http, _ = _cap()
    cap.run_tool("search_star_systems", {"allegiance": "imperial", "economy": "mining",
                                         "power": "mahon"})
    assert _filters(http) == {
        "allegiance": {"value": ["Empire"]},
        "primary_economy": {"value": ["Extraction"]},
        "power": {"value": ["Edmund Mahon"]},
    }


def test_population_min_becomes_a_range():
    cap, http, _ = _cap()
    cap.run_tool("search_star_systems", {"min_population": 1_000_000_000})
    # Spansh numeric filters are {"value", "comparison"}; a floor+ceil pair -> an inclusive range.
    f = _filters(http)["population"]
    assert f["comparison"] == "<=>"
    assert f["value"][0] == 1_000_000_000 and f["value"][1] >= 1_000_000_000


def test_boolean_colonization_slot():
    cap, http, _ = _cap()
    cap.run_tool("search_star_systems", {"being_colonised": True})
    assert _filters(http) == {"is_being_colonised": {"value": True}}


# --- invalid slot value is CAUGHT (validated, not spoken as a real filter) ------------------

def test_invalid_enum_value_is_caught_and_corrected():
    cap, http, clip = _cap()
    out = cap.run_tool("search_star_systems", {"allegiance": "Klingon"})
    assert http.calls == []          # never queried Spansh with an unvalidated value
    assert clip.copied == []
    assert "didn't recognize" in out.lower() or "recognise" in out.lower()


def test_invalid_value_suggests_a_real_vocab_value():
    cap, _, _ = _cap()
    # "federatino" (typo) should recover to the real "Federation".
    out = cap.run_tool("search_star_systems", {"allegiance": "federatino"})
    assert "Federation" in out


def test_one_bad_slot_blocks_the_whole_search():
    cap, http, clip = _cap()
    out = cap.run_tool("search_star_systems", {"security": "High", "government": "Klingon"})
    assert http.calls == [] and clip.copied == []      # a single bad value stops the query
    assert "government" in out.lower()


# --- guards: nothing to search / no reference system ---------------------------------------

def test_no_slots_asks_for_a_criterion():
    cap, http, _ = _cap()
    out = cap.run_tool("search_star_systems", {})
    assert http.calls == []
    assert "what kind" in out.lower() or "allegiance" in out.lower()


def test_no_current_system_is_spoken_not_raised():
    cap, http, _ = _cap(system=None)
    out = cap.run_tool("search_star_systems", {"allegiance": "Federation"})
    assert http.calls == []
    assert "current system" in out.lower()


def test_near_override_is_used_as_reference():
    cap, http, _ = _cap(system=None)
    cap.run_tool("search_star_systems", {"allegiance": "Federation", "near": "Shinrarta Dezhra"})
    assert http.calls[0]["payload"]["reference_system"] == "Shinrarta Dezhra"


# --- failure modes fail soft ---------------------------------------------------------------

def test_empty_results_speaks_a_soft_line():
    cap, http, clip = _cap(http=FakeHttp(body={"count": 0, "results": []}))
    out = cap.run_tool("search_star_systems", {"allegiance": "Federation"})
    assert clip.copied == []
    assert "couldn't find" in out.lower() or "relax" in out.lower()


def test_spansh_400_is_spoken():
    cap, _, _ = _cap(http=FakeHttp(status=400, body={"error": "Invalid request"}))
    out = cap.run_tool("search_star_systems", {"allegiance": "Federation"})
    assert "recognise" in out.lower() or "recognize" in out.lower()


def test_unknown_tool_name():
    cap, _, _ = _cap()
    assert "Unknown tool" in cap.run_tool("nope", {})


# --- registry contract + help vocabulary ---------------------------------------------------

def test_help_meta_is_complete_and_registers():
    cap, _, _ = _cap()
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)                              # would raise if metadata were incomplete
    assert "star systems" in reg.categories()


def test_every_advertised_slot_is_a_real_spansh_param():
    # The anti-drift guard made concrete: each refinement help advertises must be an ACCEPTED
    # Spansh param for the category, so build_query never fails loud on an advertised slot.
    from covas.search import category
    cap, _, _ = _cap()
    accepted = set(category("star_systems").param_names())
    for slot in cap.help_meta().slots:
        assert slot.param in accepted, f"advertised slot {slot.param!r} isn't a Spansh param"


def test_help_vocabulary_exposes_canonical_values():
    cap, _, _ = _cap()
    vocab = cap.help_vocabulary()
    assert "Federation" in vocab["allegiance"]
    assert "Edmund Mahon" in vocab["power"]
    # Powerplay 2.0: the retired power is absent, its replacement present.
    assert "Zachary Hudson" not in vocab["power"] and "Jerome Archer" in vocab["power"]
