"""Context detector — does a spoken turn reference live game state? (DESIGN §5).

Policy only, like the cost Router (DESIGN §4): given the spoken text, decide whether the
Commander is asking about current status (where am I, my fuel, my ship) or their recent
activity (what just happened, check my logs), and *why*. It makes no network calls and
touches no game state — it just classifies the request so `app.py` knows whether to inject
the live telemetry block into that turn.

Two match kinds:
  - status  -> inject the current-context summary (system/station/ship/fuel/cargo).
  - log     -> also inject the recent-events feed ('what just happened').
An explicit "context" wake word forces both (a manual override, mirroring the Router's
tier pin). Detection keys off the RAW text; `strip()` removes only the wake word from what
the model finally sees, so a natural request like "where am I" is never mangled.

Phrase rules are deterministic and explainable — tune them from real transcripts. A cheap
classifier pass could slot in later; leave the seam, don't build it yet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .currencies import known_names

# Base status phrases (location/ship/fuel/cargo). Money-question phrases are NOT hardcoded here:
# they come from the currency registry (`currencies.known_names()`) so the set of currencies the
# detector will inject a wallet for stays a one-row registry edit — and an UNKNOWN currency's name
# is deliberately absent, so "how many merc coins" never trips a status lookup (#101).
_BASE_STATUS_PHRASES: list[str] = [
    "where am i", "where are we", "what system", "current system", "which system",
    "am i docked", "docked at", "my fuel", "how much fuel", "fuel level", "how's my fuel",
    "my ship", "what ship", "my cargo", "how much cargo", "landing gear", "hardpoints",
    "supercruise", "am i in", "my location", "my status", "ship status",
]


@dataclass(frozen=True)
class ContextRef:
    """The detection result for one turn: whether it references game context, whether the
    recent-events log is wanted (not just current status), and an explainable reason."""
    matched: bool
    wants_log: bool
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
class ContextDetectorConfig:
    """Immutable snapshot of the detection policy, built from `[elite]`. Kept separate
    from `ContextDetector` so `decide()` is a pure function of (config, text)."""
    wake_phrases: list[str] = field(default_factory=lambda: ["context"])
    status_phrases: list[str] = field(
        default_factory=lambda: _BASE_STATUS_PHRASES + known_names())
    log_phrases: list[str] = field(default_factory=lambda: [
        "what just happened", "what happened", "recent events", "my log", "my logs",
        "check the log", "check my log", "last jump", "what did i just", "what have i been",
        "what have i done", "recently", "my journal", "so far",
    ])

    @classmethod
    def from_cfg(cls, cfg: dict) -> "ContextDetectorConfig":
        e = cfg.get("elite", {}) or {}
        d = cls()

        def phrases(key: str, default: list[str]) -> list[str]:
            v = e.get(key)
            return [str(x) for x in v] if isinstance(v, list) else default

        return cls(
            wake_phrases=phrases("context_wake", d.wake_phrases),
            status_phrases=phrases("status_phrases", d.status_phrases),
            log_phrases=phrases("log_phrases", d.log_phrases),
        )


class ContextDetector:
    """Classifies a turn as referencing ED context or not. Construct once from config;
    the decision is a pure function of the config + the turn's text."""

    def __init__(self, cfg: ContextDetectorConfig) -> None:
        self.cfg = cfg

    @classmethod
    def from_cfg(cls, cfg: dict) -> "ContextDetector":
        return cls(ContextDetectorConfig.from_cfg(cfg))

    def decide(self, text: str) -> ContextRef:
        c = self.cfg
        t = _norm(text)

        wake = _matched(t, c.wake_phrases)
        if wake:
            return ContextRef(True, True, f"context wake word '{wake}'")

        reasons: list[str] = []
        wants_log = False
        if (m := _matched(t, c.status_phrases)):
            reasons.append(f"status reference '{m}'")
        if (m := _matched(t, c.log_phrases)):
            reasons.append(f"log reference '{m}'")
            wants_log = True
        if reasons:
            return ContextRef(True, wants_log, "; ".join(reasons))
        return ContextRef(False, False, "no context reference")

    def strip(self, text: str) -> str:
        """Remove only the explicit wake word from the model's input (e.g. a leading
        'Context,' the Commander said to force a lookup). Status/log phrases ARE the
        request ('where am I'), so they're never stripped. Turns without the wake word
        pass through unchanged; a turn that is *only* the wake word falls back to the
        original so there's always something to answer."""
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
