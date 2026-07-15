"""Local phrase-spotter for the Tier-2 combat reflexes (issue #38).

This is the FAST PATH for deliberate combat calls. The normal turn is transcribe -> LLM ->
tool -> executor; that round-trip is fine for conversation but too slow when something is
shooting at you. The phrase-spotter collapses it: it maps a small, FIXED vocabulary of combat
keywords (spotted in the LOCAL Whisper transcript) STRAIGHT to a named Tier-2 reflex, so a snap
"chaff!" fires with latency ~= STT time only — no LLM, no cloud token, no queue behind the main
conversation turn. It is deliberately paired with a SECOND push-to-talk (``[reflex].ptt``) so a
reflex call jumps the queue instead of waiting behind whatever the main PTT is doing.

Design mirrors the pure-rules detectors (``covas/wake.py``, ``covas/ed/detector.py``): a class
that is a function of *(vocabulary, text)* only — no audio, no threads, no executor, no LLM — so
match / synonym / no-match are all exercised offline with plain strings. It NEVER presses a key
itself; it only returns a reflex NAME (or ``None``). Dispatch, the allowlist, and the whole
combat-permissive guard + hard abort stay in ``ReflexCapability`` (issue #36) — the spotter feeds
:meth:`ReflexCapability.fire_reflex`, so there is exactly ONE guard and ONE executor.

The vocabulary is FIXED (a closed grammar, the whole point of a reflex vs. free-form speech):

  * the reflex names it can return are exactly the Tier-2 ``COMBAT_PERMISSIVE`` set
    (``chaff`` / ``heat_sink`` / ``shields`` / ``boost``), each with a few spoken synonyms, plus
  * the special :data:`ABORT` sentinel, whose synonyms ("abort", "stop", …) route to the shared
    hard abort — a snap "abort!" on the reflex key must release every held key just as fast.

Matching rules (all case-insensitive, punctuation/whitespace-robust):

  * **Whole-word** — a phrase matches only on whole word tokens, so "chaff" fires but "chaffing"
    does not and "shield" inside "unshielded" does not. (Same tokeniser as the wake gate.)
  * **Synonyms + multi-word** — each reflex has several trigger phrases, and a phrase may be
    multiple words ("break their lock", "heat sink"); the words must appear consecutively.
  * **Leftmost wins** — the earliest keyword in the utterance decides the reflex, so "chaff now!"
    and "quick, chaff" both pick chaff; abort phrases win only if they appear first.
  * **No match -> None** — an utterance with no keyword returns ``None`` so the caller falls
    through to a normal LLM turn; the second PTT never eats an ordinary request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .capabilities.reflex_capability import COMBAT_PERMISSIVE

# Word tokens only (unicode) — punctuation/spacing are matched around, never against. Matching on
# word tokens (not a raw substring) is what makes "chaff" whole-word (so "chaffinch" can't fire).
_WORD_RE = re.compile(r"\w+", re.UNICODE)

# The hard-abort sentinel the spotter can return. It is NOT a member of COMBAT_PERMISSIVE — it's a
# meta action routed to ReflexCapability's shared release_all(), so a snap "abort!" on the reflex
# key lifts every held key as fast as a reflex fires.
ABORT = "abort"

# The FIXED spotter vocabulary: reflex NAME -> spoken trigger phrases (synonyms). The names are the
# Tier-2 COMBAT_PERMISSIVE set plus the ABORT sentinel. Being in this map is what the spotter can
# RECOGNISE; whether a recognised reflex actually FIRES is still gated downstream by the reflex
# allowlist + combat-permissive guard in ReflexCapability — the spotter never bypasses that.
REFLEX_VOCABULARY: dict[str, tuple[str, ...]] = {
    "chaff": ("chaff", "flares", "flare", "decoy", "break lock", "break their lock", "break lock now"),
    "heat_sink": ("heat", "heat sink", "heatsink", "dump heat", "sink"),
    "shields": ("shields", "shield", "shield cell", "cell", "scb", "boost shields"),
    "boost": ("boost", "punch it", "boost it", "afterburner"),
    ABORT: ("abort", "stop", "cancel", "release", "belay", "hold fire"),
}


def _validate_vocab(vocab: dict[str, tuple[str, ...]]) -> None:
    """Fail loud if a vocabulary names a reflex the Tier-2 policy doesn't know: every key must be a
    COMBAT_PERMISSIVE reflex or the ABORT sentinel, so the spotter can never return a name that
    isn't recognised by :meth:`ReflexCapability.fire_reflex`."""
    allowed = set(COMBAT_PERMISSIVE) | {ABORT}
    unknown = set(vocab) - allowed
    if unknown:
        raise ValueError(f"reflex spotter vocabulary names unknown reflexes: {sorted(unknown)}")


@dataclass(frozen=True)
class PhraseSpotter:
    """Pure local phrase-spotter. Build once (``from_cfg`` or with an explicit vocabulary in
    tests); :meth:`match` is a pure function of *(vocabulary, text)*. It returns the matched reflex
    NAME (a COMBAT_PERMISSIVE member or :data:`ABORT`) or ``None`` — dispatch stays entirely in
    ``ReflexCapability`` so the guard/executor are never duplicated."""

    vocab: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(REFLEX_VOCABULARY))
    # Pre-tokenised phrases: NAME -> list[list[word]], built in __post_init__ so match() is cheap.
    _phrases: dict[str, list[list[str]]] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        _validate_vocab(self.vocab)
        phrases: dict[str, list[list[str]]] = {}
        for name, spoken in self.vocab.items():
            toks = [[w.lower() for w in _WORD_RE.findall(p)] for p in spoken]
            phrases[name] = [t for t in toks if t]   # drop any all-punctuation phrase
        # dataclass is frozen — set the derived cache via object.__setattr__.
        object.__setattr__(self, "_phrases", phrases)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "PhraseSpotter":
        """Build from config. The vocabulary is FIXED today (a closed grammar is the point of a
        reflex), so this always uses the built-in map; the classmethod exists so the app builds the
        spotter the same way it builds the other pure detectors, and to give a single seam if a
        future issue wants to let a Commander tune the phrases."""
        return cls()

    def match(self, transcript: str) -> str | None:
        """Return the reflex NAME the utterance calls for (leftmost keyword wins), or ``None`` when
        no keyword is present so the caller falls through to a normal turn. Pure and fast."""
        tokens = [m.group(0).lower() for m in _WORD_RE.finditer(transcript or "")]
        for i in range(len(tokens)):
            for name, variants in self._phrases.items():
                for words in variants:
                    if self._matches_at(tokens, i, words):
                        return name
        return None

    # ---- internals --------------------------------------------------------
    @staticmethod
    def _matches_at(tokens: list[str], i: int, words: list[str]) -> bool:
        """Do the phrase ``words`` match ``tokens`` starting at index ``i``, word for word?"""
        if i + len(words) > len(tokens):
            return False
        return all(tokens[i + j] == w for j, w in enumerate(words))


__all__ = ["PhraseSpotter", "REFLEX_VOCABULARY", "ABORT"]
