"""Owned-ships registry (issue #134) — the persisted identity of the ships the Commander OWNS.

Elite writes no single "here is your fleet" snapshot: `StoredShips` lists only the ships
currently in *storage* (never the one you're flying), `Loadout` describes only the ACTIVE ship,
and the *ownership-change* events (`ShipyardBuy` / `ShipyardNew` / `ShipyardSell` /
`ShipyardSwap`) are one-shot deltas that may live in journals from months ago. So — exactly like
the NPC-crew seen-set (`ed/npc_crew.py`) — we persist a small git-ignored registry, keyed by the
journal **ShipID** (the stable per-hull id), that survives restarts and accumulates the fleet as
events and snapshots arrive.

This is the IDENTITY SPINE downstream per-ship config (engineering memory, #135/#139) keys on, so
the record shape is deliberately explicit. One record per owned ship:

    {ship_id (str key): {
        "ship_type": "python",          # RAW journal ShipType symbol (stable, non-localised key)
        "name":  "Void Runner" | None,  # the Commander's custom ship name (Loadout ShipName)
        "ident": "VR-01"      | None,   # the Commander's ship ident (Loadout ShipIdent)
        "system":  "Sol"      | None,   # last-known star system
        "station": "Abraham Lincoln" | None,  # last-known station
        "active":  bool,                # True for the ship currently being flown (exactly one)
        "manual":  bool,                # True once a human added/edited it — protects corrections
        "last_seen": "2026-..Z" | None, # source event/snapshot timestamp (deterministic, not wall)
    }}

Everything here is PURE + fail-soft + total, mirroring `npc_crew.fold`:

  * `fold()` folds ONE ownership-change event (raw dict in, updated map out, never raises).
  * `reconcile_loadout()` / `reconcile_stored()` fold the two SNAPSHOT events in — they only
    add/update, they NEVER remove (a stored-ships snapshot that predates a manual add mustn't
    delete it), and they NEVER overwrite a `manual` record's name/ident (a hand-typed correction
    survives the next journal event). Locations + the active flag are facts, so those update.
  * Removal happens ONLY on `ShipyardSell` / a part-exchange `ShipyardBuy` / an explicit manual
    `remove()`.

The on-disk store mirrors `npc_crew.NpcCrewRegistry`: `load()` fails soft to empty on a
missing/corrupt file, `save()` is atomic temp-then-replace and swallows I/O errors, so a bad file
can never wedge the single-writer journal thread.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from .stored import _prettify_ship  # readable hull name from a raw ShipType symbol (fallback)

# The four ownership-change events. Kept as a module constant so the journal dispatch is a cheap
# membership test and this module owns the authoritative list.
SHIPYARD_EVENTS = frozenset({"ShipyardBuy", "ShipyardNew", "ShipyardSell", "ShipyardSwap"})


def _sid(value: object) -> Optional[str]:
    """A ShipID normalised to a string key (JSON object keys are strings), or None. Accepts the
    raw int the journal writes; rejects bools / non-numerics."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return str(int(value))
    return None


def _str(value: object) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _set_active(entries: dict, ship_id: str) -> None:
    """Mark `ship_id` the active ship and every other owned ship inactive (exactly one active)."""
    for k, rec in entries.items():
        rec["active"] = (k == ship_id)


def fold(entries: dict, event: dict) -> dict:
    """Fold one ownership-change journal event into the `{ship_id: record}` map, returning the
    (possibly identical) updated map. PURE + total: an unrecognised event, or one missing the id
    it needs, is a no-op. Mutates and returns `entries` for the caller's convenience.

      * `ShipyardNew  {ShipType, NewShipID}` — a freshly bought hull; adds the record and makes
        it the active ship (you're placed into it). `ShipyardBuy` fires just before but carries
        no new id, so `New` is where the owned record is born.
      * `ShipyardBuy  {ShipType, SellShipType?, SellShipID?}` — signals INTENT + any part-exchange:
        when a `SellShipID` is present that traded-in ship is removed. Adds nothing (no new id).
      * `ShipyardSell {SellShipID}` — removes the sold ship.
      * `ShipyardSwap {ShipType, ShipID}` — switching active ship; marks `ShipID` active (adding a
        bare record for it if we'd never seen it — you can only swap into a ship you own).
    """
    if not isinstance(event, dict):
        return entries
    name = event.get("event", "")
    if name not in SHIPYARD_EVENTS:
        return entries
    ts = _str(event.get("timestamp"))

    if name == "ShipyardSell":
        sid = _sid(event.get("SellShipID"))
        if sid:
            entries.pop(sid, None)
        return entries

    if name == "ShipyardBuy":
        # A part-exchange trades in the old ship; the new hull's record is born on ShipyardNew.
        sid = _sid(event.get("SellShipID"))
        if sid:
            entries.pop(sid, None)
        return entries

    if name == "ShipyardNew":
        sid = _sid(event.get("NewShipID"))
        ship_type = _str(event.get("ShipType"))
        if not sid or not ship_type:
            return entries
        rec = dict(entries.get(sid) or {})
        rec.setdefault("name", None)
        rec.setdefault("ident", None)
        rec.setdefault("system", None)
        rec.setdefault("station", None)
        rec.setdefault("manual", False)
        rec["ship_type"] = ship_type.lower()
        if ts:
            rec["last_seen"] = ts
        entries[sid] = rec
        _set_active(entries, sid)   # a newly bought ship is the one you're now flying
        return entries

    if name == "ShipyardSwap":
        sid = _sid(event.get("ShipID"))
        if not sid:
            return entries
        rec = dict(entries.get(sid) or {})
        ship_type = _str(event.get("ShipType"))
        if ship_type:
            rec["ship_type"] = ship_type.lower()
        elif not rec.get("ship_type"):
            return entries  # never seen it and no type given — nothing worth recording
        rec.setdefault("name", None)
        rec.setdefault("ident", None)
        rec.setdefault("system", None)
        rec.setdefault("station", None)
        rec.setdefault("manual", False)
        if ts:
            rec["last_seen"] = ts
        entries[sid] = rec
        _set_active(entries, sid)
        return entries

    return entries


def reconcile_loadout(entries: dict, snapshot) -> dict:
    """Fold a `LoadoutSnapshot` (the ACTIVE ship) into the map: ensure the ship is owned, mark it
    active, and fill in its type / name / ident. PURE + total — a None/typeless snapshot is a
    no-op. Never removes anything. A `manual` record keeps its hand-typed name/ident (a
    correction survives), but its active flag still updates (that's a fact)."""
    if snapshot is None:
        return entries
    sid = _sid(getattr(snapshot, "ship_id", None))
    if not sid:
        return entries
    ship_type = _str(getattr(snapshot, "ship", None))
    rec = dict(entries.get(sid) or {})
    if ship_type:
        rec["ship_type"] = ship_type.lower()
    elif not rec.get("ship_type"):
        return entries
    manual = bool(rec.get("manual"))
    name = _str(getattr(snapshot, "ship_name", None))
    ident = _str(getattr(snapshot, "ship_ident", None))
    # Don't clobber a hand-typed correction; otherwise adopt the game's own labels.
    if name is not None and not (manual and rec.get("name")):
        rec["name"] = name
    else:
        rec.setdefault("name", None)
    if ident is not None and not (manual and rec.get("ident")):
        rec["ident"] = ident
    else:
        rec.setdefault("ident", None)
    rec.setdefault("system", None)
    rec.setdefault("station", None)
    rec["manual"] = manual
    ts = _str(getattr(snapshot, "timestamp", None))
    if ts:
        rec["last_seen"] = ts
    entries[sid] = rec
    _set_active(entries, sid)
    return entries


def reconcile_stored(entries: dict, snapshot) -> dict:
    """Fold a `StoredShipsSnapshot` into the map: every stored ship it lists is owned, so upsert
    a record for each and refresh its last-known location. PURE + total. NEVER removes (a stored
    snapshot lists only what's in storage and can predate a manual add), and a `manual` record's
    name is preserved. Stored ships are, by definition, NOT the active ship — but we do not clear
    the active flag here, because the active ship comes from Loadout and a stored-snapshot omitting
    it says nothing about it."""
    if snapshot is None:
        return entries
    snap_station = _str(getattr(snapshot, "station", None))
    snap_system = _str(getattr(snapshot, "system", None))
    for ship in getattr(snapshot, "ships", ()) or ():
        sid = _sid(getattr(ship, "ship_id", None))
        ship_type = _str(getattr(ship, "ship_type", None))
        if not sid or not ship_type:
            continue
        rec = dict(entries.get(sid) or {})
        rec["ship_type"] = ship_type.lower()
        manual = bool(rec.get("manual"))
        name = _str(getattr(ship, "name", None))
        if name is not None and not (manual and rec.get("name")):
            rec["name"] = name
        else:
            rec.setdefault("name", None)
        rec.setdefault("ident", None)
        # Location: a ship parked "here" sits at the snapshot's station/system; a remote one names
        # its own system (no station in the stored entry).
        if getattr(ship, "here", False):
            if snap_system:
                rec["system"] = snap_system
            if snap_station:
                rec["station"] = snap_station
        else:
            remote_system = _str(getattr(ship, "system", None))
            if remote_system:
                rec["system"] = remote_system
                rec["station"] = None
        rec.setdefault("active", False)
        rec["manual"] = manual
        ts = _str(getattr(snapshot, "timestamp", None))
        if ts:
            rec["last_seen"] = ts
        entries[sid] = rec
    return entries


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().replace("_", " ").split())


def display_name(rec: dict) -> str:
    """A spoken label for one owned ship: the hull name, plus the custom ship name when set."""
    hull = _prettify_ship(str(rec.get("ship_type") or ""))
    name = str(rec.get("name") or "").strip()
    if name and name.lower() != hull.lower():
        return f'{hull} "{name}"'
    return hull


def match_ships(entries: dict, query: str) -> list[tuple[str, dict]]:
    """`(ship_id, record)` pairs whose hull symbol, prettified hull name, custom name, or ident
    matches the spoken `query` (loose substring both ways). Empty query -> no matches. Pure."""
    q = _norm(query)
    if not q:
        return []
    hits: list[tuple[str, dict]] = []
    for sid, rec in entries.items():
        names = (
            _norm(rec.get("ship_type")),
            _norm(_prettify_ship(str(rec.get("ship_type") or ""))),
            _norm(rec.get("name")),
            _norm(rec.get("ident")),
        )
        if any(n and (q in n or n in q) for n in names):
            hits.append((sid, rec))
    return hits


class OwnedShipsRegistry:
    """The persisted owned-ships store: `{ship_id: record}` backed by a JSON file. Single-writer
    (the journal thread) so no internal lock — the EDContext accessor holds it under its own lock.
    Fail-soft throughout, mirroring `npc_crew.NpcCrewRegistry`."""

    def __init__(self, entries: Optional[dict] = None, path: Optional[Path | str] = None) -> None:
        self._entries: dict = dict(entries or {})
        self._path: Optional[Path] = Path(path) if path else None

    @classmethod
    def load(cls, path: Optional[Path | str]) -> "OwnedShipsRegistry":
        """Read the registry from disk, fail-soft. A missing/corrupt/non-dict file yields an EMPTY
        registry (never raises) so a bad file can't wedge the journal watcher."""
        p = Path(path) if path else None
        entries: dict = {}
        if p is not None and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _warn(f"could not read owned-ships registry {p} ({e}); starting empty")
            else:
                if isinstance(data, dict):
                    # Keep only well-formed {ship_id: {ship_type: ...}} rows.
                    entries = {str(k): _clean(v) for k, v in data.items()
                               if isinstance(v, dict) and str(v.get("ship_type", "")).strip()}
                else:
                    _warn(f"owned-ships registry {p} is not a JSON object; starting empty")
        return cls(entries=entries, path=p)

    # -- journal folds ------------------------------------------------------------------
    def apply_event(self, event: dict) -> bool:
        """Fold one ownership-change event into the registry and persist if anything changed.
        Returns True on a change. Fail-soft — persistence errors are swallowed."""
        return self._mutate(lambda e: fold(e, event))

    def reconcile_loadout(self, snapshot) -> bool:
        """Reconcile the active ship from a `LoadoutSnapshot`; persist on change."""
        return self._mutate(lambda e: reconcile_loadout(e, snapshot))

    def reconcile_stored(self, snapshot) -> bool:
        """Reconcile stored-ship locations from a `StoredShipsSnapshot`; persist on change."""
        return self._mutate(lambda e: reconcile_stored(e, snapshot))

    # -- manual CRUD --------------------------------------------------------------------
    def add(self, ship_type: str, *, name: str | None = None, ident: str | None = None,
            ship_id: object = None, timestamp: str | None = None) -> Optional[dict]:
        """Manually add an owned ship (a correction, or a pre-existing ship no event was captured
        for). Marked `manual` so a later journal reconcile won't clobber its name/ident. When no
        `ship_id` is given a synthetic NEGATIVE key is minted (real ShipIDs are non-negative, so
        they can't collide). Returns the stored record (carrying its `ship_id`), or None when
        `ship_type` is blank."""
        st = _str(ship_type)
        if not st:
            return None
        sid = _sid(ship_id)
        if sid is None:
            sid = str(_next_synthetic_id(self._entries))
        rec = dict(self._entries.get(sid) or {})
        rec["ship_type"] = st.lower()
        new_name = _str(name)
        new_ident = _str(ident)
        if new_name is not None:
            rec["name"] = new_name
        if new_ident is not None:
            rec["ident"] = new_ident
        rec.setdefault("name", None)
        rec.setdefault("ident", None)
        rec.setdefault("system", None)
        rec.setdefault("station", None)
        rec.setdefault("active", False)
        rec["manual"] = True
        rec["last_seen"] = _str(timestamp)
        self._entries[sid] = _clean(rec)
        self.save()
        return dict(self._entries[sid], ship_id=sid)

    def remove(self, ship_id: object) -> bool:
        """Remove the owned ship with this ShipID (accepts int or str). Returns True if removed."""
        sid = _sid(ship_id)
        if sid is None or sid not in self._entries:
            return False
        self._entries.pop(sid, None)
        self.save()
        return True

    def remove_matching(self, query: str) -> tuple[bool, list[tuple[str, dict]]]:
        """Remove the single owned ship matching a spoken `query`. Returns `(removed, matches)`:
        removed True only on exactly one match (an ambiguous or empty match removes nothing and
        hands the matches back so the caller can disambiguate)."""
        matches = match_ships(self._entries, query)
        if len(matches) == 1:
            sid = matches[0][0]
            self._entries.pop(sid, None)
            self.save()
            return True, matches
        return False, matches

    # -- accessors ----------------------------------------------------------------------
    def owned(self) -> list[dict]:
        """The owned ships as a list of records (each carries its `ship_id`), active ship first
        then by most-recently-seen. Safe to hand out (copies)."""
        rows = [dict(rec, ship_id=sid) for sid, rec in self._entries.items()]
        rows.sort(key=lambda r: str(r.get("last_seen") or ""), reverse=True)
        rows.sort(key=lambda r: not r.get("active"))  # active ship first
        return rows

    def entries(self) -> dict:
        """A shallow copy of the raw `{ship_id: record}` map (safe to snapshot)."""
        return {k: dict(v) for k, v in self._entries.items()}

    def active(self) -> Optional[dict]:
        """The active ship's record (with `ship_id`), or None when none is flagged."""
        for sid, rec in self._entries.items():
            if rec.get("active"):
                return dict(rec, ship_id=sid)
        return None

    # -- persistence --------------------------------------------------------------------
    def _mutate(self, op) -> bool:
        """Apply a pure `entries -> entries` op and persist if it changed anything."""
        before = json.dumps(self._entries, sort_keys=True, ensure_ascii=False)
        op(self._entries)
        after = json.dumps(self._entries, sort_keys=True, ensure_ascii=False)
        if before == after:
            return False
        self.save()
        return True

    def save(self) -> None:
        """Persist the whole registry atomically (temp-then-replace), fail-soft — mirrors
        `npc_crew.save`. A no-op when no path is configured."""
        if self._path is None:
            return
        p = self._path
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(self._entries, ensure_ascii=False, indent=2)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(body + "\n", encoding="utf-8")
            tmp.replace(p)  # atomic on the same filesystem
        except OSError as e:
            _warn(f"could not save owned-ships registry {p} ({e})")


# ---- primitives --------------------------------------------------------------------------

def _clean(rec: dict) -> dict:
    """Normalise a record to the canonical key set with sane defaults, so a hand-edited or
    older-format file loads without surprising the readers."""
    return {
        "ship_type": str(rec.get("ship_type") or "").lower(),
        "name": _str(rec.get("name")),
        "ident": _str(rec.get("ident")),
        "system": _str(rec.get("system")),
        "station": _str(rec.get("station")),
        "active": bool(rec.get("active")),
        "manual": bool(rec.get("manual")),
        "last_seen": _str(rec.get("last_seen")),
    }


def _next_synthetic_id(entries: dict) -> int:
    """A fresh NEGATIVE synthetic ShipID for a manual add (real ShipIDs are non-negative). One
    below the smallest negative key in use, starting at -1."""
    lowest = 0
    for k in entries:
        try:
            n = int(k)
        except (TypeError, ValueError):
            continue
        if n < lowest:
            lowest = n
    return lowest - 1


def _warn(msg: str) -> None:
    """Fail-soft diagnostic to stderr (matches npc_crew / app.py) — never an exception upward."""
    print(f"!! [owned_ships] {msg}", file=sys.stderr, flush=True)
