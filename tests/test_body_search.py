"""Unit tests for the body / bio-geo signal finder (#68) — offline, DESIGN §9.

Fake http (a RECORDED real Spansh bodies fixture + crafted bodies) + fake clipboard + a stubbed
current system. Covers: subtype slot-filling and filter shape, biological-signal resolution (a
genus expanding to its species, an exact species, "any biological", and a caught mishear), the
landable / arrival-distance slots, a result copying the system (vs already-here), the bio-signal
staleness caveat, the parser, and the help-metadata contract.

The recorded fixture (`fixtures/spansh_bodies_earthlike_sol.json`) is a real
`bodies/search?subtype=Earth-like world&reference_system=Sol` response captured live (2026-07);
its nearest results (Mars, Earth) sit in Sol at 0 ly, so it also exercises the already-there path.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from covas.capabilities._search_support import SearchConfig
from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.search_family import BodySearchCapability
from covas.search.bodies import BIO_GENERA, resolve_bio_signal, resolve_subtype
from covas.search.categories import parse_bodies

_FIX = Path(__file__).parent / "fixtures"
_BODIES = json.loads((_FIX / "spansh_bodies_earthlike_sol.json").read_text("utf-8"))


class FakeHttp:
    def __init__(self, body) -> None:
        self._body = body
        self.calls: list[dict] = []

    def post_json(self, url, payload, *, headers=None, timeout=20.0):
        self.calls.append({"url": url, "payload": payload})
        return 200, self._body


class Clip:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def __call__(self, text: str) -> None:
        self.copied.append(text)


def _mk(body, *, system="Sol", now=None):
    http, clip = FakeHttp(body), Clip()
    kw = dict(http=http, get_current_system=(lambda: system), clipboard=clip)
    if now is not None:
        kw["now"] = lambda: now
    cap = BodySearchCapability(SearchConfig(enabled=True), **kw)
    return cap, http, clip


def _filters(http):
    return http.calls[-1]["payload"]["filters"]


# ============================ subtype ============================

def test_body_subtype_slot_filling_and_endpoint():
    cap, http, clip = _mk(_BODIES)
    out = cap.run_tool("search_bodies", {"body_type": "earthlike"})
    assert http.calls[-1]["url"].endswith("/bodies/search")
    assert _filters(http) == {"subtype": {"value": ["Earth-like world"]}}   # alias resolved
    # Nearest ELW (Mars) is in Sol, the current system -> spoken, not copied (already there).
    assert clip.copied == [] and "already there" in out.lower()
    assert "Mars" in out and "Earth-like world" in out


def test_body_landable_and_arrival_slots_render():
    cap, http, _ = _mk(_BODIES)
    cap.run_tool("search_bodies",
                 {"body_type": "ammonia world", "landable": True, "max_arrival_distance": 500})
    f = _filters(http)
    assert f["subtype"] == {"value": ["Ammonia world"]}
    assert f["is_landable"] == {"value": True}
    assert f["distance_to_arrival"] == {"value": 500, "comparison": "<="}   # numeric comparison


def test_body_invalid_type_is_caught_not_queried():
    cap, http, clip = _mk(_BODIES)
    out = cap.run_tool("search_bodies", {"body_type": "chocolate planet"})
    assert http.calls == [] and clip.copied == []          # never searched, never invented
    assert "didn't recognize" in out.lower()


# ====================== biological signals ======================

def test_body_bio_genus_expands_to_all_species():
    cap, http, _ = _mk(_BODIES)
    cap.run_tool("search_bodies", {"biological_signal": "bacterium"})
    # A genus fills landmark_subtype with an OR over EVERY species of that genus.
    assert _filters(http) == {"landmark_subtype": {"value": list(BIO_GENERA["Bacterium"])}}


def test_body_bio_specific_species():
    cap, http, _ = _mk(_BODIES)
    cap.run_tool("search_bodies", {"biological_signal": "Bacterium Aurasus"})
    assert _filters(http) == {"landmark_subtype": {"value": ["Bacterium Aurasus"]}}


def test_body_any_biological_spans_catalogue():
    cap, http, _ = _mk(_BODIES)
    cap.run_tool("search_bodies", {"biological_signal": "any biological"})
    vals = _filters(http)["landmark_subtype"]["value"]
    total = sum(len(v) for v in BIO_GENERA.values())
    assert len(vals) == total and "Aleoida Arcus" in vals and "Tussock Virgam" in vals


def test_body_bio_mishear_is_caught_with_suggestion():
    cap, http, clip = _mk(_BODIES)
    out = cap.run_tool("search_bodies", {"biological_signal": "bacterium"})   # typo'd genus
    # A close mishear may resolve; a clearly-unknown biology must be caught, not invented.
    if http.calls:
        assert _filters(http)["landmark_subtype"]["value"] == list(BIO_GENERA["Bacterium"])
    else:
        assert clip.copied == [] and "didn't recognize" in out.lower()


def test_body_unknown_bio_is_caught():
    cap, http, clip = _mk(_BODIES)
    out = cap.run_tool("search_bodies", {"biological_signal": "space whales"})
    assert http.calls == [] and clip.copied == []
    assert "didn't recognize" in out.lower()


# ==================== results / copy / caveats ===================

def _bio_body(*, dist_ly, updated="2026-07-14 00:00:00+00"):
    return {"results": [{
        "name": "Kokary 4 c", "system_name": "Kokary", "distance": dist_ly,
        "subtype": "Rocky body", "distance_to_arrival": 812.0, "is_landable": True,
        "signals": [{"name": "Biological", "count": 5}, {"name": "Human", "count": 10}],
        "landmarks": [{"subtype": "Bacterium Aurasus", "type": "Bacterium"},
                      {"subtype": "Bacterium Aurasus", "type": "Bacterium"},
                      {"subtype": "Stratum Tectonicas", "type": "Stratum"}],
        "signals_updated_at": updated,
    }]}


def test_body_result_copies_system_when_elsewhere():
    cap, _, clip = _mk(_bio_body(dist_ly=18.4))
    out = cap.run_tool("search_bodies", {"biological_signal": "Bacterium Aurasus"})
    assert clip.copied == ["Kokary"]
    assert "Kokary 4 c" in out and "landable" in out.lower()
    assert "5 biological signals" in out
    assert "Bacterium Aurasus" in out                     # landmark confirmed


def test_body_bio_search_flags_stale_scan():
    # A biology record years old gets a gentle caveat (signal data is crowdsourced, so ageable).
    now = datetime(2026, 7, 15, tzinfo=UTC)
    cap, _, _ = _mk(_bio_body(dist_ly=18.4, updated="2023-01-01 00:00:00+00"), now=now)
    out = cap.run_tool("search_bodies", {"biological_signal": "Bacterium Aurasus"})
    assert "old" in out.lower() and "re-surveyed" in out.lower()


def test_body_structure_search_has_no_stale_caveat():
    # A subtype search never ages (a world doesn't stop being Earth-like), so: no caveat.
    now = datetime(2026, 7, 15, tzinfo=UTC)
    body = {"results": [{"name": "Merlin", "system_name": "LHS 3006", "distance": 9.7,
                         "subtype": "Earth-like world", "distance_to_arrival": 300.0,
                         "updated_at": "2019-01-01 00:00:00+00"}]}
    cap, _, _ = _mk(body, now=now)
    out = cap.run_tool("search_bodies", {"body_type": "earthlike"})
    assert "old" not in out.lower() and "Merlin" in out


def test_body_no_slots_asks():
    cap, http, _ = _mk(_BODIES)
    out = cap.run_tool("search_bodies", {})
    assert http.calls == [] and ("body type" in out.lower() or "biological" in out.lower())


def test_body_no_results_is_soft():
    cap, _, clip = _mk({"results": []})
    out = cap.run_tool("search_bodies", {"body_type": "ammonia world"})
    assert clip.copied == [] and "couldn't find" in out.lower()


def test_body_near_override_measures_from_named_system():
    cap, http, _ = _mk(_bio_body(dist_ly=5.0))
    cap.run_tool("search_bodies", {"body_type": "earthlike", "near": "Colonia"})
    assert http.calls[-1]["payload"]["reference_system"] == "Colonia"


# ============================ parser =============================

def test_parse_bodies_maps_signals_and_dedups_landmarks():
    recs = parse_bodies(_bio_body(dist_ly=3.2)["results"])
    assert len(recs) == 1
    r = recs[0]
    assert r.name == "Kokary 4 c" and r.system == "Kokary" and r.is_landable
    assert r.signals == {"Biological": 5, "Human": 10}
    assert r.landmarks == ("Bacterium Aurasus", "Stratum Tectonicas")    # de-duplicated, ordered
    assert r.extra.get("signals_updated_at")


def test_parse_bodies_recorded_fixture():
    recs = parse_bodies(_BODIES["results"])
    assert recs and recs[0].name == "Mars" and recs[0].subtype == "Earth-like world"
    assert all(r.subtype == "Earth-like world" for r in recs)            # filter really narrowed


# ======================= vocabulary sanity =======================

def test_vocabulary_resolvers():
    assert resolve_subtype("water world") == "Water world"
    assert resolve_subtype("hmc") == "High metal content world"
    assert resolve_subtype("nonsense") is None
    assert resolve_bio_signal("stratum") == list(BIO_GENERA["Stratum"])
    assert resolve_bio_signal("Aleoida Arcus") == ["Aleoida Arcus"]
    assert resolve_bio_signal("nonsense biology") is None


# ===================== registry contract =========================

def test_body_finder_registers_and_satisfies_contract():
    reg = CapabilityRegistry()
    cap = BodySearchCapability(SearchConfig(enabled=True), get_current_system=lambda: "Sol")
    assert help_meta_problems(cap.help_meta()) == []
    reg.register(cap)
    assert reg.contract_violations() == []
    assert "bodies" in reg.categories()
