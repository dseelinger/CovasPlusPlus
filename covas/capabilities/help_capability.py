"""Help subsystem — a first-class, TEMPLATED projection of the capability registry.

Direction (Search Prompt 1): help is first-class and templated. There is **NO LLM in the
help generation path** — every user-facing line is assembled from strings that came from a
registered capability's `HelpMeta`. That's what structurally prevents the companion from
claiming a filter/slot/capability that doesn't exist: help can only speak what the registry
actually carries.

Three modes, all templated (three phrasing variants each, rotated DETERMINISTICALLY by an
internal counter — `random` would make the tests flaky):

  * idle   — "what can you do": the categories the registry exposes, each with one example.
             Ranked by usage; speak at most 3, then a short "there are others — ask about …"
             tail. With nothing registered, a graceful empty-state that still reads right.
  * topic  — "how do I …" / "can you …": detail for ONE category (its example + refinements).
             A topic that isn't a registered category hits a fallback that NEVER echoes the
             unrecognized name (help must not imply an unregistered capability exists).
  * error  — the important one: failure-recovery. Given a term that failed to resolve, match
             it against the registry's canonical vocabulary and answer with the nearest VALID
             phrasing ("I didn't recognize 'power distributer' as a module — did you mean
             Power Distributor?"). Never recites the capability list. The suggestion is always
             a real registry value, so help can't invent a correction.

Help registers ITSELF as a capability, so "what can you do" always has one honest answer.
Invocation is an intent the LLM recognizes (explicit "help", meta "how do I…", or implicit —
anything that fails to resolve routes here), not a command word.
"""
from __future__ import annotations

import difflib
import re
from typing import Callable, Optional

from .base import CapabilityRegistry, HelpMeta

_TOOL_NAME = "help"

# The one category name help uses for ITSELF (excluded from the idle listing so it doesn't
# headline "you can ask for help" above the real capabilities).
_HELP_CATEGORY = "help"

_TOOL_DESCRIPTION = (
    "Explain what you can do, or recover from something you couldn't act on. Call this when "
    "the Commander:\n"
    " - explicitly asks for help ('help', 'what can you do', 'what are my options') — call "
    "with NO arguments for the overview;\n"
    " - asks about a specific capability ('how do I find a module', 'can you search "
    "stations') — pass `topic` with the capability's name;\n"
    " - says something you could NOT resolve to a real module / system / option — pass "
    "`unresolved` with the term you couldn't match (and `expected` with what it should have "
    "been, e.g. 'module'), so the reply can suggest the nearest valid phrasing. Prefer this "
    "over saying 'I didn't understand' — echo what you DID catch and ask for what's missing.\n"
    "The reply is a ready-to-speak line; relay it (you may lightly paraphrase, but keep any "
    "specific suggestion it makes)."
)

_TOOL = {
    "name": _TOOL_NAME,
    "description": _TOOL_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "A capability/category the Commander asked about (e.g. "
                               "'outfitting'). Omit for a general overview.",
            },
            "unresolved": {
                "type": "string",
                "description": "A term the Commander said that you could NOT resolve to a "
                               "real value — pass it here for a failure-recovery suggestion.",
            },
            "expected": {
                "type": "string",
                "description": "What `unresolved` should have been (e.g. 'module', 'system'), "
                               "so the suggestion is drawn from the right vocabulary.",
            },
        },
        "required": [],
    },
}


# ---- templates (three variants each, rotated deterministically) ---------------------------

_IDLE_FRAMES = (
    "Here's what I can help with: {body}.",
    "A few things I can do: {body}.",
    "I can help with a few things: {body}.",
)
_IDLE_EMPTY = (
    "I don't have any special capabilities wired up yet, but you can always just talk to me.",
    "No special skills are loaded right now — but I'm happy to just chat.",
    "Nothing extra is set up at the moment, though you can always talk to me.",
)
_TOPIC_HIT = (
    "{cat}: {one_liner} For example, say \"{example}\".{refine}",
    "For {cat}, {one_liner_lc} Try \"{example}\".{refine}",
    "{cat} — {one_liner} You could say \"{example}\".{refine}",
)
_TOPIC_MISS = (
    "I don't have that as a capability. Ask me what I can do and I'll run through the list.",
    "That's not something I can do yet. Say 'what can you do' to hear the options.",
    "I can't help with that one. Ask what I can do and I'll tell you what I've got.",
)
_RECOVERY_HIT = (
    "I didn't recognize '{term}'{as_kind} — did you mean {sugg}?",
    "'{term}' isn't something I know{as_kind}. Did you mean {sugg}?",
    "I couldn't match '{term}'{as_kind}. Closest I have is {sugg} — is that the one?",
)
_RECOVERY_MISS = (
    "I didn't recognize '{term}'{as_kind}. Try saying it another way, or ask me what I can do.",
    "'{term}' isn't something I recognize{as_kind}. Say it differently, or ask what I can help "
    "with.",
    "I couldn't place '{term}'{as_kind}. Rephrase it, or ask me what I can do.",
)


class HelpCapability:
    """Advertises the `help` tool and assembles every reply from registry data only.

    Holds a reference to the registry (not a snapshot) so capabilities registered AFTER help
    still show up — help_entries() is read live at call time.
    """

    #: help's own metadata — registers itself so "what can you do" always resolves.
    HELP_META = HelpMeta(
        category=_HELP_CATEGORY,
        one_liner="I explain what I can do and help when I couldn't act on something.",
        example="what can you do",
    )

    def __init__(self, registry: CapabilityRegistry,
                 *, log: Optional[Callable[[str], None]] = None) -> None:
        self._registry = registry
        self._log = log
        self._rot = 0  # deterministic rotation counter (advanced once per response)

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [_TOOL]

    def help_meta(self) -> HelpMeta:
        return self.HELP_META

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        idx = self._rot
        self._rot += 1  # advance once per response — rotation is a function of call order
        try:
            unresolved = str(inp.get("unresolved") or "").strip()
            topic = str(inp.get("topic") or "").strip()
            if unresolved:
                return self._recovery(unresolved, str(inp.get("expected") or "").strip(), idx)
            if topic:
                return self._topic(topic, idx)
            return self._idle(idx)
        except Exception as e:  # noqa: BLE001 — help must never break the voice loop
            self._logline(f"error: {e}")
            # Fall back to the empty-state phrasing rather than surfacing an exception.
            return _pick(_IDLE_EMPTY, idx)

    # -- idle: "what can you do" -------------------------------------------------------
    def _idle(self, idx: int) -> str:
        entries = self._registry.help_entries(exclude=self)
        if not entries:
            return _pick(_IDLE_EMPTY, idx)
        shown = entries[:3]
        rest = entries[3:]
        clauses = [f"for {m.category}, say \"{m.example}\"" for m in shown]
        line = _pick(_IDLE_FRAMES, idx).format(body=_join_clauses(clauses))
        if rest:
            names = _or_list([m.category for m in rest[:3]])
            line += f" There are others too — ask about {names}."
        return line

    # -- topic: "how do I <category>" --------------------------------------------------
    def _topic(self, topic: str, idx: int) -> str:
        meta = self._registry.help_entry_for(topic, exclude=self)
        if meta is None:
            # Unregistered topic -> fallback that does NOT echo the (unvalidated) name, so
            # help never implies a capability exists that doesn't.
            return _pick(_TOPIC_MISS, idx)
        refine = ""
        if meta.slots:
            phrasings = [s.phrasings[0] for s in meta.slots[:3] if s.phrasings]
            if phrasings:
                refine = f" You can also specify {_or_list(phrasings)}."
        one_liner = meta.one_liner.strip()
        return _pick(_TOPIC_HIT, idx).format(
            cat=meta.category,
            one_liner=one_liner,
            one_liner_lc=(one_liner[:1].lower() + one_liner[1:]) if one_liner else "",
            example=meta.example,
            refine=refine,
        )

    # -- error: failure-recovery (the important mode) ----------------------------------
    def _recovery(self, term: str, expected: str, idx: int) -> str:
        pool, kind_word = self._recovery_pool(expected)
        sugg = _nearest(term, pool)
        as_kind = f" as a {kind_word}" if kind_word else ""
        if sugg:
            self._logline(f"recovery '{term}' -> '{sugg}'")
            return _pick(_RECOVERY_HIT, idx).format(term=term, as_kind=as_kind, sugg=sugg)
        self._logline(f"recovery '{term}' -> no match")
        return _pick(_RECOVERY_MISS, idx).format(term=term, as_kind=as_kind)

    def _recovery_pool(self, expected: str) -> tuple[list[str], Optional[str]]:
        """The validated candidate values an unresolved term is matched against. When the
        caller names an `expected` kind that the registry has a vocabulary for (e.g.
        'module'), match only within it; otherwise pool every canonical value plus the slot
        phrasings so help can still suggest a real refinement. Every candidate is a real
        registry value — the suggestion can't be invented."""
        vocab = self._registry.vocabulary()
        exp = expected.strip().lower()
        if exp and exp in vocab:
            return list(vocab[exp]), exp
        pool: list[str] = []
        for values in vocab.values():
            pool.extend(values)
        for meta in self._registry.help_entries(exclude=self):
            for slot in meta.slots:
                pool.extend(slot.phrasings)
        return pool, (expected.strip() or None)

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


# ---- helpers ------------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Fold a name for fuzzy comparison: lowercase, drop punctuation/spaces."""
    return _NON_ALNUM.sub("", str(text).lower())


def _nearest(term: str, pool: list[str]) -> Optional[str]:
    """The closest VALID candidate in `pool` to `term`, or None. Returns the canonical pool
    value (not the normalized key), so the suggestion is always something the registry
    actually carries."""
    norm_map: dict[str, str] = {}
    for v in pool:
        norm_map.setdefault(_norm(v), str(v))
    key = _norm(term)
    if not key or not norm_map:
        return None
    if key in norm_map:  # exact-after-normalization (e.g. 'multicannon' -> 'Multi-Cannon')
        return norm_map[key]
    match = difflib.get_close_matches(key, list(norm_map), n=1, cutoff=0.6)
    return norm_map[match[0]] if match else None


def _pick(variants: tuple[str, ...], idx: int) -> str:
    """Deterministic rotation: the idx-th variant, wrapping. Same call order -> same text."""
    return variants[idx % len(variants)]


def _join_clauses(clauses: list[str]) -> str:
    """Join example clauses for speech: 'A', 'A, and B', 'A, B, and C'."""
    clauses = [c for c in clauses if c]
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return ", ".join(clauses[:-1]) + f", and {clauses[-1]}"


def _or_list(items: list[str]) -> str:
    """Join options for speech: 'A', 'A or B', 'A, B, or C'."""
    items = [str(i) for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"
