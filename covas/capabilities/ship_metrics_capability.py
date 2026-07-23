"""Metric-agnostic ship-metric query capability (issue #139) — "what's my current jump range",
"top three small ships by jump range", answered from the Commander's REAL fleet.

This capability is deliberately generic: it knows how to answer two SHAPES of question —

  * CURRENT: "my current <metric>" -> compute the metric for the ship being flown, using its live
    loadout + engineering + current cargo & fuel, and state the load basis.
  * RANKING: "top N <class> ships by <metric>" -> rank the OWNED ships (#134) that have a remembered
    loadout (#135) by the metric at a CONSISTENT reference load (full tank, empty cargo — the usual
    quoted basis), filtered by size class (small/medium/large), best N first.

— but nothing about what any metric IS. Both shapes dispatch through the `MetricRegistry`
(`nav/ship_metrics.py`) by spoken metric name. Today the only registered metric is jump range; adding
"dps", "shields", "cargo", "top speed"… is a new registry entry + its compute, with NO change here.

Grounding discipline (as everywhere): every number is COMPUTED from real loadout + spec data and
relayed, never invented. A ship with no remembered build is reported UNKNOWN ("fly it once and I'll
have its range"), not guessed. Fail-soft: any error is spoken, never raised into the voice loop.

All I/O is injected (owned-ships getter, per-ship + active loadout getters, live-state getter) so the
default `pytest` run is offline and free (DESIGN §9).
"""
from __future__ import annotations

from collections.abc import Callable

from ..ed.loadout import LoadoutSnapshot
from ..ed.owned_ships import display_name, match_ships
from ..nav.ship_metrics import LiveState, Metric, MetricInput, MetricRegistry, default_registry
from ..nav.ship_specs import Spec, get_spec
from ..nav.ships import id_from_journal_symbol
from .base import HelpMeta, Slot

_CURRENT_TOOL = "ship_metric_current"
_RANKING_TOOL = "ship_metric_ranking"

# Spoken size-class -> bundled spec pad_size (1=S, 2=M, 3=L). Aliases fold common phrasings.
_CLASS_TO_PAD = {
    "small": 1, "s": 1,
    "medium": 2, "med": 2, "m": 2,
    "large": 3, "l": 3, "big": 3, "huge": 3,
}
_PAD_WORD = {1: "small", 2: "medium", 3: "large"}

_MAX_COUNT = 10          # cap a spoken ranking so it doesn't read out forever
_DEFAULT_COUNT = 3


class ShipMetricsCapability:
    """Advertises the metric-agnostic current + ranking tools, answering them from the injected
    owned-ships / per-ship loadout / live-state seams crossed with the bundled hull specs and the
    metric registry (live EDContext in the app; stubs in tests)."""
    # Tiering group (issue #84): shares the engineering token-budget cluster.
    TIERING_GROUP = "engineering"

    def __init__(
        self,
        *,
        get_owned: Callable[[], list[dict]],
        get_ship_loadout: Callable[[str], LoadoutSnapshot | None],
        get_active_loadout: Callable[[], LoadoutSnapshot | None],
        get_live_state: Callable[[], dict],
        registry: MetricRegistry | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._get_owned = get_owned
        self._get_ship_loadout = get_ship_loadout
        self._get_active_loadout = get_active_loadout
        self._get_live_state = get_live_state
        self._registry = registry or default_registry()
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        metrics = ", ".join(f"'{m.label}'" for m in self._registry.metrics())
        metric_arg = {
            "type": "string",
            "description": (f"Which metric, as spoken. Available: {metrics}. Defaults to jump range "
                            "when omitted."),
        }
        return [
            {
                "name": _CURRENT_TOOL,
                "description": (
                    "Compute a metric (jump range) for the Commander's CURRENT ship from its live "
                    "loadout, engineering and current cargo & fuel — 'what's my current jump range', "
                    "'my range right now'. The figure is COMPUTED from the real fitted FSD + mass, "
                    "accounting for what's loaded, and the answer states the load basis. Optionally "
                    "pass `ship` to compute for a named OWNED ship instead (that one is quoted at a "
                    "reference load — full tank, empty cargo — since only the current ship's live "
                    "cargo is known). ALWAYS call this rather than answering from memory; if the "
                    "ship's build hasn't been seen yet, it says so — do not guess."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metric": dict(metric_arg),
                        "ship": {
                            "type": "string",
                            "description": "Optional owned ship, as spoken ('my Anaconda', a custom "
                                           "name/ident). Omit for the ship currently being flown.",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": _RANKING_TOOL,
                "description": (
                    "Rank the Commander's OWNED ships by a metric (jump range) and return the top N "
                    "— 'top three ships by jump range', 'my best small ship for range', 'which of my "
                    "ships jumps furthest'. Ranks only ships with a REMEMBERED build (fly a ship "
                    "once to capture it); a ship with no remembered build is reported UNKNOWN, never "
                    "guessed. Every ship is compared at a CONSISTENT reference load (full tank, "
                    "empty cargo) — state that basis. Optionally filter by size class with "
                    "`ship_class` (small/medium/large) and set `count` (default 3)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metric": dict(metric_arg),
                        "ship_class": {
                            "type": "string",
                            "enum": ["small", "medium", "large"],
                            "description": "Optional landing-pad size class to filter to. Omit for "
                                           "the whole fleet.",
                        },
                        "count": {
                            "type": "integer",
                            "description": f"How many top ships to return (default {_DEFAULT_COUNT}).",
                        },
                    },
                    "required": [],
                },
            },
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="ship metrics",
            group="your ship",
            one_liner=("I compute numbers about your real, engineered fleet — your current jump "
                       "range accounting for cargo, or a ranking of your ships by jump range — from "
                       "each ship's remembered loadout, so you don't have to open Coriolis."),
            example="what's my current jump range",
            slots=(
                Slot(param="metric",
                     phrasings=("a metric", "jump range, range"),
                     example="rank my ships by jump range",
                     help_text="Name the metric you care about — jump range today, with more to "
                               "come."),
                Slot(param="ship_class",
                     phrasings=("a size class", "small, medium, large"),
                     example="top three small ships by jump range",
                     help_text="Filter a ranking to a landing-pad size class — small, medium or "
                               "large."),
            ),
            help_when_active=("Ask for your current jump range, or ask me to rank your ships by "
                              "jump range — I compute it from each ship's real engineered build."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _CURRENT_TOOL:
                return self._current(inp)
            if name == _RANKING_TOOL:
                return self._ranking(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Ship-metric error: {e}"

    # -- current ----------------------------------------------------------------------
    def _current(self, inp: dict) -> str:
        metric = self._resolve_metric(inp)
        if isinstance(metric, str):
            return metric
        ship_q = str(inp.get("ship") or "").strip()
        if ship_q:
            return self._named_metric(metric, ship_q)

        active = self._get_active_loadout()
        if not isinstance(active, LoadoutSnapshot) or not active.modules:
            return ("I don't have your current ship's loadout yet — board your ship (or open "
                    "outfitting) and I'll read its build.")
        spec = self._spec_for_symbol(getattr(active, "ship", None))
        live = self._live_state()
        res = metric.compute(MetricInput(snapshot=active, spec=spec, live=live))
        label = self._active_label(active, spec)
        if not res.known or res.value is None:
            return f"I can't work out the {metric.label} of your {label} — {res.basis}."
        hedge = " (rough — I don't have every module's mass)" if res.approximate else ""
        return (f"Your {label}'s {metric.label} is {res.value:.1f} {res.unit}{hedge} — "
                f"{res.basis}.")

    def _named_metric(self, metric: Metric, ship_q: str) -> str:
        resolved = self._resolve_owned(ship_q)
        if isinstance(resolved, str):
            return resolved
        label, snap, spec = resolved
        res = metric.compute(MetricInput(snapshot=snap, spec=spec, live=None))
        if not res.known or res.value is None:
            return f"I can't work out the {metric.label} of your {label} — {res.basis}."
        hedge = " (rough — I don't have every module's mass)" if res.approximate else ""
        return (f"Your {label}'s {metric.label} is {res.value:.1f} {res.unit}{hedge}, at a "
                f"reference load ({res.basis}){'' if res.approximate else ''}.")

    # -- ranking ----------------------------------------------------------------------
    def _ranking(self, inp: dict) -> str:
        metric = self._resolve_metric(inp)
        if isinstance(metric, str):
            return metric
        pad, class_word = self._resolve_class(inp)
        count = self._resolve_count(inp)

        owned = self._get_owned() or []
        if not owned:
            return ("I haven't recorded any of your ships yet — fly them and I'll remember each "
                    "build, then I can rank them.")

        ships: list[tuple[str, MetricInput]] = []
        no_build: list[str] = []
        for rec in owned:
            sid = rec.get("ship_id")
            if sid is None:
                continue
            spec = self._spec_for_symbol(rec.get("ship_type"))
            if pad is not None and (spec is None or spec.pad_size != pad):
                continue
            label = display_name(rec)
            snap = self._get_ship_loadout(str(sid))
            if not isinstance(snap, LoadoutSnapshot) or not snap.modules:
                no_build.append(label)
                continue
            # Rank everyone at the SAME reference load (no live state) so the comparison is honest —
            # even the active ship, whose live cargo would otherwise skew it against the others.
            ships.append((label, MetricInput(snapshot=snap, spec=spec, live=None)))

        scope = f"{class_word} " if class_word else ""
        if not ships:
            miss = (f" I've not seen a build for: {', '.join(no_build[:6])}." if no_build else "")
            return (f"I don't have a remembered build for any of your {scope}ships yet, so I can't "
                    f"rank them by {metric.label}.{miss}")

        ranked, _unknown = self._registry.rank(metric, ships)
        top = ranked[:count]
        parts = [f"Top {len(top)} {scope}ship{'s' if len(top) != 1 else ''} by {metric.label} "
                 f"(reference load — full tank, empty cargo):"]
        for i, (label, res) in enumerate(top, 1):
            hedge = " (rough)" if res.approximate else ""
            parts.append(f"{i}. {label}, {res.value:.1f} {res.unit}{hedge}.")
        if no_build:
            parts.append(f"Unknown (no remembered build yet): {', '.join(no_build[:5])}.")
        return " ".join(parts)

    # -- resolution helpers -----------------------------------------------------------
    def _resolve_metric(self, inp: dict):
        """The `Metric` a request names, or a spoken error to relay. Defaults to jump range."""
        spoken = str(inp.get("metric") or "").strip()
        if not spoken:
            metric = self._registry.get("jump_range") or (self._registry.metrics()[0]
                                                           if self._registry.metrics() else None)
            if metric is None:
                return "I don't have any ship metrics configured."
            return metric
        metric = self._registry.resolve(spoken)
        if metric is None:
            have = ", ".join(m.label for m in self._registry.metrics()) or "none"
            return f"I don't compute '{spoken}' yet. I can do: {have}."
        return metric

    def _resolve_class(self, inp: dict) -> tuple[int | None, str]:
        raw = str(inp.get("ship_class") or "").strip().lower()
        if not raw:
            return None, ""
        pad = _CLASS_TO_PAD.get(raw)
        return (pad, _PAD_WORD.get(pad, "")) if pad is not None else (None, "")

    def _resolve_count(self, inp: dict) -> int:
        raw = inp.get("count")
        n = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else _DEFAULT_COUNT
        return max(1, min(n, _MAX_COUNT))

    def _resolve_owned(self, ship_q: str):
        """Resolve a spoken ship to `(label, remembered snapshot, spec)`, or a spoken message."""
        owned = self._get_owned() or []
        entries = {str(r["ship_id"]): r for r in owned if r.get("ship_id") is not None}
        matches = match_ships(entries, ship_q)
        if not matches:
            if not owned:
                return ("I haven't recorded any of your ships yet — fly them and I'll remember each "
                        "build.")
            sample = ", ".join(display_name(r) for r in owned[:8])
            return f"I don't see an owned ship matching '{ship_q}'. You own: {sample}."
        if len(matches) > 1:
            names = ", ".join(display_name(rec) for _sid, rec in matches[:6])
            return f"More than one of your ships matches '{ship_q}' — {names}. Which one?"
        sid, rec = matches[0]
        snap = self._get_ship_loadout(str(sid))
        label = display_name(rec)
        spec = self._spec_for_symbol(rec.get("ship_type"))
        if not isinstance(snap, LoadoutSnapshot) or not snap.modules:
            return (f"I haven't seen your {label}'s build yet — fly it once and I'll have its "
                    "numbers.")
        return label, snap, spec

    def _spec_for_symbol(self, symbol) -> Spec | None:
        sym = str(symbol or "").strip()
        if not sym:
            return None
        cid = id_from_journal_symbol(sym)
        return get_spec(cid) if cid else None

    def _live_state(self) -> LiveState:
        snap = self._live_snapshot()
        return LiveState(
            cargo=self._num(snap.get("cargo")),
            fuel_main=self._num(snap.get("fuel_main")),
            fuel_capacity=self._num(snap.get("fuel_capacity")),
        )

    def _live_snapshot(self) -> dict:
        try:
            snap = self._get_live_state()
            return dict(snap) if isinstance(snap, dict) else {}
        except Exception as e:  # noqa: BLE001 — a bad getter must not break the answer
            self._logline(f"live-state read failed: {e}")
            return {}

    def _active_ship_id(self) -> str | None:
        active = self._get_active_loadout()
        sid = getattr(active, "ship_id", None) if active is not None else None
        return str(sid) if sid is not None else None

    def _active_label(self, active: LoadoutSnapshot, spec: Spec | None) -> str:
        name = spec.name if spec is not None else (active.ship or "ship").replace("_", " ").title()
        return f'{name} "{active.ship_name}"' if active.ship_name else name

    @staticmethod
    def _num(v: object) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
