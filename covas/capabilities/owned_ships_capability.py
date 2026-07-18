"""Owned-ships voice capability (issue #134) — "what ships do I own", plus voice CRUD to add /
remove a ship the registry hasn't captured an event for or got wrong.

Reads and mutates the persisted owned-ships registry (`ed/owned_ships.py`) that the journal
watcher keeps on `EDContext` — folded from the Shipyard* ownership events and reconciled from
Loadout / StoredShips. This capability is the CONVERSATIONAL surface over that identity: it lists
the fleet, and provides the manual corrections the acceptance criteria call for ("I bought a
Python", "remove the Cobra") for pre-existing ships or a mis-captured event.

All I/O is injected — the owned-ships getter and the add/remove mutators (the lock-protected
`EDContext` methods in the app; a plain registry's methods, or fakes, in tests) plus an optional
log — so the default `pytest` run is offline and free (DESIGN §9). Fail soft: any error is spoken,
never raised into the voice loop.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..ed.owned_ships import display_name
from .base import HelpMeta, Slot

_LIST_TOOL = "list_owned_ships"
_ADD_TOOL = "add_owned_ship"
_REMOVE_TOOL = "remove_owned_ship"

# Cap the spoken fleet list so a big fleet doesn't read out forever.
_MAX_LIST = 8

_NO_SHIPS = ("I haven't recorded any ships you own yet. I pick them up from the journal when you "
             "buy, switch, or dock with your fleet — or you can tell me, like 'I bought a Python'.")

_LIST_DESC = (
    "List the ships the Commander OWNS — their persistent fleet, tracked from the journal's "
    "ownership events (buying / selling / switching ships) and reconciled from the active ship and "
    "stored-ships inventory, surviving restarts. Call for 'what ships do I own', 'what's in my "
    "fleet', 'which ship am I flying'. A free local read — ALWAYS call it rather than answering "
    "from memory. It names each ship (hull + custom name), flags the active one, and gives the "
    "last-known location when known."
)
_ADD_DESC = (
    "Manually record a ship the Commander OWNS that the journal hasn't captured — a correction, or "
    "a ship bought before COVAS++ was watching. Use for 'I bought a Python', 'add my Cobra to the "
    "fleet', 'I own an Anaconda called Void Runner'. Pass `ship_type` (the hull, as spoken) and "
    "optionally its custom `name` / `ident`. A manual entry is kept and never overwritten by a "
    "later journal event."
)
_REMOVE_DESC = (
    "Remove a ship from the Commander's owned fleet — for a correction, or a ship sold outside a "
    "captured event. Use for 'remove the Cobra', 'I sold my Anaconda', 'take the Sidewinder off my "
    "fleet'. Pass `ship` naming the hull, custom name, or ident. If more than one ship matches it "
    "asks which, rather than guessing."
)

_ADD_ARGS = {
    "ship_type": {
        "type": "string",
        "description": "The hull to add, as spoken ('Python', 'Cobra MkIII', 'Federal Corvette').",
    },
    "name": {
        "type": "string",
        "description": "The Commander's custom ship name, if given ('Void Runner'). Optional.",
    },
    "ident": {
        "type": "string",
        "description": "The ship's ident/registration, if given ('VR-01'). Optional.",
    },
}
_REMOVE_ARGS = {
    "ship": {
        "type": "string",
        "description": "The owned ship to remove, as spoken — a hull ('Cobra'), a custom name, or "
                       "an ident.",
    },
}


class OwnedShipsCapability:
    """Advertises the owned-ships list + CRUD tools and answers them from injected seams (the
    live EDContext accessors in the app; stubs/fakes in tests)."""
    # Tiering group (issue #84): shares the engineering token-budget cluster with the other
    # ship/fleet capabilities (stored ships, loadout, engineers).
    TIERING_GROUP = "engineering"

    def __init__(
        self,
        *,
        get_owned: Callable[[], list[dict]],
        add_ship: Callable[..., Optional[dict]],
        remove_ship: Callable[[str], tuple[bool, list]],
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._get_owned = get_owned
        self._add = add_ship
        self._remove = remove_ship
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [
            {"name": _LIST_TOOL, "description": _LIST_DESC,
             "input_schema": {"type": "object", "properties": {}, "required": []}},
            {"name": _ADD_TOOL, "description": _ADD_DESC,
             "input_schema": {"type": "object", "properties": dict(_ADD_ARGS),
                              "required": ["ship_type"]}},
            {"name": _REMOVE_TOOL, "description": _REMOVE_DESC,
             "input_schema": {"type": "object", "properties": dict(_REMOVE_ARGS),
                              "required": ["ship"]}},
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="owned ships",
            group="your ship",
            one_liner=("I keep track of the ships you own — updated as you buy, sell, and switch "
                       "ships — and you can correct the list by voice."),
            example="what ships do I own",
            slots=(
                Slot(param="ship_type",
                     phrasings=("a ship I bought", "a hull like Python or Cobra"),
                     example="I bought a Python",
                     help_text="Tell me a ship you bought and I'll add it to your fleet."),
                Slot(param="ship",
                     phrasings=("a ship to remove", "the Cobra, or a ship by name"),
                     example="remove the Cobra",
                     help_text="Name an owned ship and I'll take it off your fleet."),
            ),
            help_when_active=("Ask what ships you own, or tell me to add or remove one."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _LIST_TOOL:
                return self._list()
            if name == _ADD_TOOL:
                return self._add_ship(inp)
            if name == _REMOVE_TOOL:
                return self._remove_ship(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Owned-ships error: {e}"

    # -- list ---------------------------------------------------------------------------
    def _list(self) -> str:
        ships = self._get_owned() or []
        if not ships:
            return _NO_SHIPS
        total = len(ships)
        parts = [f"You own {total} ship{_s(total)}."]
        frags = [self._describe(rec) for rec in ships[:_MAX_LIST]]
        if total > _MAX_LIST:
            frags.append(f"and {total - _MAX_LIST} more")
        parts.append(_join(frags) + ".")
        return " ".join(parts)

    def _describe(self, rec: dict) -> str:
        label = display_name(rec)
        bits = [label]
        if rec.get("active"):
            bits.append("(the one you're flying)")
        elif rec.get("system"):
            bits.append(f"in {rec['system']}")
        return " ".join(bits)

    # -- add ----------------------------------------------------------------------------
    def _add_ship(self, inp: dict) -> str:
        ship_type = str(inp.get("ship_type") or "").strip()
        if not ship_type:
            return "Tell me which ship — say the hull, like 'I bought a Python'."
        name = str(inp.get("name") or "").strip() or None
        ident = str(inp.get("ident") or "").strip() or None
        rec = self._add(ship_type, name=name, ident=ident)
        if not rec:
            return (f"I couldn't add {ship_type} — the owned-ships registry isn't available "
                    "right now.")
        self._logline(f"added {rec.get('ship_type')} (id {rec.get('ship_id')})")
        return f"Added {display_name(rec)} to your fleet."

    # -- remove -------------------------------------------------------------------------
    def _remove_ship(self, inp: dict) -> str:
        query = str(inp.get("ship") or "").strip()
        if not query:
            return "Which ship should I remove? Name the hull, a custom name, or its ident."
        removed, matches = self._remove(query)
        if removed:
            self._logline(f"removed match for '{query}'")
            return f"Removed {display_name(matches[0][1])} from your fleet."
        if not matches:
            owned = self._get_owned() or []
            if not owned:
                return _NO_SHIPS
            sample = _join(display_name(r) for r in owned[:_MAX_LIST])
            return f"I don't see an owned ship matching '{query}'. You own: {sample}."
        names = _join(display_name(rec) for _sid, rec in matches[:_MAX_LIST])
        return (f"More than one ship matches '{query}' — {names}. Which one? "
                "Tell me its custom name or ident.")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


# ---- speech helpers ----------------------------------------------------------------------

def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _join(items) -> str:
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
