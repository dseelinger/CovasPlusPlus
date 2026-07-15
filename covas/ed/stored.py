"""Stored ships & modules snapshots — the journal `StoredShips` / `StoredModules` events,
structured (issue #67).

ED writes a `StoredShips` and a `StoredModules` event whenever you dock somewhere with a
shipyard / outfitting (and on some menu opens). Each is a COMPLETE inventory of everything
you have parked or shelved away from your current hull, so parsing replaces the previous
snapshot wholesale. This is the pure capture side: raw journal in, typed frozen dataclasses
out — the parsers never invent or rename anything (symbol → spoken name happens at speak
time in the capability, via `ed/module_names.py`).

**Transfer time & cost.** Frontier writes the EXACT transfer price and time into every
*remote* entry itself — `TransferPrice`/`TransferTime` for ships, `TransferCost`/
`TransferTime` for modules — computed by the game from the distance between where you're
docked and where the ship/module sits (time grows with distance; cost with distance and the
item's value). We surface those journal-provided numbers VERBATIM rather than re-deriving
them: it's your-state data the game already holds, so it's always accurate *as of your last
shipyard/outfitting dock* (the station the snapshot was written at). If you've since moved,
the figures are the game's last computed values for that origin — the capability says so. No
CAPI, no network, ever.

Stored on `EDContext` (see `set_stored_ships` / `set_stored_modules`) by the journal watcher;
read by the StoredCapability's tools. Local journal data only.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StoredShip:
    """One ship in storage. `ship_type` is the raw journal symbol ("cutter",
    "federation_corvette"); `ship_type_localised` is the game's own display string when
    present ("Imperial Cutter") — preferred for speech. `here` = parked at the station the
    snapshot was taken at (no transfer needed); otherwise `system` is where it sits and
    `transfer_price`/`transfer_time` are the game's own quoted transfer figures."""
    ship_type: str
    ship_type_localised: str | None = None
    name: str | None = None                 # the Commander's custom ship name, if any
    ship_id: int | None = None
    value: int | None = None                # market value, credits
    hot: bool = False                       # heat/notoriety flag
    here: bool = True                       # at the snapshot's station
    system: str | None = None               # where it sits (None when `here`)
    station_market_id: int | None = None    # the ShipMarketID it's stored at (remote)
    transfer_price: int | None = None       # credits to transfer it here (remote)
    transfer_time: int | None = None        # seconds to transfer it here (remote)
    in_transit: bool = False                # already on its way somewhere

    @property
    def display(self) -> str:
        """The best spoken hull name: the game's localised string, else the prettified
        symbol. NEVER the raw internal symbol."""
        loc = (self.ship_type_localised or "").strip()
        if loc:
            return loc
        return _prettify_ship(self.ship_type)


@dataclass(frozen=True)
class StoredModule:
    """One shelved module. `name` is the raw journal Item symbol, normalized to the
    Loadout form ("int_powerplant_size5_class5") so `module_names.module_name` can speak it;
    `name_localised` is the game's own display string, kept as a fallback. `here`,
    `transfer_cost`/`transfer_time`, and `in_transit` mirror StoredShip."""
    name: str
    name_localised: str | None = None
    slot: int | None = None                 # StorageSlot
    here: bool = True
    system: str | None = None
    market_id: int | None = None
    transfer_cost: int | None = None        # credits (remote)
    transfer_time: int | None = None        # seconds (remote)
    hot: bool = False
    in_transit: bool = False
    engineer_modifications: str | None = None   # blueprint symbol, if engineered
    level: int | None = None                # engineering grade, if engineered
    value: int | None = None                # BuyPrice, credits


@dataclass(frozen=True)
class StoredShipsSnapshot:
    """The whole stored-ships inventory as of the last `StoredShips` event. `station`/
    `system` are where the Commander was docked when it was written — the origin the remote
    transfer figures are measured from."""
    station: str | None = None
    system: str | None = None
    market_id: int | None = None
    timestamp: str | None = None
    ships: tuple[StoredShip, ...] = field(default_factory=tuple)

    def remote(self) -> tuple[StoredShip, ...]:
        return tuple(s for s in self.ships if not s.here)

    def here_ships(self) -> tuple[StoredShip, ...]:
        return tuple(s for s in self.ships if s.here)


@dataclass(frozen=True)
class StoredModulesSnapshot:
    """The whole stored-modules inventory as of the last `StoredModules` event."""
    station: str | None = None
    system: str | None = None
    market_id: int | None = None
    timestamp: str | None = None
    modules: tuple[StoredModule, ...] = field(default_factory=tuple)


# ---- primitives ----------------------------------------------------------------------------

def _i(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _s(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _prettify_ship(symbol: str) -> str:
    """A readable hull name from a raw journal ShipType symbol, for the rare case the game
    didn't give a localised string. Curated where a naive title-case reads wrong ('type9' ->
    'Type-9', 'cutter' -> 'Imperial Cutter'); underscore/title-case otherwise."""
    sym = str(symbol or "").strip().lower()
    if not sym:
        return "an unknown ship"
    hit = _SHIP_SYMBOLS.get(sym)
    if hit:
        return hit
    return sym.replace("_", " ").title()


# Journal ShipType symbols whose plain title-case is wrong or bare. ShipType_Localised is
# usually present, so this is only a fallback; kept small and only for the awkward cases.
_SHIP_SYMBOLS: dict[str, str] = {
    "cutter": "Imperial Cutter",
    "clipper": "Imperial Clipper",
    "empire_courier": "Imperial Courier",
    "empire_eagle": "Imperial Eagle",
    "empire_trader": "Imperial Clipper",
    "federation_corvette": "Federal Corvette",
    "federation_dropship": "Federal Dropship",
    "federation_dropship_mkii": "Federal Assault Ship",
    "federation_gunship": "Federal Gunship",
    "typex": "Alliance Chieftain",
    "typex_2": "Alliance Crusader",
    "typex_3": "Alliance Challenger",
    "type6": "Type-6 Transporter",
    "type7": "Type-7 Transporter",
    "type8": "Type-8 Transporter",
    "type9": "Type-9 Heavy",
    "type9_military": "Type-10 Defender",
    "belugaliner": "Beluga Liner",
    "cobramkiii": "Cobra MkIII",
    "cobramkiv": "Cobra MkIV",
    "cobramkv": "Cobra MkV",
    "krait_mkii": "Krait MkII",
    "krait_light": "Krait Phantom",
    "viper": "Viper MkIII",
    "viper_mkiv": "Viper MkIV",
    "diamondback": "Diamondback Scout",
    "diamondbackxl": "Diamondback Explorer",
    "asp": "Asp Explorer",
    "asp_scout": "Asp Scout",
    "independant_trader": "Keelback",
    "sidewinder": "Sidewinder",
}


def _norm_module_symbol(name: str) -> str:
    """Normalize a StoredModules `Name` ("$int_powerplant_size5_class5_name;") to the Loadout
    `Item` form ("int_powerplant_size5_class5") that `module_names.module_name` understands.
    Tolerant: a name already in that form passes through unchanged."""
    sym = str(name or "").strip().lower()
    if sym.startswith("$"):
        sym = sym[1:]
    if sym.endswith(";"):
        sym = sym[:-1]
    if sym.endswith("_name"):
        sym = sym[:-len("_name")]
    return sym


# ---- StoredShips -------------------------------------------------------------------------

def _parse_ship_here(raw: dict) -> StoredShip | None:
    sym = _s(raw.get("ShipType"))
    if not sym:
        return None
    return StoredShip(
        ship_type=sym.lower(),
        ship_type_localised=_s(raw.get("ShipType_Localised")),
        name=_s(raw.get("Name")),
        ship_id=_i(raw.get("ShipID")),
        value=_i(raw.get("Value")),
        hot=bool(raw.get("Hot")),
        here=True,
    )


def _parse_ship_remote(raw: dict) -> StoredShip | None:
    sym = _s(raw.get("ShipType"))
    if not sym:
        return None
    in_transit = bool(raw.get("InTransit"))
    return StoredShip(
        ship_type=sym.lower(),
        ship_type_localised=_s(raw.get("ShipType_Localised")),
        name=_s(raw.get("Name")),
        ship_id=_i(raw.get("ShipID")),
        value=_i(raw.get("Value")),
        hot=bool(raw.get("Hot")),
        here=False,
        system=_s(raw.get("StarSystem")),
        station_market_id=_i(raw.get("ShipMarketID")),
        transfer_price=_i(raw.get("TransferPrice")),
        transfer_time=_i(raw.get("TransferTime")),
        in_transit=in_transit,
    )


def parse_stored_ships(event: dict) -> StoredShipsSnapshot:
    """A `StoredShips` journal event -> structured snapshot. Tolerant of missing fields (an
    entry without a ShipType is dropped) — the watcher must never choke on a journal quirk."""
    here = tuple(
        s for s in (_parse_ship_here(r) for r in event.get("ShipsHere") or []
                    if isinstance(r, dict))
        if s is not None
    )
    remote = tuple(
        s for s in (_parse_ship_remote(r) for r in event.get("ShipsRemote") or []
                    if isinstance(r, dict))
        if s is not None
    )
    return StoredShipsSnapshot(
        station=_s(event.get("StationName")),
        system=_s(event.get("StarSystem")),
        market_id=_i(event.get("MarketID")),
        timestamp=_s(event.get("timestamp")),
        ships=here + remote,
    )


# ---- StoredModules -----------------------------------------------------------------------

def _parse_stored_module(raw: dict, snap_system: str | None,
                         snap_market_id: int | None) -> StoredModule | None:
    name = _s(raw.get("Name"))
    if not name:
        return None
    in_transit = bool(raw.get("InTransit"))
    system = _s(raw.get("StarSystem"))
    market_id = _i(raw.get("MarketID"))
    transfer_cost = _i(raw.get("TransferCost"))
    transfer_time = _i(raw.get("TransferTime"))
    # An item is "here" when it isn't in transit and carries no transfer quote and no
    # elsewhere-location — Frontier omits StarSystem/MarketID/TransferCost for items parked at
    # the current station. Belt-and-braces: also treat a same-system/same-station entry as here.
    same_place = (
        (system is not None and snap_system is not None
         and system.strip().lower() == snap_system.strip().lower())
        or (market_id is not None and snap_market_id is not None
            and market_id == snap_market_id)
    )
    here = (not in_transit
            and transfer_cost is None and transfer_time is None
            and (system is None or same_place))
    return StoredModule(
        name=_norm_module_symbol(name),
        name_localised=_s(raw.get("Name_Localised")),
        slot=_i(raw.get("StorageSlot")),
        here=here,
        system=system,
        market_id=market_id,
        transfer_cost=transfer_cost,
        transfer_time=transfer_time,
        hot=bool(raw.get("Hot")),
        in_transit=in_transit,
        engineer_modifications=_s(raw.get("EngineerModifications")),
        level=_i(raw.get("Level")),
        value=_i(raw.get("BuyPrice")),
    )


def parse_stored_modules(event: dict) -> StoredModulesSnapshot:
    """A `StoredModules` journal event -> structured snapshot. Tolerant of missing fields."""
    system = _s(event.get("StarSystem"))
    market_id = _i(event.get("MarketID"))
    modules = tuple(
        m for m in (_parse_stored_module(r, system, market_id)
                    for r in event.get("Items") or [] if isinstance(r, dict))
        if m is not None
    )
    return StoredModulesSnapshot(
        station=_s(event.get("StationName")),
        system=system,
        market_id=market_id,
        timestamp=_s(event.get("timestamp")),
        modules=modules,
    )
