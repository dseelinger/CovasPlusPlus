"""Ship loadout snapshot — the full journal `Loadout` event, structured (N9).

ED writes a `Loadout` event whenever the loadout can have changed (boarding, outfitting,
module swaps); each is a COMPLETE snapshot, so parsing replaces the previous one wholesale.
This module is the pure capture side: raw journal symbols in, typed frozen dataclasses out —
`parse_loadout()` never invents or renames anything. Turning symbols into spoken names is
`module_names.py`'s job, at speak time, so the snapshot stays a faithful record of what the
game actually wrote (including the `*_Localised` strings, kept as fallbacks for naming).

Stored on `EDContext` (see `context.set_loadout`) by the journal watcher; read by the
LoadoutCapability's tools. Local journal data only — no CAPI, no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Modifier:
    """One engineered stat change: the journal's Label with the modified vs original value.
    `less_is_good` mirrors the journal flag (mass down = improvement, etc.)."""
    label: str
    value: float | None = None
    original: float | None = None
    less_is_good: bool = False

    def pct_change(self) -> float | None:
        """Signed % change from original, or None when either side is missing/zero."""
        if self.value is None or not self.original:
            return None
        return (self.value - self.original) / abs(self.original) * 100.0


@dataclass(frozen=True)
class Engineering:
    """A module's Engineering block: blueprint + grade + experimental + stat modifiers.
    `blueprint` and `experimental` are raw journal symbols ("PowerDistributor_HighFrequency",
    "special_powerdistributor_fast"); `experimental_localised` is the game's own display
    string when present ("Super Conduits") — preferred for speech."""
    blueprint: str
    level: int | None = None            # grade 1-5
    quality: float | None = None        # 0..1 progress within the grade
    engineer: str | None = None
    experimental: str | None = None
    experimental_localised: str | None = None
    modifiers: tuple[Modifier, ...] = ()


@dataclass(frozen=True)
class ShipModule:
    """One fitted module: where it sits (`slot`), what it is (`item` — the raw internal
    symbol, e.g. "int_hyperdrive_size5_class5"), and its state + optional engineering."""
    slot: str
    item: str
    on: bool = True
    priority: int | None = None
    health: float | None = None
    engineering: Engineering | None = None

    @property
    def engineered(self) -> bool:
        return self.engineering is not None


@dataclass(frozen=True)
class LoadoutSnapshot:
    """The whole ship as of the last `Loadout` event. `ship` is the internal hull symbol
    ("corsair"); `ship_name`/`ship_ident` are the Commander's own labels."""
    ship: str | None = None
    ship_name: str | None = None
    ship_ident: str | None = None
    max_jump_range: float | None = None
    cargo_capacity: int | None = None
    fuel_capacity: float | None = None
    timestamp: str | None = None
    modules: tuple[ShipModule, ...] = field(default_factory=tuple)

    def engineered_modules(self) -> tuple[ShipModule, ...]:
        return tuple(m for m in self.modules if m.engineered)


def _f(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _i(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _parse_modifier(raw: dict) -> Modifier | None:
    label = str(raw.get("Label") or "").strip()
    if not label:
        return None
    return Modifier(
        label=label,
        value=_f(raw.get("Value")),
        original=_f(raw.get("OriginalValue")),
        less_is_good=bool(raw.get("LessIsGood")),
    )


def _parse_engineering(raw) -> Engineering | None:
    if not isinstance(raw, dict):
        return None
    blueprint = str(raw.get("BlueprintName") or "").strip()
    if not blueprint:
        return None
    modifiers = tuple(
        m for m in (_parse_modifier(r) for r in raw.get("Modifiers") or []
                    if isinstance(r, dict))
        if m is not None
    )
    return Engineering(
        blueprint=blueprint,
        level=_i(raw.get("Level")),
        quality=_f(raw.get("Quality")),
        engineer=str(raw.get("Engineer")).strip() if raw.get("Engineer") else None,
        experimental=str(raw.get("ExperimentalEffect")).strip()
        if raw.get("ExperimentalEffect") else None,
        experimental_localised=str(raw.get("ExperimentalEffect_Localised")).strip()
        if raw.get("ExperimentalEffect_Localised") else None,
        modifiers=modifiers,
    )


def _parse_module(raw: dict) -> ShipModule | None:
    slot = str(raw.get("Slot") or "").strip()
    item = str(raw.get("Item") or "").strip()
    if not slot or not item:
        return None
    return ShipModule(
        slot=slot,
        item=item,
        on=bool(raw.get("On", True)),
        priority=_i(raw.get("Priority")),
        health=_f(raw.get("Health")),
        engineering=_parse_engineering(raw.get("Engineering")),
    )


def parse_loadout(event: dict) -> LoadoutSnapshot:
    """A `Loadout` journal event -> structured snapshot. Tolerant of missing fields (a
    module without Slot/Item is dropped, a malformed Engineering block becomes None) —
    the watcher must never choke on a journal quirk."""
    fuel = event.get("FuelCapacity")
    if isinstance(fuel, dict):
        fuel_capacity = _f(fuel.get("Main"))
    else:
        fuel_capacity = _f(fuel)
    ship_name = str(event.get("ShipName") or "").strip() or None
    modules = tuple(
        m for m in (_parse_module(r) for r in event.get("Modules") or []
                    if isinstance(r, dict))
        if m is not None
    )
    return LoadoutSnapshot(
        ship=str(event.get("Ship") or "").strip() or None,
        ship_name=ship_name,
        ship_ident=str(event.get("ShipIdent") or "").strip() or None,
        max_jump_range=_f(event.get("MaxJumpRange")),
        cargo_capacity=_i(event.get("CargoCapacity")),
        fuel_capacity=fuel_capacity,
        timestamp=str(event.get("timestamp")) if event.get("timestamp") else None,
        modules=modules,
    )
