"""Local Shipyard.json — the game's OWN record of what the last-visited shipyard stocks.

Elite writes `Shipyard.json` (next to the journals) when the Commander opens a station's
shipyard; its `PriceList` names exactly the hulls purchasable with credits RIGHT NOW. That
makes it the one GROUND-TRUTH stock source we have: Spansh's per-station `ships` array is the
station's CATALOG, not its stock (verified live 2026-07 — a minutes-fresh Spansh record listed
18 ships at a station whose own Shipyard.json stocked exactly one), so no amount of data
freshness makes a Spansh listing prove a hull is buyable. The ship search uses this snapshot
to VETO a recommended station the Commander has recently seen for themselves
(`nav/ship_search.py`), skipping to the next-nearest instead of sending them to a shipyard
they just watched say "UNAVAILABLE".

Read fail-soft and per-lookup (the file is tiny): a missing/half-written/foreign file is just
`None`, never an error into the voice loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ShipyardSnapshot:
    """One station's in-stock hulls, as the game last reported them. `symbols` holds the
    journal's lowercase ShipType symbols ("corsair", "type8", "federation_corvette") — the
    same identifiers as the roster's Spansh ed_symbols, just lowercased."""
    station: str
    system: str
    market_id: int | None
    timestamp: datetime | None
    symbols: frozenset[str]

    def is_station(self, station, system) -> bool:
        """Is this snapshot about the given station (case-insensitive name + system)?"""
        return (self.station.lower() == str(station or "").strip().lower()
                and self.system.lower() == str(system or "").strip().lower())

    def stocks_symbol(self, symbol) -> bool:
        """Is a hull (by ed_symbol, any case) in the snapshot's purchasable price list?"""
        return str(symbol or "").strip().lower() in self.symbols

    def age_days(self, now: datetime | None = None) -> float | None:
        """Snapshot age in days, or None when it carries no timestamp (treat as untrusted)."""
        if self.timestamp is None:
            return None
        now = now if now is not None else datetime.now(timezone.utc)
        return max(0.0, (now - self.timestamp).total_seconds() / 86400.0)


def _parse_timestamp(raw) -> datetime | None:
    """The journal's "2026-07-11T15:54:39Z" as an aware datetime, or None."""
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def read_shipyard_snapshot(path: str | Path) -> ShipyardSnapshot | None:
    """Parse `Shipyard.json` into a `ShipyardSnapshot`, or None on anything unusable —
    missing file, half-written JSON (ED rewrites it live), a non-Shipyard event, or a body
    without a station. An EMPTY PriceList is a valid snapshot (a shipyard stocking nothing),
    not a failure."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
        body = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("event") != "Shipyard":
        return None
    station = str(body.get("StationName") or "").strip()
    system = str(body.get("StarSystem") or "").strip()
    if not station or not system:
        return None
    market_id = body.get("MarketID")
    symbols = frozenset(
        str(e.get("ShipType") or "").strip().lower()
        for e in (body.get("PriceList") or [])
        if isinstance(e, dict) and e.get("ShipType")
    )
    return ShipyardSnapshot(
        station=station,
        system=system,
        market_id=int(market_id) if isinstance(market_id, (int, float)) else None,
        timestamp=_parse_timestamp(body.get("timestamp")),
        symbols=symbols,
    )
