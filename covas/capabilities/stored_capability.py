"""Stored ships & modules finder (issue #67) — "where's my Cutter / where did I leave that
module / how much to transfer it here?" by voice.

LLM-native tools over the `StoredShipsSnapshot` / `StoredModulesSnapshot` the journal watcher
keeps on `EDContext` (ed/stored.py). Everything spoken is derived from the journal snapshot
plus the offline symbol->name tables (`ed/module_names.py`, `ed/stored.py`): a ship/module can
only be named and located if it is genuinely in the recorded inventory, and the transfer
figures are the game's OWN quoted `TransferPrice`/`TransferCost`/`TransferTime` — surfaced
verbatim, never re-derived, so nothing is invented.

**Freshness.** Frontier writes StoredShips/StoredModules (and their transfer quotes) when you
dock somewhere with a shipyard / outfitting. The data — including the transfer time & cost — is
therefore accurate *as of your last such dock* (the origin the quotes are measured from). If
you've moved since, the capability says the figures are from that last dock rather than
pretending they're live. No CAPI, no network.

**Handoff.** When a query resolves to a single ship/module sitting elsewhere, the destination
system is copied to the clipboard (galaxy-map paste) — unless you're already in that system
(the N3 "already there -> don't copy" rule).

All I/O is injected — the snapshot getters, the current-system getter, and the clipboard — so
the default `pytest` run is offline and free (DESIGN §9). Fail soft: any error is spoken.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Callable

from ..ed.module_names import module_name
from ..ed.stored import StoredModule, StoredModulesSnapshot, StoredShip, StoredShipsSnapshot
from .base import HelpMeta, Slot

_SHIP_TOOL = "find_stored_ship"
_MODULE_TOOL = "find_stored_module"

# Cap spoken lists so a big fleet / module locker doesn't read out forever.
_MAX_LIST = 6

_NO_SHIPS = ("I haven't read your stored ships yet — dock at a station with a shipyard and "
             "I'll pick them up from the journal.")
_NO_MODULES = ("I haven't read your stored modules yet — dock at a station with outfitting "
               "and I'll pick them up from the journal.")

# Item-symbol fragment aliases for loose module speech (stored modules have no fitted slot, so
# the fitted-loadout slot aliases don't apply — we match the raw Item symbol instead).
_MODULE_ALIASES: dict[str, str] = {
    "fsd": "hyperdrive",
    "frame shift drive": "hyperdrive",
    "drive": "hyperdrive",
    "jump drive": "hyperdrive",
    "shields": "shieldgenerator",
    "shield": "shieldgenerator",
    "shield generator": "shieldgenerator",
    "shield booster": "shieldbooster",
    "boosters": "shieldbooster",
    "fuel scoop": "fuelscoop",
    "scoop": "fuelscoop",
    "power plant": "powerplant",
    "reactor": "powerplant",
    "power distributor": "powerdistributor",
    "distributor": "powerdistributor",
    "thrusters": "engine",
    "sensors": "sensors",
    "fsd booster": "guardianfsdbooster",
    "guardian booster": "guardianfsdbooster",
}

_SHIP_DESC = (
    "Locate the Commander's STORED ships from the live journal (StoredShips): where each parked "
    "ship is and — for one that's elsewhere — the game's own quoted transfer cost and time to "
    "bring it to where you're docked. Call with `ship` for a specific one ('where's my Cutter', "
    "'where did I leave my Corvette') — it says whether it's here or names its system, quotes "
    "the transfer, and copies that system to the clipboard (unless you're already there). Call "
    "with no arguments for a rundown of the whole stored fleet. A free local read — ALWAYS call "
    "it rather than answering from memory; the transfer figures are accurate as of your last "
    "shipyard dock, and it relays that."
)
_MODULE_DESC = (
    "Locate the Commander's STORED (shelved) modules from the live journal (StoredModules): "
    "where each sits and — for one elsewhere — the game's own quoted transfer cost and time to "
    "bring it here. Call with `module` for a specific one ('where's my spare shield generator', "
    "'where did I leave that fuel scoop'), or with no arguments for the full stored-modules "
    "rundown. A free local read — ALWAYS call it rather than answering from memory; the transfer "
    "figures are accurate as of your last outfitting dock, and it relays that."
)

_SHIP_ARG = {
    "ship": {
        "type": "string",
        "description": "The stored ship to look for, as spoken ('Cutter', 'Federal Corvette', "
                       "'my exploration Anaconda', or a custom ship name). Omit for the whole "
                       "stored fleet.",
    },
}
_MODULE_ARG = {
    "module": {
        "type": "string",
        "description": "The stored module to look for, as spoken ('shield generator', 'fuel "
                       "scoop', 'FSD', '5A power plant'). Omit for the full stored-modules list.",
    },
}


class StoredCapability:
    """Advertises the stored-ships/modules tools and answers them from injected snapshot
    getters (the live EDContext getters in the app; stubs in tests)."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "engineering"

    def __init__(
        self,
        *,
        get_stored_ships: Callable[[], StoredShipsSnapshot | None],
        get_stored_modules: Callable[[], StoredModulesSnapshot | None],
        get_current_system: Callable[[], str | None],
        clipboard: Callable[[str], None],
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._get_ships = get_stored_ships
        self._get_modules = get_stored_modules
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        ship_schema = {"type": "object", "properties": dict(_SHIP_ARG), "required": []}
        mod_schema = {"type": "object", "properties": dict(_MODULE_ARG), "required": []}
        return [
            {"name": _SHIP_TOOL, "description": _SHIP_DESC, "input_schema": ship_schema},
            {"name": _MODULE_TOOL, "description": _MODULE_DESC, "input_schema": mod_schema},
        ]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="stored ships and modules",
            group="your ship",
            one_liner=("I find your stored ships and modules from the journal — where each one "
                       "is, and what it costs and how long to transfer it to you."),
            example="where's my Cutter",
            slots=(
                Slot(param="ship",
                     phrasings=("a ship", "my Cutter, Corvette, or a ship by name"),
                     example="where did I leave my Anaconda",
                     help_text="Name a stored ship and I'll tell you where it is and quote the "
                               "transfer to bring it to you."),
                Slot(param="module",
                     phrasings=("a module", "my spare shield generator or fuel scoop"),
                     example="where's my spare fuel scoop",
                     help_text="Name a shelved module and I'll tell you where it's stored and "
                               "quote the transfer cost and time."),
            ),
            help_when_active=("Ask where a specific stored ship or module is, or ask for the "
                              "whole stored fleet / module locker."),
        )

    def run_tool(self, name: str, inp: dict) -> str:
        try:
            if name == _SHIP_TOOL:
                return self._ships(inp)
            if name == _MODULE_TOOL:
                return self._modules(inp)
            return f"Unknown tool: {name}"
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Stored-inventory error: {e}"

    # -- ships --------------------------------------------------------------------------
    def _ships(self, inp: dict) -> str:
        snap = self._get_ships()
        if not isinstance(snap, StoredShipsSnapshot) or not snap.ships:
            return _NO_SHIPS
        query = str(inp.get("ship") or "").strip()
        if query:
            return self._ship_for(snap, query)
        return self._ships_overview(snap)

    def _ships_overview(self, snap: StoredShipsSnapshot) -> str:
        here = snap.here_ships()
        remote = snap.remote()
        total = len(snap.ships)
        parts = [f"You have {total} ship{_s(total)} in storage{_as_of(snap.station)}."]
        if here:
            names = _join(_ship_label(s) for s in here[:_MAX_LIST])
            more = "" if len(here) <= _MAX_LIST else f", and {len(here) - _MAX_LIST} more"
            where = f" at {snap.station}" if snap.station else " here"
            parts.append(f"Here{where}: {names}{more}.")
        if remote:
            frags = []
            for s in remote[:_MAX_LIST]:
                if s.in_transit:
                    frags.append(f"{_ship_label(s)} (in transit)")
                elif s.system:
                    frags.append(f"{_ship_label(s)} in {s.system}")
                else:
                    frags.append(_ship_label(s))
            more = "" if len(remote) <= _MAX_LIST else f", and {len(remote) - _MAX_LIST} more"
            parts.append("Elsewhere: " + _join(frags) + more + ".")
        return " ".join(parts)

    def _ship_for(self, snap: StoredShipsSnapshot, query: str) -> str:
        matches = _match_ships(snap.ships, query)
        if not matches:
            return self._unknown_ship(snap, query)
        # A single clear hit gets the full location + transfer + clipboard handoff.
        if len(matches) == 1:
            return self._ship_detail(snap, matches[0])
        # Several (e.g. two Cobras): list them compactly, no clipboard (ambiguous target).
        frags = [self._ship_where(snap, s) for s in matches[:_MAX_LIST]]
        return f"You have {len(matches)} matching stored ships. " + " ".join(frags)

    def _ship_detail(self, snap: StoredShipsSnapshot, s: StoredShip) -> str:
        if s.here:
            where = f" at {snap.station}" if snap.station else " here"
            return f"Your {_ship_label(s)} is here{where} — no transfer needed."
        if s.in_transit:
            dest = f" to {s.system}" if s.system else ""
            return f"Your {_ship_label(s)} is already in transit{dest}."
        if not s.system:
            return (f"Your {_ship_label(s)} is in storage elsewhere, but the journal didn't "
                    "record its system.")
        line = f"Your {_ship_label(s)} is in {s.system}{_as_of(snap.station)}."
        line += _transfer_phrase(s.transfer_price, s.transfer_time)
        return line + self._deliver(s.system)

    def _ship_where(self, snap: StoredShipsSnapshot, s: StoredShip) -> str:
        if s.here:
            return f"{_ship_label(s)} is here."
        if s.in_transit:
            return f"{_ship_label(s)} is in transit."
        if s.system:
            return f"{_ship_label(s)} is in {s.system}." + _transfer_phrase(
                s.transfer_price, s.transfer_time)
        return f"{_ship_label(s)} is stored elsewhere."

    def _unknown_ship(self, snap: StoredShipsSnapshot, query: str) -> str:
        stored = _dedupe(_ship_label(s) for s in snap.ships)
        sample = _join(stored[:_MAX_LIST])
        return (f"I don't see a stored ship matching '{query}'. In storage you have: {sample}.")

    # -- modules ------------------------------------------------------------------------
    def _modules(self, inp: dict) -> str:
        snap = self._get_modules()
        if not isinstance(snap, StoredModulesSnapshot) or not snap.modules:
            return _NO_MODULES
        query = str(inp.get("module") or "").strip()
        if query:
            return self._module_for(snap, query)
        return self._modules_overview(snap)

    def _modules_overview(self, snap: StoredModulesSnapshot) -> str:
        here = [m for m in snap.modules if m.here]
        remote = [m for m in snap.modules if not m.here and not m.in_transit]
        transit = [m for m in snap.modules if m.in_transit]
        total = len(snap.modules)
        parts = [f"You have {total} module{_s(total)} in storage{_as_of(snap.station)}."]
        if here:
            names = _join(_dedupe_counts(_module_display(m) for m in here))
            parts.append(f"Here: {names}.")
        if remote:
            frags = _dedupe_counts(f"{_module_display(m)} in {m.system}"
                                   for m in remote if m.system)
            unknown = [m for m in remote if not m.system]
            if unknown:
                frags = frags + _dedupe_counts(_module_display(m) + " (elsewhere)"
                                               for m in unknown)
            if frags:
                shown = frags[:_MAX_LIST]
                more = "" if len(frags) <= _MAX_LIST else f", and {len(frags) - _MAX_LIST} more"
                parts.append("Elsewhere: " + _join(shown) + more + ".")
        if transit:
            parts.append(f"{len(transit)} in transit.")
        return " ".join(parts)

    def _module_for(self, snap: StoredModulesSnapshot, query: str) -> str:
        matches = _match_modules(snap.modules, query)
        if not matches:
            return self._unknown_module(snap, query)
        if len(matches) == 1:
            return self._module_detail(snap, matches[0])
        # Several copies: summarize where they are; clipboard only if they're all one system.
        frags = [self._module_where(snap, m) for m in matches[:_MAX_LIST]]
        line = f"You have {len(matches)} matching stored modules. " + " ".join(frags)
        systems = {m.system for m in matches if not m.here and not m.in_transit and m.system}
        if len(systems) == 1:
            line += self._deliver(next(iter(systems)))
        return line

    def _module_detail(self, snap: StoredModulesSnapshot, m: StoredModule) -> str:
        label = _module_display(m)
        if m.here:
            where = f" at {snap.station}" if snap.station else " here"
            return f"Your {label} is stored here{where} — no transfer needed."
        if m.in_transit:
            dest = f" to {m.system}" if m.system else ""
            return f"Your {label} is already in transit{dest}."
        if not m.system:
            return (f"Your {label} is stored elsewhere, but the journal didn't record its "
                    "system.")
        line = f"Your {label} is in {m.system}{_as_of(snap.station)}."
        line += _transfer_phrase(m.transfer_cost, m.transfer_time)
        return line + self._deliver(m.system)

    def _module_where(self, snap: StoredModulesSnapshot, m: StoredModule) -> str:
        label = _module_display(m)
        if m.here:
            return f"one {label} is here."
        if m.in_transit:
            return f"one {label} is in transit."
        if m.system:
            return f"one {label} is in {m.system}." + _transfer_phrase(
                m.transfer_cost, m.transfer_time)
        return f"one {label} is stored elsewhere."

    def _unknown_module(self, snap: StoredModulesSnapshot, query: str) -> str:
        stored = _dedupe(_module_display(m) for m in snap.modules)
        sample = _join(stored[:_MAX_LIST])
        return (f"I don't see a stored module matching '{query}'. In storage you have: {sample}.")

    # -- shared: clipboard handoff ------------------------------------------------------
    def _deliver(self, system: str) -> str:
        """Copy `system` and return the trailing clipboard sentence — unless the Commander is
        already in that system (N3: don't copy your own current system)."""
        current = self._current_system()
        if current and system and current.strip().lower() == system.strip().lower():
            return " That's your current system, so I haven't copied anything."
        copied = self._copy(system)
        return (f" I've copied {system} to your clipboard." if copied
                else f" (Couldn't copy to the clipboard — the system is {system}.)")

    def _copy(self, text: str) -> bool:
        try:
            self._clipboard(text)
            self._logline(f"copied {text}")
            return True
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return False

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


# ---- naming / matching helpers -------------------------------------------------------------

def _ship_label(s: StoredShip) -> str:
    """Spoken label: the hull name, plus the custom ship name when it's meaningfully set."""
    hull = s.display
    name = (s.name or "").strip()
    if name and name.lower() != hull.lower():
        return f"{hull} \"{name}\""
    return hull


def _module_display(m: StoredModule) -> str:
    """Spoken module name: the module_names mapping of the normalized symbol, falling back to
    the journal's own localised string, then a prettified symbol."""
    spoken = module_name(m.name)
    if spoken and spoken != "unknown module":
        return spoken
    loc = (m.name_localised or "").strip()
    return loc or m.name.replace("_", " ").title()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).strip()


def _match_ships(ships: tuple[StoredShip, ...], query: str) -> list[StoredShip]:
    """Stored ships whose hull name or custom name matches the spoken query. Substring both
    ways first (so 'cutter' matches 'Imperial Cutter'); a difflib pass catches near-misses."""
    q = _norm(query)
    if not q:
        return []
    hits: list[StoredShip] = []
    for s in ships:
        names = (_norm(s.display), _norm(s.name or ""), _norm(s.ship_type))
        if any(n and (q in n or n in q) for n in names):
            hits.append(s)
    if hits:
        return hits
    # Fuzzy fallback on the hull display name (Whisper mishears, partial words).
    labelled = [(_norm(s.display), s) for s in ships]
    close = difflib.get_close_matches(q, [n for n, _ in labelled], n=3, cutoff=0.7)
    return [s for n, s in labelled if n in close]


def _match_modules(modules: tuple[StoredModule, ...], query: str) -> list[StoredModule]:
    """Stored modules matching a spoken query. Alias the common loose terms to an Item-symbol
    fragment, then match that / the spoken name / the raw symbol."""
    q = _norm(query)
    if not q:
        return []
    fragment = _MODULE_ALIASES.get(q)
    hits: list[StoredModule] = []
    for m in modules:
        symbol = _norm(m.name)
        if fragment and fragment in symbol:
            hits.append(m)
            continue
        names = (_norm(_module_display(m)), _norm(m.name_localised or ""), symbol)
        if any(n and (q in n or n in q) for n in names):
            hits.append(m)
    return hits


# ---- speech formatting ---------------------------------------------------------------------

def _transfer_phrase(cost: int | None, time_s: int | None) -> str:
    """' Transferring it here costs 12,000 credits and takes about 5 minutes.' — from the
    journal's own quote. Empty when neither figure is known."""
    bits = []
    if cost is not None:
        bits.append(f"costs {_credits(cost)}")
    if time_s is not None:
        bits.append(f"takes {_duration(time_s)}")
    if not bits:
        return ""
    return " Transferring it here " + " and ".join(bits) + "."


def _credits(value: int) -> str:
    from ..i18n import fmt_int, fmt_num  # locale grouping/decimal for spoken amounts (#199)
    if value <= 0:
        return "nothing"
    if value >= 1_000_000:
        return f"{fmt_num(value / 1_000_000, 1)} million credits"
    return f"{fmt_int(value)} credits"


def _duration(seconds: int) -> str:
    """A rough spoken duration from a whole-seconds transfer time."""
    if seconds <= 0:
        return "no time"
    if seconds < 60:
        return f"about {seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        rem = seconds % 60
        base = f"{minutes} minute{_s(minutes)}"
        return base if rem < 15 else f"{base} {rem} seconds"
    hours = minutes // 60
    rem_min = minutes % 60
    base = f"{hours} hour{_s(hours)}"
    return base if rem_min == 0 else f"{base} {rem_min} minute{_s(rem_min)}"


def _as_of(station: str | None) -> str:
    """Freshness clause: the transfer quotes/locations are as of the last shipyard dock."""
    return f" (as of your last dock at {station})" if station else " (as of your last dock)"


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _join(items) -> str:
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _dedupe(items) -> list[str]:
    out: list[str] = []
    for it in items:
        if it not in out:
            out.append(it)
    return out


def _dedupe_counts(items) -> list[str]:
    """Collapse duplicates into 'N x label', preserving first-seen order (keeps a spoken list
    short when several identical modules are stored together)."""
    order: list[str] = []
    counts: dict[str, int] = {}
    for it in items:
        if it not in counts:
            order.append(it)
            counts[it] = 0
        counts[it] += 1
    return [(label if counts[label] == 1 else f"{counts[label]} x {label}") for label in order]
