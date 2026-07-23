"""NPC crew registry (issue #125) — harvest your hired NPC fighter pilots from the journal.

Elite writes **no snapshot** of currently-hired crew; hiring history is scattered across five
sparse events, some of which may live in journals from months ago. So we persist a small
git-ignored **seen-set**, keyed by CrewID, that survives restarts and accumulates names as they
appear. The Crew editor reads it to offer a datalist of your ACTUAL pilots to *adopt* (issue #125);
nothing here ever auto-adds a pilot to the speaking roster — adoption is always explicit.

The five events we fold (NPC crew only — multicrew *human* events like `CrewMemberJoins` are
deliberately excluded):

  * `CrewHire      {Name, CrewID, Faction, Cost, CombatRank}` — the hire itself.
  * `CrewAssign    {Name, CrewID, Role}` — duty change; only a signal the pilot still exists
                    (the `Role` here is a game duty like "Active", NOT our free-text crew role).
  * `NpcCrewPaidWage {NpcCrewName, NpcCrewId, Amount}` — recurring; the reliable way a
                    long-ago-hired pilot resurfaces in a *current* session.
  * `NpcCrewRank   {NpcCrewName, NpcCrewId, RankCombat}` — a combat-rank tick.
  * `CrewFire      {Name, CrewID}` — removes the entry by CrewID.

Everything here is PURE + fail-soft: `fold()` is a total function (raw event in, updated map out,
never raises), and the on-disk registry mirrors `crew.save_members` — atomic temp-then-replace,
swallowing I/O errors. A corrupt file degrades to an empty registry rather than crashing the
journal thread. `last_seen` is taken from the event's own `timestamp` field (NOT wall-clock) so
folds are deterministic and testable.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

# The events we care about, and which key holds the CrewID / the pilot's name in each. The
# Npc*-prefixed events use `NpcCrewId` / `NpcCrewName`; the older Crew* events use `CrewID` / `Name`.
_ID_KEYS = ("CrewID", "NpcCrewId")
_NAME_KEYS = ("Name", "NpcCrewName")

# Combat-rank ordinal -> spoken name (Frontier's ranks), for building a nicer suggest-persona prompt.
# Out-of-range / unknown ordinals fall back to the bare number at call sites.
_COMBAT_RANKS = (
    "Harmless", "Mostly Harmless", "Novice", "Competent", "Expert",
    "Master", "Dangerous", "Deadly", "Elite",
)


def combat_rank_name(rank: object) -> str:
    """A spoken combat-rank name for an ordinal (0..8), or '' when unknown. Pure, total."""
    if isinstance(rank, bool):  # bool is an int subclass — never a rank
        return ""
    if isinstance(rank, int) and 0 <= rank < len(_COMBAT_RANKS):
        return _COMBAT_RANKS[rank]
    return ""


def _crew_id(event: dict) -> str | None:
    """The CrewID for this event as a string key (JSON object keys are strings), or None."""
    for k in _ID_KEYS:
        if k in event and event[k] is not None:
            return str(event[k])
    return None


def _name(event: dict) -> str:
    for k in _NAME_KEYS:
        v = str(event.get(k, "") or "").strip()
        if v:
            return v
    return ""


def fold(entries: dict, event: dict) -> dict:
    """Fold one journal event into the `{crew_id: {name, combat_rank?, faction?, last_seen}}` map,
    returning the (possibly identical) updated map. PURE + total: an event we don't recognise, or
    one missing a CrewID, is a no-op. Mutates and returns `entries` for the caller's convenience —
    callers wanting immutability pass a copy.

    `CrewFire` removes by CrewID. Every other handled event upserts: the pilot's name and
    `last_seen` (from the event `timestamp`) are refreshed, and any combat rank / faction the event
    carries is merged in without clobbering a value a prior event already recorded."""
    name = event.get("event", "")
    if not isinstance(event, dict) or name not in _HANDLED:
        return entries
    crew_id = _crew_id(event)
    if not crew_id:
        return entries
    if name == "CrewFire":
        entries.pop(crew_id, None)
        return entries
    rec = dict(entries.get(crew_id) or {})
    disp = _name(event)
    if disp:
        rec["name"] = disp
    ts = str(event.get("timestamp", "") or "").strip()
    if ts:
        rec["last_seen"] = ts
    faction = str(event.get("Faction", "") or "").strip()
    if faction:
        rec["faction"] = faction
    # Combat rank arrives as `CombatRank` (CrewHire) or `RankCombat` (NpcCrewRank).
    rank = event.get("CombatRank", event.get("RankCombat"))
    if isinstance(rank, int) and not isinstance(rank, bool):
        rec["combat_rank"] = rank
    if rec.get("name"):  # never store a nameless ghost entry
        entries[crew_id] = rec
    return entries


_HANDLED = frozenset(
    {"CrewHire", "CrewAssign", "NpcCrewPaidWage", "NpcCrewRank", "CrewFire"}
)


class NpcCrewRegistry:
    """The persisted NPC-crew seen-set: `{crew_id: {name, combat_rank?, faction?, last_seen}}`,
    backed by a JSON file. State is guarded by the EDContext lock; the only internal lock is
    `_io_lock`, which serialises the DISK write so the journal thread can persist OUTSIDE the
    EDContext lock without a slow disk stalling readers (#161). Fail-soft throughout."""

    def __init__(self, entries: dict | None = None, path: Path | str | None = None) -> None:
        self._entries: dict = dict(entries or {})
        self._path: Path | None = Path(path) if path else None
        # Serialises DISK writes only (never held during a state mutation), so the journal thread
        # can persist OUTSIDE the EDContext lock without a slow disk stalling readers, yet two
        # writers can't corrupt the shared temp file (#161). State reads happen under the caller's
        # lock; the body is rendered there and only the finished string crosses into a write.
        self._io_lock = threading.Lock()

    @classmethod
    def load(cls, path: Path | str | None) -> NpcCrewRegistry:
        """Read the registry from disk, fail-soft. A missing/corrupt/non-dict file yields an EMPTY
        registry (never raises) so a bad file can't wedge the journal watcher."""
        p = Path(path) if path else None
        entries: dict = {}
        if p is not None and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError, ValueError) as e:
                _warn(f"could not read npc-crew registry {p} ({e}); starting empty")
            else:
                if isinstance(data, dict):
                    # Keep only well-formed {crew_id: {name: ...}} rows.
                    entries = {str(k): dict(v) for k, v in data.items()
                               if isinstance(v, dict) and str(v.get("name", "")).strip()}
                else:
                    _warn(f"npc-crew registry {p} is not a JSON object; starting empty")
        return cls(entries=entries, path=p)

    def apply_event(self, event: dict) -> bool:
        """Fold `event` into the registry and persist if anything changed. Returns True on a change
        (so the caller could log), False otherwise. Fail-soft — persistence errors are swallowed."""
        changed, body = self.apply_event_deferred(event)
        if body is not None:
            self.persist(body)
        return changed

    def apply_event_deferred(self, event: dict) -> tuple[bool, str | None]:
        """Fold `event` into the registry IN MEMORY and render the body to persist, WITHOUT touching
        disk. Returns `(changed, body)` — `body` is the JSON to write (None when nothing changed or
        no path is configured). The caller mutates under its state lock, then `persist()`s the body
        OUTSIDE that lock so a slow disk never stalls readers (#161)."""
        before = json.dumps(self._entries, sort_keys=True, ensure_ascii=False)
        fold(self._entries, event)
        after = json.dumps(self._entries, sort_keys=True, ensure_ascii=False)
        if before == after:
            return False, None
        return True, (self._render() if self._path is not None else None)

    def _render(self) -> str:
        """Serialize the whole registry to its on-disk JSON body. Reads `_entries`, so call it under
        the caller's state lock; only the returned (immutable) string crosses into a write."""
        return json.dumps(self._entries, ensure_ascii=False, indent=2) + "\n"

    def persist(self, body: str) -> None:
        """Write a pre-rendered body to disk atomically (temp-then-replace), fail-soft — mirrors
        `crew.save_members`. Serialised by `_io_lock` (never the state lock) so it's safe to call
        OUTSIDE the EDContext lock. A no-op when no path is configured."""
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
                _warn(f"could not save npc-crew registry {p} ({e})")

    def save(self) -> None:
        """Persist the current state immediately (render + write). Retained for direct callers /
        tests; the journal path uses `apply_event_deferred` + `persist` to keep disk off the lock."""
        if self._path is not None:
            self.persist(self._render())

    def entries(self) -> dict:
        """A shallow copy of the raw `{crew_id: record}` map (safe to hand out / snapshot)."""
        return {k: dict(v) for k, v in self._entries.items()}

    def hired(self) -> list[dict]:
        """The hired pilots as `[{name, combat_rank}]` for the Crew-editor datalist — most-recently
        seen first (by `last_seen`), de-duplicated by name (a re-hire under a new CrewID collapses
        to one suggestion). `combat_rank` is the raw ordinal or None."""
        rows = sorted(self._entries.values(),
                      key=lambda r: str(r.get("last_seen", "")), reverse=True)
        out: list[dict] = []
        seen: set[str] = set()
        for r in rows:
            name = str(r.get("name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "combat_rank": r.get("combat_rank")})
        return out


def _warn(msg: str) -> None:
    """Fail-soft diagnostic to stderr (matches crew.py / app.py) — never an exception upward."""
    print(f"!! [npc_crew] {msg}", file=sys.stderr, flush=True)
