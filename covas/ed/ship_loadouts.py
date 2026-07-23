"""Per-ship loadout + engineering memory (issue #135) — remembers HOW EACH OWNED SHIP is built.

`EDContext._loadout` (N9) keeps only the CURRENT ship's `LoadoutSnapshot`, replaced wholesale on
every journal `Loadout` event — so switching ships loses the prior ship's build. This module is the
persistence that fixes that: a small git-ignored store, keyed by the journal **ShipID** (the same
IDENTITY SPINE the owned-ships registry #134 keys on), holding a serialized `LoadoutSnapshot` per
ship. Board ship A, board ship B, restart — A's modules + applied engineering are still remembered.

Everything here is PURE + fail-soft + total, mirroring `owned_ships.OwnedShipsRegistry`:

  * `snapshot_to_dict` / `snapshot_from_dict` serialize a `LoadoutSnapshot` (with its nested
    `ShipModule` / `Engineering` / `Modifier` frozen dataclasses) to/from a plain JSON-able dict.
    `from_dict` is total — a missing/garbled field degrades (a bad module is dropped, a bad
    engineering block becomes None) rather than raising, so a hand-edited or older-format file loads.
  * `ShipLoadoutStore.capture()` upserts ONE snapshot under its ShipID (a snapshot with no ShipID is
    ignored — it can't be keyed), persisting only when the stored build actually changed.
  * `get()` hands back a rebuilt `LoadoutSnapshot` (or None) for a ShipID — the grounded per-ship
    build the engineering-planning capability reasons over.

The on-disk store mirrors the sibling registries: `load()` fails soft to empty on a missing/corrupt
file, `save()` is atomic temp-then-replace and swallows I/O errors, so a bad file can never wedge
the single-writer journal thread. Single-writer (the journal thread) so no internal lock — the
EDContext accessor holds it under its own lock.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from .loadout import Engineering, LoadoutSnapshot, Modifier, ShipModule


def _sid(value: object) -> str | None:
    """A ShipID normalised to a string key (JSON object keys are strings), or None. Accepts the
    raw int the journal writes; rejects bools / non-numerics — mirrors owned_ships._sid so the two
    stores key on the SAME identity."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return str(int(value))
    return None


# ---- serialization -----------------------------------------------------------------------

def _modifier_to_dict(m: Modifier) -> dict:
    return {"label": m.label, "value": m.value, "original": m.original,
            "less_is_good": m.less_is_good}


def _engineering_to_dict(e: Engineering) -> dict:
    return {
        "blueprint": e.blueprint,
        "level": e.level,
        "quality": e.quality,
        "engineer": e.engineer,
        "experimental": e.experimental,
        "experimental_localised": e.experimental_localised,
        "modifiers": [_modifier_to_dict(m) for m in e.modifiers],
    }


def _module_to_dict(m: ShipModule) -> dict:
    d = {"slot": m.slot, "item": m.item, "on": m.on, "priority": m.priority, "health": m.health}
    if m.engineering is not None:
        d["engineering"] = _engineering_to_dict(m.engineering)
    return d


def snapshot_to_dict(snap: LoadoutSnapshot) -> dict:
    """A JSON-able dict for a `LoadoutSnapshot` — the whole ship, modules + engineering included."""
    return {
        "ship": snap.ship,
        "ship_name": snap.ship_name,
        "ship_ident": snap.ship_ident,
        "ship_id": snap.ship_id,
        "max_jump_range": snap.max_jump_range,
        "cargo_capacity": snap.cargo_capacity,
        "fuel_capacity": snap.fuel_capacity,
        "timestamp": snap.timestamp,
        "modules": [_module_to_dict(m) for m in snap.modules],
    }


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v: object) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _modifier_from_dict(raw: object) -> Modifier | None:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label") or "").strip()
    if not label:
        return None
    return Modifier(label=label, value=_num(raw.get("value")),
                    original=_num(raw.get("original")),
                    less_is_good=bool(raw.get("less_is_good")))


def _engineering_from_dict(raw: object) -> Engineering | None:
    if not isinstance(raw, dict):
        return None
    blueprint = str(raw.get("blueprint") or "").strip()
    if not blueprint:
        return None
    modifiers = tuple(m for m in (_modifier_from_dict(r) for r in raw.get("modifiers") or [])
                      if m is not None)
    return Engineering(
        blueprint=blueprint,
        level=_int(raw.get("level")),
        quality=_num(raw.get("quality")),
        engineer=str(raw.get("engineer")).strip() if raw.get("engineer") else None,
        experimental=str(raw.get("experimental")).strip() if raw.get("experimental") else None,
        experimental_localised=str(raw.get("experimental_localised")).strip()
        if raw.get("experimental_localised") else None,
        modifiers=modifiers,
    )


def _module_from_dict(raw: object) -> ShipModule | None:
    if not isinstance(raw, dict):
        return None
    slot = str(raw.get("slot") or "").strip()
    item = str(raw.get("item") or "").strip()
    if not slot or not item:
        return None
    return ShipModule(
        slot=slot, item=item,
        on=bool(raw.get("on", True)),
        priority=_int(raw.get("priority")),
        health=_num(raw.get("health")),
        engineering=_engineering_from_dict(raw.get("engineering")),
    )


def snapshot_from_dict(raw: object) -> LoadoutSnapshot | None:
    """Rebuild a `LoadoutSnapshot` from a serialized dict, TOTAL + fail-soft: a bad module is
    dropped and a garbled engineering block becomes None, so an older-format or hand-edited row
    still loads. Returns None only when `raw` isn't a dict."""
    if not isinstance(raw, dict):
        return None
    modules = tuple(m for m in (_module_from_dict(r) for r in raw.get("modules") or [])
                    if m is not None)
    return LoadoutSnapshot(
        ship=str(raw.get("ship") or "").strip() or None,
        ship_name=str(raw.get("ship_name") or "").strip() or None,
        ship_ident=str(raw.get("ship_ident") or "").strip() or None,
        ship_id=_int(raw.get("ship_id")),
        max_jump_range=_num(raw.get("max_jump_range")),
        cargo_capacity=_int(raw.get("cargo_capacity")),
        fuel_capacity=_num(raw.get("fuel_capacity")),
        timestamp=str(raw.get("timestamp")) if raw.get("timestamp") else None,
        modules=modules,
    )


class ShipLoadoutStore:
    """The persisted per-ship loadout memory: `{ship_id: serialized LoadoutSnapshot}` backed by a
    JSON file. State is guarded by the EDContext lock; the only internal lock is `_io_lock`, which
    serialises the DISK write so the journal thread can persist OUTSIDE the EDContext lock without a
    slow disk stalling readers (#161). Fail-soft throughout, mirroring `owned_ships.OwnedShipsRegistry`."""

    def __init__(self, loadouts: dict | None = None, path: Path | str | None = None) -> None:
        self._loadouts: dict = dict(loadouts or {})
        self._path: Path | None = Path(path) if path else None
        # Serialises DISK writes only (never held during a state mutation) so the journal thread can
        # persist OUTSIDE the EDContext lock without a slow disk stalling readers (#161).
        self._io_lock = threading.Lock()

    @classmethod
    def load(cls, path: Path | str | None) -> ShipLoadoutStore:
        """Read the store from disk, fail-soft. A missing/corrupt/non-dict file yields an EMPTY
        store (never raises) so a bad file can't wedge the journal watcher."""
        p = Path(path) if path else None
        loadouts: dict = {}
        if p is not None and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _warn(f"could not read ship-loadouts store {p} ({e}); starting empty")
            else:
                if isinstance(data, dict):
                    # Keep only rows that round-trip through the tolerant deserializer.
                    for k, v in data.items():
                        if _sid(k) is not None and snapshot_from_dict(v) is not None:
                            loadouts[str(k)] = v
                else:
                    _warn(f"ship-loadouts store {p} is not a JSON object; starting empty")
        return cls(loadouts=loadouts, path=p)

    # -- capture / read -----------------------------------------------------------------
    def capture(self, snapshot: LoadoutSnapshot | None) -> bool:
        """Remember ONE ship's loadout, keyed by its ShipID. Persists (and returns True) only when
        the stored build actually changed. A None snapshot, or one with no ShipID (can't be keyed),
        is a no-op returning False — the current ship's build is never lost to a keyless event."""
        changed, body = self.capture_deferred(snapshot)
        if body is not None:
            self.persist(body)
        return changed

    def capture_deferred(self, snapshot: LoadoutSnapshot | None) -> tuple[bool, str | None]:
        """Capture ONE ship's loadout IN MEMORY and render the body to persist, WITHOUT touching
        disk. Returns `(changed, body)` — `body` is None when nothing changed or no path. The
        journal path mutates under its state lock, then `persist()`s OUTSIDE it so a slow disk never
        stalls readers (#161)."""
        if snapshot is None:
            return False, None
        sid = _sid(getattr(snapshot, "ship_id", None))
        if sid is None:
            return False, None
        serialized = snapshot_to_dict(snapshot)
        before = json.dumps(self._loadouts.get(sid), sort_keys=True, ensure_ascii=False)
        after = json.dumps(serialized, sort_keys=True, ensure_ascii=False)
        if before == after:
            return False, None
        self._loadouts[sid] = serialized
        return True, (self._render() if self._path is not None else None)

    def get(self, ship_id: object) -> LoadoutSnapshot | None:
        """The remembered `LoadoutSnapshot` for a ShipID (accepts int or str), or None when nothing
        is remembered for it. Rebuilt fresh each call, so the caller can't mutate the store."""
        sid = _sid(ship_id)
        if sid is None:
            return None
        return snapshot_from_dict(self._loadouts.get(sid))

    def ship_ids(self) -> list[str]:
        """The ShipIDs (string keys) with a remembered loadout. Safe to hand out (a copy)."""
        return list(self._loadouts.keys())

    def __len__(self) -> int:
        return len(self._loadouts)

    # -- persistence --------------------------------------------------------------------
    def _render(self) -> str:
        """Serialize the whole store to its on-disk JSON body. Reads `_loadouts`, so call it under
        the caller's state lock; only the returned (immutable) string crosses into a write."""
        return json.dumps(self._loadouts, ensure_ascii=False, indent=2) + "\n"

    def persist(self, body: str) -> None:
        """Write a pre-rendered body to disk atomically (temp-then-replace), fail-soft — mirrors
        `owned_ships.persist`. Serialised by `_io_lock` (never the state lock) so it's safe to call
        OUTSIDE the EDContext lock (#161). A no-op when no path is configured."""
        if self._path is None:
            return
        with self._io_lock:
            p = self._path
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(p.suffix + ".tmp")
                tmp.write_text(body, encoding="utf-8")
                tmp.replace(p)  # atomic on the same filesystem
            except OSError as e:
                _warn(f"could not save ship-loadouts store {p} ({e})")

    def save(self) -> None:
        """Persist the current state immediately (render + write). Retained for direct callers; the
        journal path uses `capture_deferred` + `persist` to keep disk off the lock."""
        if self._path is not None:
            self.persist(self._render())


def _warn(msg: str) -> None:
    """Fail-soft diagnostic to stderr (matches owned_ships / npc_crew) — never an exception up."""
    print(f"!! [ship_loadouts] {msg}", file=sys.stderr, flush=True)
