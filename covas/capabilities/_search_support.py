"""Shared plumbing for the LLM-native Spansh search capabilities (Search Prompts 4–5).

The five search capabilities (outfitting, star systems, stations, minor factions, signals,
misc) all repeat the same non-domain steps: work out the reference system, build+run the
query over the shared client, copy the primary system to the clipboard, and phrase small
spoken fragments. Those live here so each capability file stays focused on its slots, its
vocabulary, and its result sentence — the parts that actually differ.

Nothing here is domain-specific: no slot names, no vocabularies. (The outfitting and
star-systems capabilities predate this module and keep their own inline copies; the four
Prompt-5 categories share these.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..search import NavError, build_query, execute_search
from ..search.categories import CategorySpec
from ..search.spansh import _DEFAULT_UA


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


def run_query(spec: CategorySpec, slots: dict, http, reference: str, *,
              user_agent: str, size: int) -> list[dict]:
    """Build the category's query (fails LOUD on an unknown param), POST it, and return the raw
    results list. Raises `NavError` (spoken-friendly) on a Spansh/transport failure."""
    payload = build_query(spec, slots, reference, size=size)
    return execute_search(spec.endpoint, payload, http, user_agent=user_agent,
                          reference_system=reference, subject=spec.subject,
                          lookup_name=spec.lookup_name)


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


def clipboard_note(name: str, copied: bool) -> str:
    """The trailing 'copied to clipboard' sentence both outcomes share."""
    return (f" I've copied {name} to your clipboard." if copied
            else f" (Couldn't copy to the clipboard — the system is {name}.)")


def distance_phrase(distance_ly: float) -> str:
    """'your current system' when on top of it, else 'N.N light-years away'."""
    return ("your current system" if distance_ly < 0.05
            else f"{distance_ly:.1f} light-years away")


def a_an(word: str) -> str:
    """'a'/'an' for a spoken kind word ('an allegiance', 'a station type')."""
    return f"an {word}" if word[:1].lower() in "aeiou" else f"a {word}"


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
__all__ = ["NavError", "SearchConfig", "reference_system", "run_query", "faction_or_recovery",
           "copy_system", "clipboard_note", "distance_phrase", "a_an", "or_list"]
