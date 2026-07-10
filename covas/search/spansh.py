"""Category-agnostic Spansh transport — the reusable half of the old `nav/closest.py`.

Everything here is true of EVERY Spansh search (stations, systems, bodies), verified against
the live API (2026-07) and cross-checked against how the established tools call it
(EDDiscovery, corenting/ED-API, RatherRude/Elite-Dangerous-AI-Integration):

  * POST https://spansh.co.uk/api/<type>/search is SYNCHRONOUS — the results array comes back
    on the POST (no job-id/poll step). EDDiscovery reads `json["results"]` straight off the
    POST; so do we (`execute_search`).
  * Every filter value is STRUCTURED: an enum/multi-select is `{"value": [...]}`, a range is
    `{"min": "...", "max": "..."}`, a boolean is `{"value": true|false}`. A BARE value (e.g.
    `"allegiance": ["Federation"]`) is rejected with HTTP 400 {"error":"Invalid request"}.
  * An UNKNOWN filter key, sent with a valid structure, is SILENTLY IGNORED (returns
    everything, no error) — Spansh does not tell you that you misspelled a param. That single
    fact is why `categories.py` validates params itself and fails LOUD: a typo'd or drifted
    param would otherwise widen a search without any signal.
  * An unrecognised `reference_system` -> HTTP 400 with the same generic body, so we can't tell
    it apart from other 400s — we message it as an unknown-system likely.
  * `distance` is light-years from the reference system; results come pre-sorted ascending
    when we ask (`distance_sort`), so the first survivor is the nearest.
  * Fleet carriers show up in station results and often dominate near busy systems, but their
    location is TRANSIENT (they jump) — a stale answer. EDDiscovery and RatherRude exclude
    them; so do we (`is_fleet_carrier`).

`http` is injected (a `RequestsHttp` in the app, a fake in tests) so the default test run
never hits the network (DESIGN §9).
"""
from __future__ import annotations

from typing import Protocol

# Spansh search endpoints (the three real POST /search targets; bodies is a seam for now).
_BASE = "https://spansh.co.uk/api"
SYSTEMS_URL = f"{_BASE}/systems/search"
STATIONS_URL = f"{_BASE}/stations/search"
BODIES_URL = f"{_BASE}/bodies/search"

_DEFAULT_UA = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion; +https://github.com/)"

# Station types dropped from station results: a fleet carrier can sell/offer a service but it
# jumps around, so it's a poor "nearest X to fly to" answer (EDDiscovery/RatherRude exclude
# them too). Kept as a set so more transient types can be added in one place.
EXCLUDED_STATION_TYPES = frozenset({"Drake-Class Carrier"})

# pad size -> the Spansh boolean pad filter that means "has a pad at least this big". A large
# starport also has medium/small pads, so has_medium_pad true correctly includes it.
PAD_FILTER_KEY = {"L": "has_large_pad", "M": "has_medium_pad", "S": "has_small_pad"}
PAD_RANK = {"S": 1, "M": 2, "L": 3}


class NavError(Exception):
    """A lookup that couldn't produce an answer. `str(e)` is a short, spoken-friendly line —
    the capability returns it to the LLM verbatim (fail soft; the voice loop stays alive).

    Named `NavError` for continuity with the outfitting feature that first raised it; every
    category shares it so the capabilities catch one exception type."""


class Http(Protocol):
    """Tiny injected HTTP seam — one method, so tests pass a fake and the default run is
    hermetic (DESIGN §9)."""
    def post_json(self, url: str, payload: dict, *, headers: dict | None = None,
                  timeout: float = 20.0) -> tuple[int, object]:
        """POST `payload` as JSON; return (status_code, parsed_json_or_None)."""
        ...


# ---- request helpers ----------------------------------------------------------------------

def distance_sort() -> list[dict]:
    """The one sort every category uses: nearest first, so results[0] is the closest match."""
    return [{"distance": {"direction": "asc"}}]


def execute_search(url: str, payload: dict, http: Http, *,
                   user_agent: str = _DEFAULT_UA, timeout: float = 20.0,
                   reference_system: str | None = None,
                   subject: str = "the galaxy database",
                   lookup_name: str = "search") -> list[dict]:
    """POST `payload` to a Spansh search endpoint and return its `results` list (possibly
    empty — an empty result set is category-specific to interpret, so we don't raise on it).

    Raises `NavError` (spoken-friendly) on a transport failure, an HTTP 400 (most often an
    unrecognised `reference_system`), or any other non-200 / non-JSON response. `subject` and
    `lookup_name` only tune the wording so each category can name itself ("the station
    database" / "station lookup")."""
    headers = {"Content-Type": "application/json", "User-Agent": user_agent}
    try:
        status, body = http.post_json(url, payload, headers=headers, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — any transport failure degrades to a spoken note
        raise NavError(f"I couldn't reach {subject} just now ({e}). Try again in "
                       "a moment.") from e

    if status == 400:
        ref = f" '{reference_system}'" if reference_system else ""
        raise NavError(f"{subject.capitalize()} didn't recognise your current system{ref}. "
                       f"If you just jumped, give it a second.")
    if status != 200 or not isinstance(body, dict):
        raise NavError(f"The {lookup_name} failed (HTTP {status}). Try again shortly.")

    results = body.get("results")
    return results if isinstance(results, list) else []


# ---- landing-pad logic (pure) -------------------------------------------------------------

def largest_pad(result: dict) -> str | None:
    """The biggest landing pad a station has, from Spansh's pad fields ("L"/"M"/"S"), or None."""
    if result.get("has_large_pad") or (result.get("large_pads") or 0) > 0:
        return "L"
    if (result.get("medium_pads") or 0) > 0:
        return "M"
    if (result.get("small_pads") or 0) > 0:
        return "S"
    return None


def pad_ok(result: dict, need: str | None) -> bool:
    """Does the station have a pad big enough for a ship that needs `need` (S/M/L)? A larger
    pad accommodates a smaller ship, so 'need M' is satisfied by an M or L pad. `need` None
    (or unknown) means don't care."""
    if not need:
        return True
    want = PAD_RANK.get(str(need).strip().upper()[:1])
    if want is None:
        return True
    have = largest_pad(result)
    return have is not None and PAD_RANK[have] >= want


def pad_filter_key(pad_size: str | None) -> str | None:
    """The Spansh boolean pad-filter key for a required pad size (S/M/L), or None."""
    if not pad_size:
        return None
    return PAD_FILTER_KEY.get(str(pad_size).strip().upper()[:1])


# ---- station helpers ----------------------------------------------------------------------

def is_fleet_carrier(result: dict) -> bool:
    """A transient fleet carrier that should be dropped from station results (it jumps)."""
    return result.get("type") in EXCLUDED_STATION_TYPES


# ---- real HTTP (used by the app; never in the default test run) ---------------------------

class RequestsHttp:
    """Production Http: a thin `requests` wrapper. Built only at the app composition root, so
    unit tests inject a fake instead and the default `pytest` never imports/needs the network."""

    def post_json(self, url: str, payload: dict, *, headers: dict | None = None,
                  timeout: float = 20.0) -> tuple[int, object]:
        import requests  # local import: keeps the offline stack importable without hitting it
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        try:
            body: object = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body
