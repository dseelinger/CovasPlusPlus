"""Station location via Spansh's live station search (the ONE networked step).

Only ever called with a RESOLVED + confirmed module (see the capability): find the nearest
station selling it, sorted by distance from the Commander's current system.

Spansh quirks, verified against the live API (2026-07) and cross-checked against how the
established tools call it (EDDiscovery's `SpanshClassStation`, corenting/ED-API,
RatherRude/Elite-Dangerous-AI-Integration) — this is why the request/parsing look the way
they do:
  * POST https://spansh.co.uk/api/stations/search is SYNCHRONOUS — it returns the results
    array directly (no job-id/poll step, despite the shareable /search/<uuid> result URLs;
    the job/poll `search/save` + `search/recall/<ref>/<page>` variant exists but isn't
    needed here). EDDiscovery reads `json["results"]` straight off the POST — same as us.
  * The module filter only honours `name`, `class`, `rating`. `ed_symbol`, `weapon_mode` /
    `mount`, and the top-level `landing_pad` key are SILENTLY IGNORED (a bogus value returns
    everything, not nothing). So MOUNT can't be filtered server-side — we send name+class,
    then POST-FILTER results by `weapon_mode` (each result carries the station's full
    `modules` list). PAD, however, IS filterable via the boolean `has_large_pad` /
    `has_medium_pad` / `has_small_pad` filters (`{"value": true}`) — the form EDDiscovery
    uses — so we push the pad constraint server-side and keep a client check as a backstop.
  * Fleet carriers ("Drake-Class Carrier") show up in results and often dominate near busy
    systems, but their location is TRANSIENT (they jump) — a stale answer. EDDiscovery and
    RatherRude both exclude them; we drop them from results so "nearest station" means a
    fixed one you can actually fly to.
  * An unrecognised `reference_system` → HTTP 400 {"error":"Invalid request"} (generic body,
    so we can't distinguish it from other 400s — we message it as an unknown-system likely).
  * `distance` is light-years from the reference system; results come pre-sorted ascending
    when we ask, so the first post-filter survivor is the nearest.

`http` is injected (a `RequestsHttp` in the app, a fake in tests) so the default test run
never hits the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Nearest-station search window. We fetch this many nearest stations that sell the module
# (name+class, pad filtered server-side), then post-filter locally by mount + carrier. Amply
# covers the case where the very nearest station stocks the module but not the wanted mount.
_SEARCH_SIZE = 50
_DEFAULT_BASE_URL = "https://spansh.co.uk/api/stations/search"
_DEFAULT_UA = "COVAS-Plus-Plus/0.1 (Elite Dangerous voice companion; +https://github.com/)"

# Station types dropped from results: a fleet carrier can sell a module but it jumps around,
# so it's a poor "nearest station to go buy X" answer (EDDiscovery/RatherRude exclude them too).
_EXCLUDED_STATION_TYPES = frozenset({"Drake-Class Carrier"})

# pad size -> the Spansh boolean pad filter that means "has a pad at least this big". A large
# starport also has medium/small pads, so has_medium_pad true correctly includes it.
_PAD_FILTER_KEY = {"L": "has_large_pad", "M": "has_medium_pad", "S": "has_small_pad"}


class NavError(Exception):
    """A lookup that couldn't produce an answer. `str(e)` is a short, spoken-friendly line —
    the capability returns it to the LLM verbatim (fail soft; the voice loop stays alive)."""


class Http(Protocol):
    """Tiny injected HTTP seam — one method, so tests pass a fake and the default run is
    hermetic (DESIGN §9)."""
    def post_json(self, url: str, payload: dict, *, headers: dict | None = None,
                  timeout: float = 20.0) -> tuple[int, object]:
        """POST `payload` as JSON; return (status_code, parsed_json_or_None)."""
        ...


@dataclass(frozen=True)
class ClosestResult:
    """The nearest matching station. `extra` carries the softer details the spoken line may
    mention (arrival distance, station type, mount confirmation)."""
    system: str
    station: str
    distance_ly: float
    pad: str                     # "L" | "M" | "S" — the largest pad the station has
    extra: dict = field(default_factory=dict)


# ---- pad logic (pure) ---------------------------------------------------------------------

_PAD_RANK = {"S": 1, "M": 2, "L": 3}


def _largest_pad(result: dict) -> str | None:
    """The biggest landing pad a station has, from Spansh's pad fields."""
    if result.get("has_large_pad") or (result.get("large_pads") or 0) > 0:
        return "L"
    if (result.get("medium_pads") or 0) > 0:
        return "M"
    if (result.get("small_pads") or 0) > 0:
        return "S"
    return None


def _pad_ok(result: dict, need: str | None) -> bool:
    """Does the station have a pad big enough for a ship that needs `need` (S/M/L)? A larger
    pad accommodates a smaller ship, so 'need M' is satisfied by an M or L pad. `need` None
    (or unknown) means don't care."""
    if not need:
        return True
    want = _PAD_RANK.get(str(need).strip().upper()[:1])
    if want is None:
        return True
    have = _largest_pad(result)
    return have is not None and _PAD_RANK[have] >= want


def _sells_mount(result: dict, module_name: str, size: int | None, mount: str | None) -> bool:
    """Does this station's outfitting actually include the wanted module VARIANT? Confirms
    the mount (Spansh can't filter it), and re-checks name/class as a belt-and-braces guard."""
    if mount is None:
        return True
    for m in result.get("modules") or []:
        if m.get("name") != module_name:
            continue
        if size is not None and m.get("class") != size:
            continue
        if m.get("weapon_mode") == mount:
            return True
    return False


# ---- query build + parse ------------------------------------------------------------------

def _pad_filter_key(pad_size: str | None) -> str | None:
    """The Spansh boolean pad-filter key for a required pad size (S/M/L), or None."""
    if not pad_size:
        return None
    return _PAD_FILTER_KEY.get(str(pad_size).strip().upper()[:1])


def build_payload(resolved, current_system: str, *, pad_size: str | None = None,
                  size: int = _SEARCH_SIZE) -> dict:
    """The Spansh station-search request body. Module name+class and the PAD constraint go
    server-side; MOUNT is not sent (Spansh ignores it — see the module docstring) and is
    applied when parsing results."""
    module_filter: dict = {"name": resolved.name}
    if resolved.size is not None:
        module_filter["class"] = [str(resolved.size)]
    filters: dict = {"modules": [module_filter]}
    pad_key = _pad_filter_key(pad_size)
    if pad_key is not None:
        filters[pad_key] = {"value": True}
    return {
        "filters": filters,
        "sort": [{"distance": {"direction": "asc"}}],
        "size": int(size),
        "page": 0,
        "reference_system": current_system,
    }


def _nearest_match(results: list[dict], resolved, pad_size: str | None) -> dict | None:
    """First (nearest — results are distance-sorted) fixed station that stocks the wanted
    mount variant and has a big-enough pad. Pad is already filtered server-side; the
    `_pad_ok` check is a backstop, and fleet carriers are dropped as transient."""
    for r in results:
        if r.get("type") in _EXCLUDED_STATION_TYPES:
            continue
        if not _sells_mount(r, resolved.name, resolved.size, resolved.mount):
            continue
        if not _pad_ok(r, pad_size):
            continue
        return r
    return None


def _to_result(r: dict) -> ClosestResult:
    extra = {
        "distance_to_arrival": r.get("distance_to_arrival"),
        "station_type": r.get("type"),
        "is_planetary": r.get("is_planetary"),
    }
    return ClosestResult(
        system=r.get("system_name") or "an unknown system",
        station=r.get("name") or "an unknown station",
        distance_ly=float(r.get("distance") or 0.0),
        pad=_largest_pad(r) or "?",
        extra={k: v for k, v in extra.items() if v is not None},
    )


def find_closest_module(resolved, current_system: str, http: Http, *,
                        pad_size: str | None = None,
                        base_url: str = _DEFAULT_BASE_URL,
                        user_agent: str = _DEFAULT_UA,
                        search_size: int = _SEARCH_SIZE) -> ClosestResult:
    """Nearest station selling `resolved`, from `current_system`, via Spansh (`http` injected).

    Raises NavError (with a spoken-friendly message) on: no current system, a Spansh error /
    unreachable API, no station selling the module, or none within the search window that has
    the wanted mount + a big-enough pad. Returns a ClosestResult on success."""
    if not current_system or not str(current_system).strip():
        raise NavError("I don't know your current system yet — is Elite Dangerous running "
                       "with monitoring on? Jump somewhere and I'll have it.")

    payload = build_payload(resolved, current_system, pad_size=pad_size, size=search_size)
    headers = {"Content-Type": "application/json", "User-Agent": user_agent}
    try:
        status, body = http.post_json(base_url, payload, headers=headers, timeout=20.0)
    except Exception as e:  # noqa: BLE001 — any transport failure degrades to a spoken note
        raise NavError(f"I couldn't reach the station database just now ({e}). Try again in "
                       "a moment.") from e

    if status == 400:
        raise NavError(f"The station database didn't recognise your current system "
                       f"'{current_system}'. If you just jumped, give it a second.")
    if status != 200 or not isinstance(body, dict):
        raise NavError(f"The station lookup failed (HTTP {status}). Try again shortly.")

    results = body.get("results") or []
    if not results:
        # Pad is filtered server-side, so an empty result under a pad constraint usually means
        # "none with a big-enough pad" rather than "the module doesn't exist".
        if pad_size:
            raise NavError(f"I couldn't find a station with a {pad_size} pad selling a "
                           f"{resolved.label} near you — try relaxing the pad size.")
        raise NavError(f"I couldn't find any station selling a {resolved.label} — it may be "
                       "rare or unavailable near you.")

    match = _nearest_match(results, resolved, pad_size)
    if match is None:
        # Stations sell the module, but none in-window fit the mount/pad constraint.
        pad_note = f" with a {pad_size} pad" if pad_size else ""
        raise NavError(f"I found stations selling that module, but none nearby stock the "
                       f"{resolved.label}{pad_note}. Try relaxing the pad size or mount.")
    return _to_result(match)


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
