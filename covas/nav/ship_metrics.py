"""Pluggable ship-metric registry (issue #139) — the extensibility seam behind the metric queries.

The query capability (`capabilities/ship_metrics_capability.py`) is deliberately METRIC-AGNOSTIC: it
knows how to answer "my current <metric>" and "top N <class> ships by <metric>" but nothing about
what any metric *is*. Each metric is a self-contained `Metric` entry here that declares its spoken
names, unit, ranking direction, and a pure `compute` over a `MetricInput`. Adding "dps", "shield_mj",
"cargo", "top_speed"… later is a new `Metric` + its compute — **no change to the capability, the
ranking, or the voice surface**. That is the whole point of doing the registry now, with jump range as
the single real first metric.

Contract:

    MetricInput   — everything a compute can read: the ship's `LoadoutSnapshot` (remembered/live),
                    its bundled hull `Spec` (or None), and optional live cargo/fuel (`LiveState`,
                    present only for the CURRENT ship).
    MetricResult  — value (None when the data isn't known — e.g. no remembered build), the unit, a
                    spoken load `basis`, and `known`.
    Metric        — key, spoken `names`, `label`, `unit`, `higher_is_better`, and `compute`.
    MetricRegistry — register / resolve-by-spoken-name / get-by-key / list; and `rank`, the shared
                    metric-agnostic ranking used for the fleet query.

Everything is pure + offline (the jump-range compute defers to `jump_range.py`, itself pure), so the
default `pytest` run covers it for free (DESIGN §9).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..ed.loadout import LoadoutSnapshot
from .jump_range import compute_jump_range
from .ship_specs import Spec


@dataclass(frozen=True)
class LiveState:
    """Live telemetry for the CURRENT ship only (from `EDContext.snapshot()`); None on any other
    ship, which can't know its live cargo/fuel."""
    cargo: float | None = None
    fuel_main: float | None = None
    fuel_capacity: float | None = None


@dataclass(frozen=True)
class MetricInput:
    """Everything a metric's `compute` may read for one ship. `snapshot` is the ship's remembered (or
    live) `LoadoutSnapshot`; `spec` its bundled hull spec (None for a hull not in the table); `live`
    is present only for the ship currently being flown."""
    snapshot: LoadoutSnapshot
    spec: Spec | None = None
    live: LiveState | None = None


@dataclass(frozen=True)
class MetricResult:
    """One metric's value for one ship. `value` is None when the metric can't be known from the data
    (e.g. no FSD resolvable) — the capability then reports it UNKNOWN, never a guess. `basis` is a
    short spoken description of the load/assumptions the figure used; `approximate` hedges a
    best-effort figure (e.g. the hull-only jump-range fallback)."""
    value: float | None
    unit: str
    basis: str = ""
    known: bool = True
    approximate: bool = False


@dataclass(frozen=True)
class Metric:
    """One named, computable ship metric. `names` are the spoken aliases the query surface matches
    ('jump range', 'range'); `label` is the canonical spoken noun; `higher_is_better` sets the
    ranking direction; `compute` is a pure `MetricInput -> MetricResult`."""
    key: str
    names: tuple[str, ...]
    label: str
    unit: str
    higher_is_better: bool
    compute: Callable[[MetricInput], MetricResult]


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().replace("_", " ").split())


class MetricRegistry:
    """The registry of `Metric`s. Metric-agnostic: `resolve` maps a spoken name to a metric, `rank`
    orders any set of `(label, MetricInput)` ships by any metric. The capability holds one of these
    and never special-cases a metric key."""

    def __init__(self) -> None:
        self._by_key: dict[str, Metric] = {}
        self._by_name: dict[str, Metric] = {}

    def register(self, metric: Metric) -> None:
        """Add a metric. Its key and every spoken name (normalised) become lookup routes. A later
        registration with the same key replaces it (handy in tests)."""
        self._by_key[metric.key] = metric
        self._by_name[_norm(metric.key)] = metric
        for name in metric.names:
            self._by_name[_norm(name)] = metric

    def get(self, key: str) -> Metric | None:
        return self._by_key.get(str(key or "").strip())

    def resolve(self, spoken: str) -> Metric | None:
        """The metric a spoken name refers to ('jump range', 'range', 'jump_range'), or None. Exact
        normalised match first, then loose containment so 'my jump range' still resolves."""
        q = _norm(spoken)
        if not q:
            return None
        if q in self._by_name:
            return self._by_name[q]
        for name, metric in self._by_name.items():
            if len(name) >= 3 and (name in q or q in name):
                return metric
        return None

    def keys(self) -> list[str]:
        return list(self._by_key.keys())

    def metrics(self) -> list[Metric]:
        # Distinct metrics in registration order.
        seen: set[str] = set()
        out: list[Metric] = []
        for m in self._by_key.values():
            if m.key not in seen:
                seen.add(m.key)
                out.append(m)
        return out

    def rank(self, metric: Metric,
             ships: list[tuple[str, MetricInput]]) -> tuple[list[tuple[str, MetricResult]],
                                                             list[tuple[str, MetricResult]]]:
        """Rank `ships` (each `(label, MetricInput)`) by `metric`, honouring its direction. Returns
        `(ranked, unknown)`: `ranked` are the ships with a known value, best first; `unknown` are the
        ships whose value couldn't be computed (reported as unknown, never guessed). Pure."""
        ranked: list[tuple[str, MetricResult]] = []
        unknown: list[tuple[str, MetricResult]] = []
        for label, inp in ships:
            res = metric.compute(inp)
            if res.known and res.value is not None:
                ranked.append((label, res))
            else:
                unknown.append((label, res))
        ranked.sort(key=lambda lr: lr[1].value, reverse=metric.higher_is_better)
        return ranked, unknown


# ---- the jump-range metric (the one real implementation for #139) --------------------------

def _jump_range_compute(inp: MetricInput) -> MetricResult:
    """Jump range (ly) for one ship. The CURRENT ship (live telemetry present) gets a LADEN figure at
    its real cargo/fuel; any other ship gets the REFERENCE figure (full tank, empty cargo) — the
    consistent basis fleet ranking needs. Unknown (value None) when no FSD can be resolved."""
    snap = inp.snapshot
    hull_mass = inp.spec.hull_mass if inp.spec is not None else None
    if inp.live is not None:
        res = compute_jump_range(
            snap,
            hull_mass=hull_mass,
            cargo=inp.live.cargo if inp.live.cargo is not None else 0.0,
            fuel=inp.live.fuel_main,
            fuel_capacity=inp.live.fuel_capacity,
        )
    else:
        res = compute_jump_range(snap, hull_mass=hull_mass)
    if res is None:
        # None = either no readable FSD, or no dry-mass basis (no MaxJumpRange and no hull mass in
        # the spec) — in both cases the honest answer is "unknown" rather than a fabricated figure.
        return MetricResult(value=None, unit="ly", known=False,
                            basis="I couldn't work out that ship's jump range")
    return MetricResult(value=res.value, unit="ly", basis=res.basis,
                        known=True, approximate=not res.calibrated)


JUMP_RANGE = Metric(
    key="jump_range",
    names=("jump range", "range", "jump", "hyperspace range"),
    label="jump range",
    unit="ly",
    higher_is_better=True,
    compute=_jump_range_compute,
)


def default_registry() -> MetricRegistry:
    """A `MetricRegistry` preloaded with the metrics COVAS++ ships today (jump range). Adding a new
    metric = register another `Metric` here (and its compute); nothing else changes."""
    reg = MetricRegistry()
    reg.register(JUMP_RANGE)
    return reg
