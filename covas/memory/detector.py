"""Recall detector — does a spoken turn reference the PAST? (issue #61).

Policy only, the exact twin of the ED `ContextDetector` (covas/ed/detector.py): given the
spoken text, decide whether the Commander is reaching back into persistent memory ("do you
remember…", "what's my favourite…", "have I been here before") and *why*. It makes no network
calls, runs no LLM, and touches no store — it just classifies the request so `app.py` knows
whether to prepend a COMPACT memory block to THAT turn's user message (never the cached system
prompt, so recall can't bust the prompt cache — the same trick as the ED telemetry block).

One match kind: a recall reference -> build + inject the memory block. An explicit "recall"
wake word forces a lookup and is scrubbed from what the model sees (mirrors the ED "context"
override); the natural recall phrases ARE the request ("what's my main ship") and are never
stripped.

Phrase rules are deterministic and explainable — tune them from real transcripts. The optional
embedding-similarity seam lives in the Retriever, not here; detection stays pure keyword rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MemoryRef:
    """The detection result for one turn: whether it references stored memory, and an
    explainable reason. Mirrors `ed.detector.ContextRef` (minus the log flag — recall has
    a single mode)."""
    matched: bool
    reason: str


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace so phrase matching is punctuation/spacing-robust."""
    return " ".join(str(text).lower().split())


def _matched(text: str, phrases: list[str]) -> str | None:
    for p in phrases:
        p = _norm(p)
        if p and p in text:
            return p
    return None


@dataclass(frozen=True)
class MemoryDetectorConfig:
    """Immutable snapshot of the recall-detection policy, built from `[memory]`. Kept separate
    from `MemoryDetector` so `decide()` is a pure function of (config, text)."""
    wake_phrases: list[str] = field(default_factory=lambda: ["recall"])
    recall_phrases: list[str] = field(default_factory=lambda: [
        # explicit "reach into memory" asks
        "do you remember", "you remember", "do you recall", "what do you remember",
        "what do you know about", "remind me what", "remind me of", "did i tell you",
        "did i ever tell you", "i told you", "we talked about", "we discussed",
        "last time", "have i been", "have i done", "have i ever",
        # standing preferences / identity the store is meant to hold
        "my favourite", "my favorite", "my preferred", "i prefer",
        "what's my", "what is my", "how do i like", "how i like",
        "what do you call me", "my usual",
    ])

    @classmethod
    def from_cfg(cls, cfg: dict) -> "MemoryDetectorConfig":
        m = cfg.get("memory", {}) or {}
        d = cls()

        def phrases(key: str, default: list[str]) -> list[str]:
            v = m.get(key)
            return [str(x) for x in v] if isinstance(v, list) else default

        return cls(
            wake_phrases=phrases("recall_wake", d.wake_phrases),
            recall_phrases=phrases("recall_phrases", d.recall_phrases),
        )


class MemoryDetector:
    """Classifies a turn as referencing stored memory or not. Construct once from config; the
    decision is a pure function of the config + the turn's text. The exact shape of
    `ed.detector.ContextDetector` so the worker loop composes the two blocks identically."""

    def __init__(self, cfg: MemoryDetectorConfig) -> None:
        self.cfg = cfg

    @classmethod
    def from_cfg(cls, cfg: dict) -> "MemoryDetector":
        return cls(MemoryDetectorConfig.from_cfg(cfg))

    def decide(self, text: str) -> MemoryRef:
        c = self.cfg
        t = _norm(text)

        wake = _matched(t, c.wake_phrases)
        if wake:
            return MemoryRef(True, f"recall wake word '{wake}'")

        if (m := _matched(t, c.recall_phrases)):
            return MemoryRef(True, f"recall reference '{m}'")
        return MemoryRef(False, "no recall reference")

    def strip(self, text: str) -> str:
        """Remove only the explicit wake word from the model's input (e.g. a leading
        'Recall,' the Commander said to force a lookup). Recall phrases ARE the request
        ('what's my main ship'), so they're never stripped. Turns without the wake word pass
        through unchanged; a turn that is *only* the wake word falls back to the original so
        there's always something to answer. Verbatim of `ContextDetector.strip`."""
        low = _norm(text)
        if not any(_norm(p) in low for p in self.cfg.wake_phrases if p):
            return text
        out = text
        for p in sorted(self.cfg.wake_phrases, key=len, reverse=True):
            if p:
                out = re.sub(re.escape(p), " ", out, flags=re.IGNORECASE)
        out = re.sub(r"\s+", " ", out).strip()
        out = re.sub(r"^[\W_]+", "", out)                       # drop dangling punctuation
        out = re.sub(r"^(and|then|so)\b\s*", "", out, flags=re.IGNORECASE).strip()
        return out or text
