"""Body / bio-geo signal finder (issue #68) — "find the nearest Earth-like world", "the closest
body with Bacterium signals".

The SEVENTH Spansh search category and the first over the `bodies/search` endpoint. Same
stateless, LLM-native shape as the star-systems / stations reference: the conversation is the
state, the model fills slots and refines by re-calling, no classifier or state machine. A
TARGETED single-body lookup — it complements the Road-to-Riches ROUTE planner (#42): R2R plans a
whole scan run, this points you at ONE body ("where's the nearest ammonia world / body with
Aleoida?"). The nearest match's SYSTEM is copied to the clipboard for the galaxy map (there is no
per-body plot; you plot the system, then fly in).

Two vocabularies drive it (see `search/bodies.py`, baked from the live API and verified to
NARROW results): body `subtype` (Earth-like / Ammonia / Water world, gas giants, …) and the
biological signal `landmark_subtype` (the Odyssey exobiology species — a genus like "Bacterium"
expands to an OR over its species; "any biological" spans the whole catalogue). Enum values are
validated against that vocabulary before the query, so a mishear is corrected, never invented.

Beats the competitors: EDCoPilot/COVAS:NEXT can read a body's data once you're there; COVAS++
FINDS the nearest body matching a type/biology from anywhere and hands its system to the galaxy
map off one spoken command.

I/O (`http`, `get_current_system`, `clipboard`) is injected so the default `pytest` run is
offline (DESIGN §9); fail soft throughout — a bad value or a failed lookup is spoken, never raised.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from ..nav import copy as _default_copy
from ..search import RequestsHttp, parse_bodies
from ..search.bodies import BIO_GENUS_NAMES, BODY_SUBTYPES, nearest_bio_signal, nearest_subtype, \
    resolve_bio_signal, resolve_subtype
from ..search.categories import category
from ..search.spansh import Http, data_age_days
from . import _search_support as sup
from ._search_support import SearchConfig
from .base import HelpMeta, Slot

_TOOL_NAME = "search_bodies"

# A biological-signal record older than this gets a gentle "that scan is N days old" caveat —
# the signal/landmark data is the crowdsourced (so ageable) part of a body; the structure isn't.
_BIO_STALE_DAYS = 30

_DESC = (
    "Find the nearest Elite Dangerous BODY (planet or moon) by its type or its biological "
    "signals, from the Commander's current system, and copy the body's SYSTEM name to the "
    "clipboard for the galaxy map. Answers 'nearest Earth-like world', 'closest ammonia / water "
    "world', or 'nearest body with <biology> signals'.\n"
    "ROUTING: this is a SINGLE-body lookup. If they want a whole exploration/credit RUN of "
    "systems to scan, use plan_riches_route (Road to Riches) instead.\n"
    "It is LLM-native and STATELESS:\n"
    "1. Fill only the slots the Commander said; unspoken means Any. `body_type` is a body class "
    "(Earth-like world, Ammonia world, Water world, High metal content world, a gas-giant class, "
    "…). `biological_signal` is an exobiology genus ('Bacterium', 'Stratum', 'Tussock'), a "
    "specific species ('Bacterium Aurasus'), or 'any biological'. Optionally `landable`, or "
    "`max_arrival_distance` in light-seconds.\n"
    "2. If a type or biology is unclear, relay the tool's suggested correction — don't invent "
    "one.\n"
    "3. Refine by re-calling with the accumulated slots (each call RE-QUERIES). On 'cancel' / "
    "'never mind', drop it and do NOT call this tool. Report the body, its system, distance, and "
    "distance from the star, and ALWAYS say the system name was copied to the clipboard."
)

_SCHEMA_PROPS = {
    "body_type": {"type": "string",
                  "description": "Body class to find, e.g. Earth-like world, Ammonia world, "
                                 "Water world, High metal content world, Class II gas giant."},
    "biological_signal": {"type": "string",
                          "description": "Biological signal to require: an exobiology genus "
                                         "(Bacterium, Stratum, Tussock, Aleoida, …), a specific "
                                         "species (Bacterium Aurasus), or 'any biological'."},
    "landable": {"type": "boolean",
                 "description": "Set true to require a landable body (needed to scan surface "
                                "biology on foot). Omit for any."},
    "max_arrival_distance": {"type": "integer",
                             "description": "Maximum distance from the main star, in "
                                            "light-seconds. Omit for any."},
    "near": {"type": "string",
             "description": "Reference system to measure from. Omit to use the current system."},
}


class BodySearchCapability:
    """Advertises `search_bodies`; stateless slot-fill -> validate -> search over the shared
    Spansh client. Injected seams (`http`, `get_current_system`, `clipboard`) keep the default
    test run offline."""
    # Tiering group (issue #84): the token-budget cluster this capability's tools belong
    # to; the level filter (covas/tiering.py) keeps or drops the whole group as a unit.
    TIERING_GROUP = "search"

    def __init__(self, config: SearchConfig, *, http: Http | None = None,
                 get_current_system: Callable[[], str | None] | None = None,
                 clipboard: Callable[[str], None] = _default_copy,
                 log: Callable[[str], None] | None = None,
                 now: Callable[[], datetime] | None = None) -> None:
        self._cfg = config
        self._http = http if http is not None else RequestsHttp()
        self._current_system = get_current_system
        self._clipboard = clipboard
        self._log = log
        self._now = now if now is not None else (lambda: datetime.now(timezone.utc))
        self._spec = category("bodies")

    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": dict(_SCHEMA_PROPS),
                                  "required": []}}]

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="bodies",
            group="navigation and search",
            one_liner=("I find the nearest body — an Earth-like world, ammonia or water world, or "
                       "one with a given biological signal — and copy its system to your "
                       "clipboard for the galaxy map."),
            example="find the nearest Earth-like world",
            slots=(
                Slot(param="subtype", phrasings=("a body type", "a kind of world"),
                     example="the closest ammonia world",
                     help_text="Name the body class — Earth-like world, ammonia world, water "
                               "world, high metal content world, or a gas-giant class."),
                Slot(param="landmark_subtype",
                     phrasings=("a biological signal", "a type of biology"),
                     example="the nearest body with Bacterium",
                     help_text="Require a biological signal — an exobiology genus like Bacterium "
                               "or Stratum, a specific species, or 'any biological'."),
                Slot(param="is_landable", phrasings=("landable", "one I can land on"),
                     example="a landable body with Aleoida",
                     help_text="Require a landable body — needed to scan surface biology on foot."),
                Slot(param="distance_to_arrival",
                     phrasings=("close to the star", "distance from the star"),
                     example="an Earth-like world close to the star",
                     help_text="Restrict to bodies within a distance of the main star."),
            ),
            help_when_active=("Tell me a body type — like an Earth-like world — or a biological "
                              "signal like Bacterium, and I'll find the nearest one."),
        )

    def help_vocabulary(self) -> dict[str, list[str]]:
        return {"body type": list(BODY_SUBTYPES), "biological signal": list(BIO_GENUS_NAMES)}

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        try:
            return self._handle(inp)
        except Exception as e:  # noqa: BLE001 — the voice loop must survive any tool error
            self._logline(f"error: {e}")
            return f"Body search error: {e}"

    def _handle(self, inp: dict) -> str:
        slots: dict[str, object] = {}
        caught: list[str] = []          # values understood so far, echoed on a later bad slot
        bio_search = False              # whether biology was requested (drives the age caveat)

        btype = inp.get("body_type")
        if btype not in (None, ""):
            val = resolve_subtype(btype)
            if val is None:
                return sup.recovery(btype, "body type", nearest_subtype(btype), caught=caught)
            slots["subtype"] = val
            caught.append(val)

        bio = inp.get("biological_signal")
        if bio not in (None, ""):
            species = resolve_bio_signal(bio)
            if species is None:
                return sup.recovery(bio, "biological signal", nearest_bio_signal(bio),
                                    caught=caught)
            slots["landmark_subtype"] = species
            bio_search = True
            # Echo the spoken term, not the (possibly long) species list, so the caught-so-far
            # line stays readable when a LATER slot is the one that fails.
            caught.append(str(bio))

        if inp.get("landable") is True:
            slots["is_landable"] = True
            caught.append("landable")

        max_ls = inp.get("max_arrival_distance")
        if max_ls is not None:
            try:
                slots["distance_to_arrival"] = {"max": int(max_ls)}
            except (TypeError, ValueError):
                return "I didn't catch the distance from the star — say it in light-seconds."

        if not slots:
            return ("Tell me what to look for — a body type like an Earth-like world, or a "
                    "biological signal like Bacterium. (Say 'never mind' to drop it.)")

        return self._search(slots, inp, bio_search=bio_search)

    def _search(self, slots: dict, inp: dict, *, bio_search: bool) -> str:
        system = sup.reference_system(self._current_system, inp)
        if not system:
            return ("I don't know your current system yet — is Elite Dangerous running with "
                    "monitoring on? Jump somewhere, or tell me a system to search near.")
        try:
            results = sup.run_query(self._spec, slots, self._http, system,
                                    user_agent=self._cfg.user_agent, size=self._cfg.search_size)
        except sup.NavError as e:
            self._logline(f"search failed: {e}")
            return str(e)

        bodies = parse_bodies(results)
        if not bodies:
            return ("I couldn't find a body matching that near you — try a different type, a "
                    "broader biology, or relaxing a filter.")
        best = bodies[0]
        copied, here = sup.deliver_system(self._clipboard, best.system, best.distance_ly, self._log)
        self._logline(f"nearest body: {best.name} ({best.subtype}) in {best.system} "
                      f"({best.distance_ly:.1f} ly), filters={sorted(slots)}, "
                      f"clipboard={'here' if here else ('ok' if copied else 'failed')}")
        return self._say(best, copied, here, bio_search=bio_search)

    def _say(self, rec, copied: bool, here: bool, *, bio_search: bool) -> str:
        what = rec.subtype or "body"
        line = f"Closest {what}: {rec.name} in {rec.system}, {sup.distance_phrase(rec.distance_ly)}."
        if rec.distance_to_arrival_ls is not None and rec.distance_to_arrival_ls >= 1:
            line += f" About {rec.distance_to_arrival_ls:,.0f} light-seconds from the star."
        if rec.is_landable:
            line += " It's landable."
        if bio_search:
            bio_count = rec.signals.get("Biological")
            if isinstance(bio_count, int) and bio_count > 0:
                sig = "signal" if bio_count == 1 else "signals"
                line += f" {bio_count} biological {sig}."
            if rec.landmarks:
                line += f" Confirmed: {sup.or_list(list(rec.landmarks)[:3])}."
        return line + sup.clipboard_note(rec.system, copied, here) + self._age_caveat(rec, bio_search)

    def _age_caveat(self, rec, bio_search: bool) -> str:
        """A gentle caveat when a BIO search lands on an old record — the signal data is
        crowdsourced, so a very stale body may have been re-surveyed since. Structure searches
        (subtype etc.) don't age, so they get no caveat."""
        if not bio_search:
            return ""
        stamp = rec.extra.get("signals_updated_at") or rec.extra.get("updated_at")
        age = data_age_days({"_": stamp}, "_", now=self._now())
        if age is None or age < _BIO_STALE_DAYS:
            return ""
        return sup.stale_note(age, what="that biology scan",
                              risk="the surface may have been re-surveyed since")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
