"""Shared plumbing for the LLM-native Spansh search capabilities (Search Prompts 4–5).

The Spansh search categories all repeat the same non-domain steps: work out the reference
system, build+run the query over the shared client, copy the primary system to the clipboard,
and phrase small spoken fragments. Those live here so each category stays focused on its slots,
its vocabulary, and its result sentence — the parts that actually differ.

Nothing here is domain-specific: no slot names, no vocabularies. Every spec-driven category
(`capabilities/search_family.py`, issue #111) shares these; the star-systems inline copies
were folded in when that family collapsed. One deliberate holdout: the find-closest MODULE
tool keeps its inline clipboard/distance fragments — its frozen result line says "in your
current system" where `distance_phrase` says "your current system", and #111 forbids
changing a spoken byte.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from ..search import NavError, build_query, execute_search
from ..search.categories import CategorySpec
from ..search.spansh import (BGS_MAX_AGE_DAYS, _DEFAULT_UA, data_age_days, freshness_filter,
                             is_fresh)


@dataclass(frozen=True)
class SearchConfig:
    """Immutable snapshot of a `[search]`-style section. Off by default; the four Prompt-5
    categories (stations, minor factions, signals, misc) share one `[search]` section, so a
    single toggle enables the group."""
    enabled: bool = False
    user_agent: str = _DEFAULT_UA
    search_size: int = 50

    @classmethod
    def from_cfg(cls, cfg: dict, section: str = "search") -> "SearchConfig":
        s = cfg.get(section, {}) or {}
        d = cls()
        return cls(
            enabled=bool(s.get("enabled", False)),
            user_agent=str(s.get("user_agent", d.user_agent) or d.user_agent),
            search_size=int(s.get("search_size", d.search_size) or d.search_size),
        )


def reference_system(get_current_system: Callable[[], str | None] | None, inp: dict,
                     *, arg: str = "near") -> str | None:
    """The system to measure from: a spoken `near` override, else the Commander's current
    system (the injected getter — ED context with a journal fallback), else None."""
    near = inp.get(arg)
    if near and str(near).strip():
        return str(near).strip()
    return get_current_system() if get_current_system is not None else None


def near_override(inp: dict, *, arg: str = "near") -> bool:
    """True when the Commander gave an explicit `near X` reference override, so the search was
    measured from X rather than their current system. This distinguishes "the query target
    itself" from "the Commander's current location": a distance-0 top match under an override
    means the answer IS X (worth copying — the Commander may be elsewhere), whereas a distance-0
    match with NO override means they are actually already there (nothing to copy)."""
    near = inp.get(arg)
    return bool(near and str(near).strip())


def run_query(spec: CategorySpec, slots: dict, http, reference: str, *,
              user_agent: str, size: int) -> list[dict]:
    """Build the category's query (fails LOUD on an unknown param), POST it, and return the raw
    results list. Raises `NavError` (spoken-friendly) on a Spansh/transport failure."""
    payload = build_query(spec, slots, reference, size=size)
    return execute_search(spec.endpoint, payload, http, user_agent=user_agent,
                          reference_system=reference, subject=spec.subject,
                          lookup_name=spec.lookup_name)


def run_query_fresh(spec: CategorySpec, slots: dict, http, reference: str, *,
                    user_agent: str, size: int, fresh_field: str,
                    fresh_within_days: int = BGS_MAX_AGE_DAYS,
                    now: datetime | None = None) -> tuple[list[dict], float | None]:
    """`run_query` with the staleness policy for VOLATILE facts (BGS states tick daily; see
    `spansh.py`): constrain `fresh_field` server-side (with a client backstop), and when
    nothing fresh matches, ONE retry without the window answers from stale data. Returns
    `(results, stale_age_days)` — the age is the nearest stale result's, None on the fresh
    path, so the capability knows whether to speak the caveat. `now` is injectable for tests."""
    today = now.astimezone(timezone.utc).date() if now is not None else None
    payload = build_query(spec, slots, reference, size=size)
    payload["filters"].update(freshness_filter(fresh_field, fresh_within_days, today=today))
    results = execute_search(spec.endpoint, payload, http, user_agent=user_agent,
                             reference_system=reference, subject=spec.subject,
                             lookup_name=spec.lookup_name)
    results = [r for r in results if is_fresh(r, fresh_field, fresh_within_days, today=today)]
    if results:
        return results, None

    results = run_query(spec, slots, http, reference, user_agent=user_agent, size=size)
    age = data_age_days(results[0], fresh_field, now=now) if results else None
    return results, age


def faction_or_recovery(index, spoken) -> tuple[str | None, str | None]:
    """Resolve a spoken minor-faction name to Spansh's EXACT string via the canonical faction
    index (Spansh's faction filter is exact-match, so a mishear -> 0 systems otherwise).

    Returns `(canonical_name, None)` on success; `(None, recovery_message)` when the index is
    loaded but the name doesn't resolve (the message names the nearest real factions, so we
    never search — and never let the model confabulate — on an unresolved name); and
    `(raw_name, None)` when the index couldn't be fetched, a fail-soft best-effort so faction
    search still works offline of the index."""
    canon = index.resolve(spoken)
    if canon:
        return canon, None
    if not index.loaded:                       # index unreachable -> best-effort raw name
        return str(spoken).strip(), None
    sugg = index.suggestions(spoken)
    if sugg:
        return None, f"I don't know a faction called '{spoken}'. Did you mean {or_list(sugg)}?"
    return None, (f"I couldn't find a minor faction called '{spoken}'. Check the name — it may "
                  f"be spelled differently, or not control any systems I can search.")


def copy_system(clipboard: Callable[[str], None], name: str,
                log: Callable[[str], None] | None = None) -> bool:
    """Copy a system name to the clipboard; never fatal (the answer is still spoken)."""
    try:
        clipboard(name)
        return True
    except Exception as e:  # noqa: BLE001 — clipboard is a convenience, never fatal
        if log is not None:
            log(f"clipboard copy failed: {e}")
        return False


def deliver_system(clipboard: Callable[[str], None], name: str, distance_ly: float,
                   log: Callable[[str], None] | None = None, *,
                   reference_is_current: bool = True) -> tuple[bool, bool]:
    """Copy the result system to the clipboard UNLESS the Commander is already at it.

    A distance of ~0 ly means the nearest match is the system we measured FROM. On the common
    path that reference IS the Commander's current system, so a ~0 match means they're already
    there — nothing to navigate to, nothing worth copying (copying your own system just clobbers
    the clipboard). But when the Commander searched `near X`, the reference is X, NOT where they
    are: a ~0 match is X itself and they may be light-years away, so it IS worth copying. Pass
    ``reference_is_current=False`` in that case (see `near_override`). Returns
    ``(copied, already_here)``: exactly one is ever true."""
    if reference_is_current and distance_ly < 0.05:
        return False, True
    return copy_system(clipboard, name, log), False


def clipboard_note(name: str, copied: bool, already_here: bool = False) -> str:
    """The trailing clipboard sentence. When the answer is the current system, say so and
    note that nothing was copied (paired with `deliver_system`)."""
    if already_here:
        return " You're already there, so I haven't copied anything."
    return (f" I've copied {name} to your clipboard." if copied
            else f" (Couldn't copy to the clipboard — the system is {name}.)")


def distance_phrase(distance_ly: float, *, reference_is_current: bool = True) -> str:
    """'your current system' when the Commander is on top of it, else 'N.N light-years away'.
    Only claims 'your current system' when the reference IS the current system (no `near X`
    override); under an override a ~0 match is the override target, not where they are."""
    return ("your current system" if (reference_is_current and distance_ly < 0.05)
            else f"{distance_ly:.1f} light-years away")


def stale_note(age_days: float | None, *, what: str = "that data",
               risk: str = "it may have changed since") -> str:
    """The trailing caveat for a stale-fallback answer ("Fair warning — that listing is 5 days
    old, so stock may have rotated."). Empty for the fresh path (`age_days` None), so callers
    can append it unconditionally."""
    if age_days is None:
        return ""
    days = max(1, round(age_days))
    unit = "day" if days == 1 else "days"
    return f" Fair warning — {what} is {days} {unit} old, so {risk}."


def a_an(word: str) -> str:
    """'a'/'an' for a spoken kind word ('an allegiance', 'a station type')."""
    return f"an {word}" if word[:1].lower() in "aeiou" else f"a {word}"


def recovery(term, kind: str, suggestion: str | None = None, *, caught=None) -> str:
    """The shared templated failure-recovery line — the search-side mirror of the help
    subsystem's error mode (Search Prompt 6). Names what wasn't recognized and, when there is
    one, the nearest REAL value; it never emits the unresolved term as if it were valid.

    `caught` (the values already understood THIS turn) is echoed first, so a single bad slot
    doesn't throw away the rest of the request — "I've got Empire, but I didn't recognize
    'zombie' as a faction state — did you mean War?" (the "echo what was caught, ask for what's
    missing" behavior)."""
    lead = f"I've got {or_list(caught)}, but " if caught else ""
    verb = "didn't recognize" if lead else "I didn't recognize"
    base = f"{lead}{verb} '{term}' as {a_an(kind)}"
    return f"{base} — did you mean {suggestion}?" if suggestion else \
           f"{base}. Try saying it another way."


def or_list(items) -> str:
    """Join options for speech: 'A', 'A or B', 'A, B, or C'."""
    items = [str(i) for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"


# Re-export so capability modules import their exception from one place.
__all__ = ["NavError", "SearchConfig", "reference_system", "near_override", "run_query",
           "run_query_fresh", "faction_or_recovery", "copy_system", "deliver_system",
           "clipboard_note", "distance_phrase", "stale_note", "a_an", "or_list", "recovery"]
