"""Unit tests for the ship-metric registry + query capability (issue #139; offline, DESIGN §9).

Covers the metric-agnostic registry (resolve by spoken name, direction-aware ranking) INCLUDING a
trivial second dummy metric that proves dispatch/ranking never special-case jump range; the
current-ship path with injected live cargo/fuel; and the fleet ranking with a class filter over a
faked owned fleet — with an unknown-build ship reported unknown, never guessed. All via stubbed
getters — no journal, no network.
"""
from __future__ import annotations

from covas.capabilities.base import CapabilityRegistry, help_meta_problems
from covas.capabilities.ship_metrics_capability import ShipMetricsCapability
from covas.ed.loadout import Engineering, LoadoutSnapshot, ShipModule
from covas.nav.jump_range import resolve_fsd, single_jump_range
from covas.nav.ship_metrics import (
    Metric,
    MetricInput,
    MetricRegistry,
    MetricResult,
    default_registry,
)

# ---- fixtures ------------------------------------------------------------------------------

def _fsd(size: int, cls: int = 5, eng: Engineering | None = None) -> ShipModule:
    return ShipModule(slot="FrameShiftDrive", item=f"int_hyperdrive_size{size}_class{cls}",
                      engineering=eng)


def _ship(ship: str, ship_id: int, fsd: ShipModule, *extra, max_range: float | None,
          fuel_cap: float = 16.0) -> LoadoutSnapshot:
    return LoadoutSnapshot(ship=ship, ship_id=ship_id, max_jump_range=max_range,
                           fuel_capacity=fuel_cap, modules=(fsd, *extra))


def _calibrated_max_range(fsd: ShipModule, dry: float) -> float:
    """The game's MaxJumpRange a dry mass `dry` would produce, so calibration recovers `dry`."""
    fit = resolve_fsd(LoadoutSnapshot(modules=(fsd,)))
    return single_jump_range(fit, dry + fit.max_fuel)


def _build_fleet():
    """A faked owned fleet + a per-ship loadout store keyed by ShipID.

      * Hauler (small): light -> long range.
      * Sidewinder (small): heavier dry mass -> shorter range.
      * Anaconda (large): big FSD.
      * Diamondback Explorer (small): OWNED but NO remembered build (unknown).
    """
    hauler_fsd = _fsd(2)
    sidey_fsd = _fsd(2)
    conda_fsd = _fsd(6)
    loadouts = {
        "1": _ship("hauler", 1, hauler_fsd, max_range=_calibrated_max_range(hauler_fsd, 40.0)),
        "2": _ship("sidewinder", 2, sidey_fsd, max_range=_calibrated_max_range(sidey_fsd, 90.0)),
        "3": _ship("anaconda", 3, conda_fsd, max_range=_calibrated_max_range(conda_fsd, 500.0),
                   fuel_cap=32.0),
        # ship 4 (Diamondback Explorer, small) intentionally has NO loadout row.
    }
    owned = [
        {"ship_id": 1, "ship_type": "hauler", "name": None, "active": False},
        {"ship_id": 2, "ship_type": "sidewinder", "name": None, "active": False},
        {"ship_id": 3, "ship_type": "anaconda", "name": "Big Girl", "active": True},
        {"ship_id": 4, "ship_type": "diamondbackxl", "name": None, "active": False},
    ]
    return owned, loadouts


def _capability(owned, loadouts, *, active=None, live=None, registry=None):
    return ShipMetricsCapability(
        get_owned=lambda: owned,
        get_ship_loadout=lambda sid: loadouts.get(str(sid)),
        get_active_loadout=lambda: active,
        get_live_state=lambda: live or {},
        registry=registry,
    )


# ---- registry: metric-agnostic dispatch + a dummy second metric ----------------------------

def test_registry_resolves_by_spoken_name_and_key():
    reg = default_registry()
    assert reg.resolve("jump range").key == "jump_range"
    assert reg.resolve("range").key == "jump_range"
    assert reg.resolve("my jump range").key == "jump_range"   # loose containment
    assert reg.get("jump_range").key == "jump_range"
    assert reg.resolve("dps") is None                          # not registered -> None


def test_registry_ranking_is_metric_agnostic_with_dummy_metric():
    """A trivial SECOND metric proves the registry's rank() never special-cases jump range: it just
    calls compute() and sorts by direction. Adding a metric is data, not code here."""
    def cargo_compute(inp: MetricInput) -> MetricResult:
        # "cargo racks" — count cargo modules; higher is better. Unknown when no snapshot modules.
        n = sum(1 for m in inp.snapshot.modules if "cargorack" in m.item)
        return MetricResult(value=float(n), unit="racks", basis="fitted racks")

    cargo_metric = Metric(key="cargo_racks", names=("cargo", "racks"), label="cargo racks",
                          unit="racks", higher_is_better=True, compute=cargo_compute)
    lower_metric = Metric(key="cargo_racks_min", names=("fewest racks",), label="fewest racks",
                          unit="racks", higher_is_better=False, compute=cargo_compute)

    reg = MetricRegistry()
    reg.register(cargo_metric)
    reg.register(lower_metric)

    a = MetricInput(snapshot=LoadoutSnapshot(modules=(
        ShipModule(slot="s1", item="int_cargorack_size6_class1"),
        ShipModule(slot="s2", item="int_cargorack_size5_class1"))))
    b = MetricInput(snapshot=LoadoutSnapshot(modules=(
        ShipModule(slot="s1", item="int_cargorack_size2_class1"),)))
    ships = [("A", a), ("B", b)]

    ranked_hi, _ = reg.rank(cargo_metric, ships)
    assert [lbl for lbl, _ in ranked_hi] == ["A", "B"]        # higher-is-better: A (2) first
    ranked_lo, _ = reg.rank(lower_metric, ships)
    assert [lbl for lbl, _ in ranked_lo] == ["B", "A"]        # lower-is-better flips it


def test_registry_rank_separates_unknown():
    reg = default_registry()
    metric = reg.get("jump_range")
    good = MetricInput(snapshot=_ship("hauler", 1, _fsd(2),
                                      max_range=_calibrated_max_range(_fsd(2), 40.0)))
    bad = MetricInput(snapshot=LoadoutSnapshot(modules=()))   # no FSD -> unknown
    ranked, unknown = reg.rank(metric, [("Good", good), ("Bad", bad)])
    assert [l for l, _ in ranked] == ["Good"]
    assert [l for l, _ in unknown] == ["Bad"]


# ---- current-ship path ---------------------------------------------------------------------

def test_current_jump_range_uses_live_cargo_and_fuel():
    owned, loadouts = _build_fleet()
    active = loadouts["3"]        # the Anaconda is active
    live = {"cargo": 120.0, "fuel_main": 32.0, "fuel_capacity": 32.0}
    cap = _capability(owned, loadouts, active=active, live=live)
    out = cap.run_tool("ship_metric_current", {})
    assert "jump range" in out.lower()
    assert "cargo" in out.lower()
    # Laden figure must be lower than the empty-cargo reference for the same ship.
    empty = _capability(owned, loadouts, active=active,
                        live={"cargo": 0.0, "fuel_main": 32.0, "fuel_capacity": 32.0})
    laden_val = _first_float(out)
    empty_val = _first_float(empty.run_tool("ship_metric_current", {}))
    assert laden_val < empty_val


def test_current_no_loadout_is_honest():
    cap = _capability([], {}, active=None, live={})
    out = cap.run_tool("ship_metric_current", {})
    assert "board" in out.lower() or "loadout" in out.lower()


def test_current_named_ship_uses_reference_load():
    owned, loadouts = _build_fleet()
    cap = _capability(owned, loadouts, active=loadouts["3"], live={})
    out = cap.run_tool("ship_metric_current", {"ship": "hauler"})
    assert "hauler" in out.lower() and "reference load" in out.lower()


def test_current_named_unknown_build_reported_unknown():
    owned, loadouts = _build_fleet()
    cap = _capability(owned, loadouts, active=loadouts["3"], live={})
    out = cap.run_tool("ship_metric_current", {"ship": "diamondback"})
    assert "haven't seen" in out.lower() or "fly it" in out.lower()


# ---- fleet ranking + class filter ----------------------------------------------------------

def test_ranking_small_ships_orders_correctly_and_flags_unknown():
    owned, loadouts = _build_fleet()
    cap = _capability(owned, loadouts, active=loadouts["3"], live={})
    out = cap.run_tool("ship_metric_ranking", {"ship_class": "small", "count": 3})
    # Small hulls only: Hauler (light, longest) then Sidewinder; Anaconda (large) excluded.
    assert "anaconda" not in out.lower()
    hi = out.lower().find("hauler")
    lo = out.lower().find("sidewinder")
    assert hi != -1 and lo != -1 and hi < lo          # Hauler ranked above Sidewinder
    # The never-flown small hull is reported unknown, not guessed.
    assert "unknown" in out.lower() and "diamondback" in out.lower()
    assert "reference load" in out.lower()


def test_ranking_respects_count():
    owned, loadouts = _build_fleet()
    cap = _capability(owned, loadouts, active=loadouts["3"], live={})
    out = cap.run_tool("ship_metric_ranking", {"count": 1})
    # Exactly one ranked line ("1. ...") and no "2." line.
    assert "1." in out and "2." not in out.split("Unknown")[0]


def test_ranking_no_owned_ships_is_honest():
    cap = _capability([], {}, active=None, live={})
    out = cap.run_tool("ship_metric_ranking", {})
    assert "haven't recorded" in out.lower()


def test_unknown_metric_is_declined_not_guessed():
    owned, loadouts = _build_fleet()
    cap = _capability(owned, loadouts, active=loadouts["3"], live={})
    out = cap.run_tool("ship_metric_current", {"metric": "shield strength"})
    assert "don't compute" in out.lower() and "jump range" in out.lower()


# ---- help metadata contract ----------------------------------------------------------------

def test_help_meta_is_complete_and_registers_clean():
    cap = _capability([], {}, active=None, live={})
    assert help_meta_problems(cap.help_meta()) == []
    reg = CapabilityRegistry()
    reg.register(cap)          # must not raise on the help-metadata contract
    names = {t["name"] for t in cap.tools()}
    assert names == {"ship_metric_current", "ship_metric_ranking"}


# ---- helpers -------------------------------------------------------------------------------

def _first_float(text: str) -> float:
    import re
    m = re.search(r"(\d+\.\d+)", text)
    assert m, f"no number in {text!r}"
    return float(m.group(1))
