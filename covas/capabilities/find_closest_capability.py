"""Find-closest capabilities — "the closest station that sells module X / ship Y".

Two bespoke tools that deliberately stay OUTSIDE the spec-driven search family (issue #111): they
use the outfitting/shipyard request path in `nav/closest.py` (module resolution + mount/pad
post-filter; ship roster + local-shipyard cross-check + EDSM stock verification), NOT the generic
`build_query`/`execute_search` slot search the family is built on — and the module tool carries a
stateful confirmation turn-gate. Forcing either through the family descriptor would need so many
hooks it would be obfuscation, not simplification. They share the `[nav]` config and the same
resolve -> search dialog, so they're grouped in one module.

The dialogue is multi-turn but the tools are STATELESS: the conversation history *is* the state, so
each re-call just passes more-complete args (module -> +size/mount -> +confirmed; loose ship family
-> a picked variant). The tool is a pure function of its arguments; there's no pending-request
object to manage.

Flow the MODULE tool DESCRIPTION steers the LLM through:
  1. Commander asks for the closest <module>.
  2. The LLM normalizes the (maybe misheard) name and calls the tool. The tool validates
     against the offline taxonomy and returns structured guidance:
       - NEED_ATTRS  -> ask for the missing size/mount (never guess), don't search.
       - AMBIGUOUS   -> ask which module they meant, don't search.
       - UNKNOWN     -> say so, offer suggestions, don't search.
       - RESOLVED    -> state the interpretation and (if require_confirmation) ask to CONFIRM.
  3. Commander narrows / confirms / cancels. On "cancel / never mind" the LLM simply drops the
     request (it doesn't call the tool) — a verbal cancel is an LLM-recognized intent.
  4. When RESOLVED (and confirmed if required), the one rate-limited Spansh query fires — once.

The SHIP tool is the shipyard sibling: same pattern, but ships have no size/mount and no
confirmation gate (a resolved ship is a single unambiguous decision), so it searches at once.

Everything I/O-bound is injected (`http`, `get_current_system`, `clipboard`) so the whole module is
unit-testable offline and the default `pytest` never hits the network or the real clipboard (DESIGN
§9). Fail soft throughout — an unknown module/ship or a failed lookup is spoken, never raised.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from ..nav import (Ambiguous, AmbiguousShip, NavError, NeedAttrs, Resolved, ResolvedShip,
                   SHIP_NAMES, Unknown, UnknownShip, copy as _default_copy, find_closest_module,
                   find_closest_ship, resolve as _default_resolve,
                   resolve_ship as _default_resolve_ship)
from ..nav.closest import Http, RequestsHttp, _DEFAULT_BASE_URL, _DEFAULT_UA, _SEARCH_SIZE
from ..nav.modules import TAXONOMY
from . import _search_support as sup
from .base import HelpMeta, Slot


# ---- config -------------------------------------------------------------------------------


@dataclass(frozen=True)
class NavConfig:
    """Immutable snapshot of `[nav]`. Off by default; the capability isn't registered unless
    `enabled`. Shared by BOTH find-closest tools (module + ship) — pad default, base URL,
    user-agent, search size, enable toggle."""
    enabled: bool = False
    base_url: str = _DEFAULT_BASE_URL
    user_agent: str = _DEFAULT_UA
    default_pad_size: str = "L"          # my main ships need Large — configurable
    search_size: int = _SEARCH_SIZE
    # Require an explicit separate-turn confirmation before the (rate-limited) search fires.
    # DEFAULT OFF: a resolved module searches immediately — this is a read-only lookup, and
    # in practice the extra "confirm" turn is friction. When ON, a hard turn-gate (mirroring
    # the keybind safety layer) makes the confirmation real — the model can't self-confirm
    # inside the arming turn the way it otherwise will.
    require_confirmation: bool = False
    # SHIP search only: confirm each candidate's CURRENT stock against EDSM before speaking
    # it (Spansh lists a station's catalog, not its stock). DEFAULT ON — the kill switch
    # exists in case EDSM misbehaves. Ignored by the module search.
    verify_stock: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> "NavConfig":
        n = cfg.get("nav", {}) or {}
        d = cls()
        pad = str(n.get("default_pad_size", d.default_pad_size) or "").strip()
        return cls(
            enabled=bool(n.get("enabled", False)),
            base_url=str(n.get("base_url", d.base_url) or d.base_url),
            user_agent=str(n.get("user_agent", d.user_agent) or d.user_agent),
            default_pad_size=pad,
            search_size=int(n.get("search_size", d.search_size) or d.search_size),
            require_confirmation=bool(n.get("require_confirmation", False)),
            verify_stock=bool(n.get("verify_stock", True)),
        )


# ---- helpers shared by BOTH find-closest tools (module + ship) -----------------------------
# The two classes stay separate (different request paths, outcomes, and gates) but their small
# private helpers were byte-identical pairs — they live here once instead.


def _make_logline(log: Callable[[str], None] | None) -> Callable[[str], None]:
    """Bind the optional log seam once; None -> a no-op."""
    return log if log is not None else (lambda msg: None)


def _pad_constraint(
    inp: dict, default: str,
    get_current_ship_size: Callable[[], str | None] | None = None,
) -> str | None:
    """The pad constraint for this search: the tool arg if given, else the config
    default. 'any' / 'none' / '' disables it.

    'match' (issue #117 — "Match Current Ship Size") resolves to the Commander's
    CURRENTLY-FLOWN ship's pad size via the injected getter, falling back to Large when the
    ship is unknown (undetected, or an unrecognized symbol) — the conservative choice, since
    a Large pad fits any ship. Applies identically whether 'match' came from the config
    default or a one-off tool-arg override ('find the closest X for my current ship')."""
    raw = inp.get("pad_size")
    pad = str(raw).strip() if raw is not None else default
    if pad.lower() == "match":
        size = get_current_ship_size() if get_current_ship_size is not None else None
        pad = size or "L"
    if not pad or pad.lower() in ("any", "none", "n/a"):
        return None
    return pad


def _live_extra_names(idx: object | None, logline: Callable[[str], None],
                      label: str) -> tuple[str, ...]:
    """Newly-released names from the live index (empty until its background fetch lands, or
    if it's absent/unreachable). Fail-soft — a broken index never blocks a lookup; resolution
    just uses the bundled taxonomy/roster."""
    if idx is None:
        return ()
    try:
        return tuple(idx.extra_names())
    except Exception as e:  # noqa: BLE001 — the live index is a bonus, never fatal
        logline(f"{label} unavailable: {e}")
        return ()


def _say_unknown(o, noun: str) -> str:
    if o.suggestions:
        return (f"I don't recognize '{o.query}' as a {noun}. Did you mean "
                f"{sup.or_list(o.suggestions)}?")
    return (f"I don't recognize '{o.query}' as a {noun}. Tell me the {noun} name another "
            "way.")


def _say_ambiguous(o, plural: str) -> str:
    return f"That could be a few {plural} — {sup.or_list(o.candidates)}. Which one?"


# ==========================================================================================
# Find closest MODULE — find_closest_module (bespoke: nav/closest.py + confirmation turn-gate)
# ==========================================================================================

_TOOL_NAME = "find_closest_module"

_SHARED_STEPS = (
    "1. Normalize the spoken module name to a real module (e.g. 'multiple cannon' or "
    "'multicannon' -> Multi-Cannon) and call this tool with `module` set to your best "
    "interpretation.\n"
    "2. The tool replies with structured guidance. If it says attributes are MISSING "
    "(size and/or mount), ask the Commander for exactly those — offering the valid options "
    "it lists — and NEVER guess them. If it says the name is AMBIGUOUS, ask which one. If "
    "UNKNOWN, say so and offer the suggestions.\n"
)
_CANCEL_STEP = (
    "If the Commander says 'cancel' / 'never mind' / 'forget it', DROP the request and "
    "acknowledge — do NOT call this tool.\n"
)
# When the tool returns a result it ends with the copied-to-clipboard note. Tell the model to
# keep it — it paraphrases freely and will otherwise drop it (observed live).
_REPORT_STEP = (
    "When the tool returns a result, relay the station, system, and distance, and ALWAYS "
    "tell the Commander that the system name has been copied to their clipboard."
)

_DESC_NO_CONFIRM = (
    "Find the closest Elite Dangerous station that SELLS a given outfitting module, by "
    "distance from the Commander's current system, and copy that system's name to the "
    "clipboard. (If the Commander wants a whole SHIP rather than a module — 'where can I buy "
    "an Anaconda' — use the find_closest_ship tool; if they want the nearest station by "
    "SERVICE, TYPE, or PAD, use search_stations.) Resolve the module "
    "CONVERSATIONALLY, then search:\n"
    + _SHARED_STEPS +
    "3. As soon as the module is fully specified (name plus any required size/mount), the "
    "tool searches immediately and returns the nearest station — no separate confirmation "
    "step. " + _CANCEL_STEP +
    "It is stateless — re-call it each turn with everything known so far (module, then "
    "+size/+mount). " + _REPORT_STEP
)
_DESC_CONFIRM = (
    "Find the closest Elite Dangerous station that SELLS a given outfitting module, by "
    "distance from the Commander's current system, and copy that system's name to the "
    "clipboard. (If the Commander wants a whole SHIP rather than a module — 'where can I buy "
    "an Anaconda' — use the find_closest_ship tool; if they want the nearest station by "
    "SERVICE, TYPE, or PAD, use search_stations.) Resolve the module "
    "CONVERSATIONALLY before searching:\n"
    + _SHARED_STEPS +
    "3. When the tool reports the module is RESOLVED, tell the Commander your interpretation "
    "and ask them to CONFIRM before searching. Do NOT set `confirmed` until they actually "
    "confirm on a LATER turn — a confirmation in the same turn you resolved is refused.\n"
    "4. " + _CANCEL_STEP +
    "5. Only once the Commander confirms on a separate command, call the tool again with the "
    "same module args plus `confirmed=true` to run the one-shot search. " + _REPORT_STEP
)

_SCHEMA_PROPS = {
    "module": {
        "type": "string",
        "description": "Your best interpretation of the module name (e.g. 'Multi-Cannon', "
                       "'Frame Shift Drive', 'Fuel Scoop').",
    },
    "size": {
        "type": "string",
        "description": "Module size when relevant: a word for weapons "
                       "(small/medium/large/huge) or a class number for internals (1-8). "
                       "Omit if unknown — the tool will ask.",
    },
    "mount": {
        "type": "string",
        "description": "Weapon mount when relevant: fixed / gimballed / turreted. Omit if "
                       "unknown — the tool will ask.",
    },
    "pad_size": {
        "type": "string",
        "description": "Required landing-pad size (S/M/L) for the Commander's ship, or "
                       "'match' to use whatever ship they're CURRENTLY flying (falls back to "
                       "Large if unknown). Omit to use the configured default.",
    },
}
_CONFIRMED_PROP = {
    "confirmed": {
        "type": "boolean",
        "description": "Set true ONLY after the Commander has explicitly confirmed the "
                       "resolved module on a separate turn. Triggers the one-shot search.",
    },
}


def _build_tool(require_confirmation: bool) -> dict:
    """The tool schema, tailored to the confirmation mode so the model's instructions match
    the actual behavior (search-on-resolve vs confirm-first)."""
    props = dict(_SCHEMA_PROPS)
    if require_confirmation:
        props.update(_CONFIRMED_PROP)
    return {
        "name": _TOOL_NAME,
        "description": _DESC_CONFIRM if require_confirmation else _DESC_NO_CONFIRM,
        "input_schema": {"type": "object", "properties": props, "required": ["module"]},
    }


class FindClosestCapability:
    """Advertises `find_closest_module` and runs the resolve -> confirm -> search dialog.

    STANDALONE by design (issue #111): the outfitting request path (`nav/closest.py` module
    resolution + mount/pad post-filter) and the stateful confirmation turn-gate don't fit the
    generic slot-search family.

    Injected seams (all so the default test run is offline):
      * `http` — the Http poster for Spansh (RequestsHttp in the app; a fake in tests).
      * `get_current_system` — Callable[[], str|None] returning the Commander's current
        system (ED context, with a journal fallback the app wires up), or None.
      * `get_current_ship_size` — Callable[[], str|None] returning the pad size ("S"/"M"/"L")
        the Commander's CURRENTLY-FLOWN ship needs (ED context's ship symbol through
        `ed.ships.ship_pad_size`), or None when unknown. Backs the "match" pad option
        (#117); None (the default) makes 'match' fall back to Large, same as an unwired app.
      * `resolve` / `search` / `clipboard` — pure/offline deps, defaulted to the real ones
        but overridable in tests.
    """
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "search"

    def __init__(
        self,
        config: NavConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        get_current_ship_size: Callable[[], str | None] | None = None,
        resolve: Callable[..., object] = _default_resolve,
        search: Callable[..., object] = find_closest_module,
        clipboard: Callable[[str], None] = _default_copy,
        module_index: object | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._current_ship_size = get_current_ship_size
        self._resolve = resolve
        self._search = search
        self._clipboard = clipboard
        # Optional live taxonomy: surfaces module names Spansh knows that the bundle is missing
        # (newly-released modules), folded into resolution so a new module is findable with no
        # code change. None -> bundled taxonomy only (the default; keeps tests offline).
        self._module_index = module_index
        self._log = log
        self._logline = _make_logline(log)
        self._tool = _build_tool(config.require_confirmation)
        # Confirmation turn-gate (only used when require_confirmation is on): _turn counts
        # Commander utterances (advanced by new_turn()); _armed_turn is the turn a resolve
        # was armed on, so a confirmation is only honored on a genuinely later turn.
        self._lock = threading.Lock()
        self._turn = 0
        self._armed_turn: int | None = None

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [self._tool]

    def help_meta(self) -> HelpMeta:
        """Describe this capability for the help subsystem (Search Prompt 1). Templated help
        is projected from this — nothing here is generated by an LLM."""
        return HelpMeta(
            category="outfitting",
            group="navigation and search",
            one_liner=("I find the closest station selling an outfitting module and copy that "
                       "system to your clipboard."),
            example="find the closest multi-cannon",
            slots=(
                Slot(param="module",
                     phrasings=("the module name", "the module"),
                     example="find the nearest fuel scoop",
                     help_text="Name the module you want — a multi-cannon, a fuel scoop, a "
                               "frame shift drive, and so on."),
                Slot(param="size",
                     phrasings=("a size", "a class"),
                     example="a large multi-cannon",
                     help_text="For modules that come in several sizes, say the size — small, "
                               "medium, large, huge, or a class number."),
                Slot(param="mount",
                     phrasings=("a mount", "fixed, gimballed, or turreted"),
                     example="a gimballed multi-cannon",
                     help_text="For weapons, say the mount: fixed, gimballed, or turreted."),
                Slot(param="pad_size",
                     phrasings=("a landing pad size", "a pad size"),
                     example="somewhere with a large pad",
                     help_text="Restrict to stations with a given landing-pad size — small, "
                               "medium, or large — or say 'match my ship' to use whatever "
                               "ship you're currently flying for this one search."),
            ),
            help_when_active=("Tell me the module — and its size or mount if I ask — and I'll "
                              "find the nearest station that sells it."),
        )

    def help_vocabulary(self) -> dict[str, list[str]]:
        """The canonical module names help's failure-recovery mode matches an unresolved term
        against, so a suggested correction is always a real module (never invented)."""
        return {"module": [spec.name for spec in TAXONOMY]}

    def new_turn(self) -> None:
        """Called by the app once per Commander utterance. Advances the confirmation gate so
        a resolved module can only be confirmed on a genuinely new command (the model can't
        arm-and-confirm within one turn). No-op unless require_confirmation is on."""
        with self._lock:
            self._turn += 1

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Module lookup error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        module = str(inp.get("module") or "").strip()
        if not module:
            return "Which module should I find the closest station for?"

        # Fold in any modules the live index learned Spansh knows but the bundle doesn't. Only
        # pass the kwarg when there ARE extras, so the injected resolve seam stays simple.
        extra = _live_extra_names(self._module_index, self._logline, "module index")
        outcome = (self._resolve(module, inp.get("size"), inp.get("mount"), extra_names=extra)
                   if extra else self._resolve(module, inp.get("size"), inp.get("mount")))

        if isinstance(outcome, Unknown):
            return _say_unknown(outcome, "module")
        if isinstance(outcome, Ambiguous):
            return _say_ambiguous(outcome, "modules")
        if isinstance(outcome, NeedAttrs):
            return self._say_need_attrs(outcome)
        if isinstance(outcome, Resolved):
            # Default: search as soon as the module is resolved (read-only lookup; the extra
            # confirm turn is friction). require_confirmation flips on a real turn-gate.
            if not self._cfg.require_confirmation:
                return self._do_search(outcome, inp)
            return self._confirm_gate(outcome, inp)
        return "I couldn't interpret that module — try naming it another way."

    def _confirm_gate(self, resolved: Resolved, inp: dict) -> str:
        """Turn-gated confirmation (require_confirmation on). A `confirmed=true` call only
        searches if a resolve was armed on an EARLIER Commander turn — so the model can't
        arm-and-confirm inside one turn (exactly what Haiku does otherwise)."""
        with self._lock:
            armed_turn, cur = self._armed_turn, self._turn
        if bool(inp.get("confirmed")):
            if armed_turn is not None and cur > armed_turn:
                return self._do_search(resolved, inp)
            # Self-confirm in the arming turn (or never armed): refuse, and (re)arm at this
            # turn so the NEXT command genuinely confirms.
            with self._lock:
                self._armed_turn = self._turn
            self._logline(f"refused same-turn confirm for {resolved.label}")
            return (f"I've got {resolved.label} ready — but I need you to confirm on a "
                    f"separate command. Say 'confirm' or 'yes' and I'll search.")
        with self._lock:
            self._armed_turn = self._turn
        self._logline(f"resolved '{resolved.label}'; awaiting confirmation")
        return (f"Resolved to {resolved.label}. Say 'confirm' on a separate command and I'll "
                f"find the closest station that sells it. (Say 'cancel' to drop it.)")

    def _say_need_attrs(self, o: NeedAttrs) -> str:
        asks: list[str] = []
        for attr in o.missing:
            opts = o.options.get(attr, [])
            if attr == "size":
                asks.append(f"what size ({sup.or_list(opts)})")
            elif attr == "mount":
                asks.append(f"which mount ({sup.or_list(opts)})")
        joined = " and ".join(asks) if asks else "a bit more detail"
        return (f"I've got the {o.module}. Before I search, {joined}? I won't guess.")

    def _do_search(self, resolved: Resolved, inp: dict) -> str:
        """The one networked step — fires exactly once, only on RESOLVED + confirmed."""
        system = self._current_system() if self._current_system is not None else None
        pad = _pad_constraint(inp, self._cfg.default_pad_size, self._current_ship_size)
        try:
            result = self._search(
                resolved, system, self._http,
                pad_size=pad,
                base_url=self._cfg.base_url,
                user_agent=self._cfg.user_agent,
                search_size=self._cfg.search_size,
            )
        except NavError as e:
            self._logline(f"search failed for {resolved.label}: {e}")
            return str(e)

        # Search done — clear any confirmation arm so a later request starts fresh.
        with self._lock:
            self._armed_turn = None
        # Copy the SYSTEM name (what you paste into the galaxy map) — UNLESS the station is in
        # the Commander's current system (distance ~0): you're already there, so copying your
        # own system just clobbers the clipboard. Non-fatal on failure — answer still spoken.
        here = result.distance_ly < 0.05
        copied = False if here else self._copy(result.system)
        self._logline(f"nearest {resolved.label}: {result.station} in {result.system} "
                      f"({result.distance_ly:.1f} ly), pad {result.pad}, "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        return self._say_result(resolved, result, copied, here)

    def _say_result(self, resolved: Resolved, result, copied: bool, here: bool = False) -> str:
        # Inline on purpose, not sup.distance_phrase: this frozen line says "in your current
        # system" — one word off the shared helper — and #111 forbids changing a spoken byte.
        dist = ("in your current system" if result.distance_ly < 0.05
                else f"{result.distance_ly:.1f} light-years away")
        line = (f"Closest {resolved.label}: {result.station} in {result.system}, {dist}. "
                f"Largest pad {result.pad}.")
        arrival = result.extra.get("distance_to_arrival")
        if isinstance(arrival, (int, float)) and arrival >= 1:
            line += f" About {arrival:,.0f} light-seconds from the star."
        # Present only when the answer came from the stale fallback (nothing fresh matched).
        line += sup.stale_note(result.extra.get("stock_age_days"), what="that listing",
                               risk="outfitting stock rotates and it may be gone")
        if here:
            line += " You're already there, so I haven't copied anything."
        else:
            line += (f" I've copied {result.system} to your clipboard." if copied
                     else f" (Couldn't copy to the clipboard — the system is {result.system}.)")
        return line

    # -- helpers ----------------------------------------------------------------------
    def _copy(self, text: str) -> bool:
        try:
            self._clipboard(text)
            return True
        except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
            self._logline(f"clipboard copy failed: {e}")
            return False


# ==========================================================================================
# Find closest SHIP — find_closest_ship (bespoke: nav/closest.py ship path + EDSM stock verify)
# ==========================================================================================

_SHIP_TOOL = "find_closest_ship"

_SHIP_DESC = (
    "Find the closest Elite Dangerous station that SELLS a given SHIP (from a shipyard), by "
    "distance from the Commander's current system, and copy that system's name to the "
    "clipboard. Use THIS tool whenever the Commander wants to buy/find a whole SHIP ('where "
    "can I buy an Anaconda', 'find the closest Krait', 'nearest Python'). For an outfitting "
    "MODULE (multi-cannon, fuel scoop, shield generator) use find_closest_module instead; for "
    "the nearest station by SERVICE/TYPE/PAD use search_stations. Resolve the ship "
    "CONVERSATIONALLY, then search:\n"
    "1. Normalize the spoken ship name to a real ship (e.g. 'conda' -> Anaconda, 'fdl' -> "
    "Fer-de-Lance, 'clipper' -> Imperial Clipper) and call this tool with `ship` set to your "
    "best interpretation.\n"
    "2. The tool replies with structured guidance. If it says the name is AMBIGUOUS (a "
    "family like Krait, Cobra, Viper, Asp, Diamondback, or Type), ask which one it lists — "
    "NEVER guess. If UNKNOWN, say so and offer the suggestions.\n"
    "3. As soon as the ship is resolved to a single model, the tool searches immediately and "
    "returns the nearest station — there's no size/mount to ask about and no separate confirm "
    "step. If the Commander says 'cancel' / 'never mind', DROP the request and acknowledge — "
    "do NOT call this tool.\n"
    "It is stateless — re-call it each turn with the most specific name known so far (family "
    "-> picked model). When the tool returns a result, relay the station, system, and "
    "distance, and ALWAYS tell the Commander that the system name has been copied to their "
    "clipboard."
)

_SHIP_SCHEMA_PROPS = {
    "ship": {
        "type": "string",
        "description": "Your best interpretation of the ship name (e.g. 'Anaconda', 'Krait "
                       "Phantom', 'Type-9 Heavy', 'Fer-de-Lance'). For an ambiguous family "
                       "(Krait, Cobra, Viper, Asp, Type), pass what you have and the tool will "
                       "ask which model.",
    },
    "pad_size": {
        "type": "string",
        "description": "Required landing-pad size (S/M/L) for the Commander's ship, or "
                       "'match' to use whatever ship they're CURRENTLY flying (falls back to "
                       "Large if unknown). Omit to use the configured default.",
    },
}


class FindClosestShipCapability:
    """Advertises `find_closest_ship` and runs the resolve -> search dialog.

    STANDALONE by design (issue #111): the shipyard request path (`nav/closest.py` roster
    resolution + local-shipyard cross-check + EDSM live-stock verification) and the
    ResolvedShip/AmbiguousShip/UnknownShip outcomes don't fit the generic slot-search family.

    Injected seams (all so the default test run is offline):
      * `http` — the Http poster for Spansh (RequestsHttp in the app; a fake in tests).
      * `get_current_system` — Callable[[], str|None] returning the Commander's current
        system (ED context, with a journal fallback the app wires up), or None.
      * `get_current_ship_size` — Callable[[], str|None] returning the pad size ("S"/"M"/"L")
        the Commander's CURRENTLY-FLOWN ship needs (ED context's ship symbol through
        `ed.ships.ship_pad_size`), or None when unknown. Backs the "match" pad option
        (#117); None (the default) makes 'match' fall back to Large, same as an unwired app.
      * `get_local_shipyard` — Callable[[], ShipyardSnapshot|None] reading the game's own
        Shipyard.json (ed/shipyard.py). Ground truth for the last-visited station's stock —
        Spansh's ships list is the CATALOG, so a contradicted candidate gets skipped. None
        (the default) disables the cross-check.
      * `stock_lookup` — the (system, station) -> current-stock callable (an
        `EdsmStockLookup` in the app) that confirms each candidate is REALLY selling the
        ship before it's spoken — the local veto generalized to unvisited stations. None
        (the default) skips verification, keeping tests offline and legacy behavior intact.
      * `resolve` / `search` / `clipboard` — pure/offline deps, defaulted to the real ones
        but overridable in tests.
    """
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "search"

    def __init__(
        self,
        config: NavConfig,
        *,
        http: Http | None = None,
        get_current_system: Callable[[], str | None] | None = None,
        get_current_ship_size: Callable[[], str | None] | None = None,
        get_local_shipyard: Callable[[], object | None] | None = None,
        stock_lookup: Callable[[str, str], object | None] | None = None,
        resolve: Callable[..., object] = _default_resolve_ship,
        search: Callable[..., object] = find_closest_ship,
        clipboard: Callable[[str], None] = _default_copy,
        ship_index: object | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._current_ship_size = get_current_ship_size
        self._local_shipyard = get_local_shipyard
        self._stock_lookup = stock_lookup
        self._resolve = resolve
        self._search = search
        self._clipboard = clipboard
        # Optional live roster: surfaces ship names Spansh knows that the bundle is missing
        # (newly-released hulls), folded into resolution so a new ship is findable with no code
        # change. None -> bundled roster only (the default; keeps tests offline).
        self._ship_index = ship_index
        self._log = log
        self._logline = _make_logline(log)

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{
            "name": _SHIP_TOOL,
            "description": _SHIP_DESC,
            "input_schema": {"type": "object", "properties": dict(_SHIP_SCHEMA_PROPS),
                             "required": ["ship"]},
        }]

    def help_meta(self) -> HelpMeta:
        """Describe this capability for the help subsystem. Templated help is projected from
        this — nothing here is generated by an LLM."""
        return HelpMeta(
            category="shipyards",
            group="navigation and search",
            one_liner=("I find the closest station selling a given ship and copy that system "
                       "to your clipboard."),
            example="find the closest Anaconda",
            slots=(
                Slot(param="ship",
                     phrasings=("the ship name", "the ship"),
                     example="where can I buy a Python",
                     help_text="Name the ship you want — an Anaconda, a Python, a Krait, a "
                               "Type-9, and so on. If it's a family like Krait or Cobra, I'll "
                               "ask which model."),
                Slot(param="pad_size",
                     phrasings=("a landing pad size", "a pad size"),
                     example="somewhere with a large pad",
                     help_text="Restrict to stations with a given landing-pad size — small, "
                               "medium, or large — or say 'match my ship' to use whatever "
                               "ship you're currently flying for this one search."),
            ),
            help_when_active=("Tell me the ship — and which model if I ask — and I'll find the "
                              "nearest station that sells it."),
        )

    def help_vocabulary(self) -> dict[str, list[str]]:
        """The canonical ship names help's failure-recovery mode matches an unresolved term
        against, so a suggested correction is always a real ship (never invented)."""
        return {"ship": list(SHIP_NAMES)}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _SHIP_TOOL:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Ship lookup error: {e}"

    # -- dialog -----------------------------------------------------------------------
    def _handle(self, inp: dict) -> str:
        ship = str(inp.get("ship") or "").strip()
        if not ship:
            return "Which ship should I find the closest station for?"

        # Fold in any hulls the live index learned Spansh knows but the bundle doesn't. Only
        # pass the kwarg when there ARE extras, so the injected resolve seam stays simple.
        extra = _live_extra_names(self._ship_index, self._logline, "ship index")
        outcome = self._resolve(ship, extra_names=extra) if extra else self._resolve(ship)

        if isinstance(outcome, UnknownShip):
            return _say_unknown(outcome, "ship")
        if isinstance(outcome, AmbiguousShip):
            return _say_ambiguous(outcome, "ships")
        if isinstance(outcome, ResolvedShip):
            return self._do_search(outcome, inp)
        return "I couldn't interpret that ship — try naming it another way."

    def _do_search(self, resolved: ResolvedShip, inp: dict) -> str:
        """The one networked step — fires as soon as a ship resolves to a single model."""
        system = self._current_system() if self._current_system is not None else None
        pad = _pad_constraint(inp, self._cfg.default_pad_size, self._current_ship_size)
        # Only pass the stock seam when it exists, so injected `search` fakes without the
        # kwarg keep working (the extra_names pattern).
        kwargs: dict = {}
        if self._stock_lookup is not None:
            kwargs["stock_lookup"] = self._stock_lookup
        try:
            result = self._search(
                resolved, system, self._http,
                pad_size=pad,
                base_url=self._cfg.base_url,
                user_agent=self._cfg.user_agent,
                search_size=self._cfg.search_size,
                local_shipyard=self._read_local_shipyard(),
                **kwargs,
            )
        except NavError as e:
            self._logline(f"search failed for {resolved.label}: {e}")
            return str(e)

        # Copy the SYSTEM name unless the station is in the Commander's current system (N3
        # rule): you're already there, so copying your own system just clobbers the clipboard.
        copied, here = sup.deliver_system(self._clipboard, result.system, result.distance_ly,
                                          self._log)
        stock = ("confirmed" if result.extra.get("stock_verified")
                 else "unverified" if result.extra.get("stock_unverified") else "unchecked")
        self._logline(f"nearest {resolved.label}: {result.station} in {result.system} "
                      f"({result.distance_ly:.1f} ly), pad {result.pad}, stock={stock}, "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        return self._say_result(resolved, result, copied, here)

    def _say_result(self, resolved: ResolvedShip, result, copied: bool, here: bool) -> str:
        line = ""
        # A nearer station was contradicted — by the Commander's own shipyard visit, or by
        # current stock data (EDSM). Say why it isn't the answer before naming the one that
        # is; at most ONE skip note, this is spoken aloud.
        skipped = result.extra.get("skipped_local")
        if skipped:
            line += (f"Spansh lists it at {skipped}, but the shipyard you visited there "
                     f"doesn't currently stock it. ")
        elif result.extra.get("skipped_stock"):
            line += (f"Spansh lists it nearer at {result.extra['skipped_stock']}, but "
                     f"current stock data says it isn't actually available there. ")
        line += (f"Closest {resolved.label}: {result.station} in {result.system}, "
                 f"{sup.distance_phrase(result.distance_ly)}. Largest pad {result.pad}.")
        arrival = result.extra.get("distance_to_arrival")
        if isinstance(arrival, (int, float)) and arrival >= 1:
            line += f" About {arrival:,.0f} light-seconds from the star."
        price = result.extra.get("ship_price")
        if isinstance(price, (int, float)) and price >= 1:
            line += f" It runs about {price:,.0f} credits."
        # Present only when the answer came from the stale fallback (nothing fresh matched).
        line += sup.stale_note(result.extra.get("stock_age_days"), what="that listing",
                               risk="shipyard stock rotates and it may be gone")
        # The stock check couldn't confirm this one (no data / source down) — say so rather
        # than imply the certainty a confirmed answer has.
        if result.extra.get("stock_unverified"):
            line += " I couldn't verify live stock for this one, so no guarantees."
        return line + sup.clipboard_note(result.system, copied, here)

    # -- helpers ----------------------------------------------------------------------
    def _read_local_shipyard(self):
        """The Commander's own Shipyard.json snapshot, or None. Fail-soft — the cross-check
        is a bonus; a broken reader never blocks a lookup."""
        if self._local_shipyard is None:
            return None
        try:
            return self._local_shipyard()
        except Exception as e:  # noqa: BLE001 — ground truth is a bonus, never fatal
            self._logline(f"local shipyard snapshot unavailable: {e}")
            return None
