"""Cost router — per-turn cloud model tiering policy (DESIGN §4).

This module is *policy only*: given the spoken text and a little context, it decides
which Anthropic model and `max_tokens` a turn should use, and *why*. It holds no
provider logic and makes no network calls, so it's pure and unit-testable — the whole
point is that tuning cost never touches the LLM/loop code.

Tiers (deterministic first cut):
  - default  -> Haiku : in-cockpit banter, acks, checklist reads, status readouts.
  - escalate -> Sonnet: turns needing current/web data, depth/analysis, or a wake
    phrase ("think hard", "ask the big brain").
  - premium  -> Opus  : only on an explicit override ("use opus").
A manual pin (UI toggle) forces a tier regardless of the text. An explicit
"full breakdown"-style request raises `max_tokens` so a long answer isn't truncated
mid-sentence over TTS.

Extension point: a future cheap-classifier pass (a Haiku turn that tags the request
cheap/premium) would slot in as an alternative to the phrase rules in `decide()` —
leave the seam, don't build it yet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Route:
    """The routing decision for one turn: which model, its token cap, and an
    explainable reason (logged so the rules can be tuned from real transcripts)."""
    model: str
    max_tokens: int
    reason: str


# Tier tokens accepted by the manual pin (UI toggle / context override), each mapped to
# the RouterConfig attribute holding that tier's model id. Friendly aliases included.
_TIER_ALIASES = {
    "haiku": "default_model", "default": "default_model", "cheap": "default_model",
    "sonnet": "escalate_model", "escalate": "escalate_model",
    "opus": "premium_model", "premium": "premium_model",
}


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace so phrase matching is punctuation/spacing-robust."""
    return " ".join(str(text).lower().split())


# Filler that clings to a spoken control phrase ("use opus FOR THIS", "think hard
# FOR ME") and reads oddly once the phrase is removed. Only stripped when a control
# phrase is actually present, so ordinary turns ("scan that for me") are untouched.
_FILLER = ("for this one", "for this", "for me", "on this one", "on this", "please")


def _matched(text: str, phrases: list[str]) -> str | None:
    """First phrase in `phrases` that appears in `text` (already normalized), else None."""
    for p in phrases:
        p = _norm(p)
        if p and p in text:
            return p
    return None


@dataclass(frozen=True)
class RouterConfig:
    """Immutable snapshot of the routing policy, built from `[router]` (with the fixed
    fallback pulled from `[anthropic]`). Kept separate from `Router` so `decide()` stays
    a pure function of (this config, text, context)."""
    enabled: bool = False
    default_model: str = "claude-haiku-4-5"
    escalate_model: str = "claude-sonnet-5"
    premium_model: str = "claude-opus-4-8"
    # The model used when routing is OFF — the plain [anthropic].model default, so
    # nothing changes for the user until they opt in.
    fixed_model: str = "claude-sonnet-5"
    # Base cap ([anthropic].max_tokens) and the raised cap for a "full breakdown" turn.
    base_max_tokens: int = 1024
    full_breakdown_max_tokens: int = 2048
    # Manual tier pin from the UI toggle: "" (off) or a tier token in _TIER_ALIASES.
    pin: str = ""
    escalate_phrases: list[str] = field(default_factory=lambda: [
        "think hard", "ask the big brain", "big brain",
    ])
    premium_phrases: list[str] = field(default_factory=lambda: [
        "use opus", "ask opus",
    ])
    depth_phrases: list[str] = field(default_factory=lambda: [
        "analyze", "analyse", "in detail", "in depth", "deep dive",
        "explain why", "walk me through", "compare", "pros and cons",
    ])
    web_phrases: list[str] = field(default_factory=lambda: [
        "latest", "current", "right now", "look up", "search the web",
        "the news", "up to date", "how much is",
    ])
    full_breakdown_phrases: list[str] = field(default_factory=lambda: [
        "full breakdown", "give me everything", "long version",
        "complete breakdown", "the whole rundown",
    ])

    @classmethod
    def from_cfg(cls, cfg: dict) -> "RouterConfig":
        r = cfg.get("router", {}) or {}
        a = cfg.get("anthropic", {}) or {}
        base_max = int(a.get("max_tokens", 1024))

        def phrases(key: str, default: list[str]) -> list[str]:
            v = r.get(key)
            return [str(x) for x in v] if isinstance(v, list) else default

        d = cls()  # for phrase defaults
        return cls(
            enabled=bool(r.get("enabled", False)),
            default_model=str(r.get("default_model", d.default_model)),
            escalate_model=str(r.get("escalate_model", d.escalate_model)),
            premium_model=str(r.get("premium_model", d.premium_model)),
            fixed_model=str(a.get("model", d.fixed_model)),
            base_max_tokens=base_max,
            full_breakdown_max_tokens=int(
                r.get("full_breakdown_max_tokens", max(base_max, d.full_breakdown_max_tokens))
            ),
            pin=str(r.get("pin", "")).strip().lower(),
            escalate_phrases=phrases("escalate_phrases", d.escalate_phrases),
            premium_phrases=phrases("premium_phrases", d.premium_phrases),
            depth_phrases=phrases("depth_phrases", d.depth_phrases),
            web_phrases=phrases("web_phrases", d.web_phrases),
            full_breakdown_phrases=phrases("full_breakdown_phrases", d.full_breakdown_phrases),
        )

    def model_for_tier(self, tier: str) -> str | None:
        """Resolve a tier token ("haiku"/"sonnet"/"opus" + aliases) to its model id."""
        attr = _TIER_ALIASES.get(_norm(tier))
        return getattr(self, attr) if attr else None


class Router:
    """Decides the model + max_tokens for each turn. Construct once from config; the
    decision itself is a pure function of the config + the turn's text/context."""

    def __init__(self, cfg: RouterConfig) -> None:
        self.cfg = cfg

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Router":
        return cls(RouterConfig.from_cfg(cfg))

    def cheap_route(self, max_tokens: int | None = None) -> Route:
        """The cheapest tier, for turns the app itself originates (e.g. proactive ED
        callouts, DESIGN §5) rather than routing from spoken text. Always the default
        (Haiku) tier regardless of the enabled flag, so proactive lines stay cheap even
        when routing is off. `max_tokens` defaults to the base cap; callers pass a small
        value for one-sentence callouts."""
        cap = self.cfg.base_max_tokens if max_tokens is None else int(max_tokens)
        return Route(self.cfg.default_model, cap, "proactive — cheap tier")

    def strip_control(self, text: str) -> str:
        """Remove pure tier-control phrases (the escalate/premium wake phrases) from the
        text the model will see, so it answers the real request instead of pushing back
        on a model-switch instruction it can't act on ("I can't change my own model").
        Routing still keys off the RAW text — this only cleans the model's input.

        Turns with no control phrase pass through unchanged (ordinary "for me"/"please"
        are left alone). A turn that is *only* a control phrase strips to nothing and
        falls back to the original — there's no real request to preserve."""
        control = list(self.cfg.escalate_phrases) + list(self.cfg.premium_phrases)
        low = _norm(text)
        if not any(_norm(p) in low for p in control if p):
            return text
        out = text
        # Longest-first so "ask the big brain" is removed before "big brain", etc.
        for p in sorted([*control, *_FILLER], key=len, reverse=True):
            if p:
                out = re.sub(re.escape(p), " ", out, flags=re.IGNORECASE)
        out = re.sub(r"\s+", " ", out).strip()
        out = re.sub(r"^[\W_]+", "", out)               # drop punctuation left dangling
        out = re.sub(r"^(and|then|so)\b\s*", "", out, flags=re.IGNORECASE).strip()
        out = re.sub(r"\s+([,.!?;:])", r"\1", out)       # tidy space before punctuation
        return out or text

    def decide(self, text: str, context: dict | None = None) -> Route:
        """Pick a Route for `text`. `context` is an optional dict for future signals
        (e.g. a per-turn UI pin, or ED state hints); today it may carry:
            {"pin": "sonnet"}     -> force a tier for this one turn
            {"needs_web": True}   -> caller already knows the turn needs current data
        """
        c = self.cfg
        context = context or {}

        # OFF: behave exactly like the fixed [anthropic].model default. One code path
        # for on/off keeps the decision logged either way.
        if not c.enabled:
            return Route(c.fixed_model, c.base_max_tokens, "router off — fixed tier")

        t = _norm(text)

        # max_tokens is chosen independently of tier: an explicit "full breakdown"
        # request raises the cap so a long answer isn't cut off over TTS.
        breakdown = _matched(t, c.full_breakdown_phrases)
        max_tokens = c.full_breakdown_max_tokens if breakdown else c.base_max_tokens
        cap_note = f"; raised max_tokens for '{breakdown}'" if breakdown else ""

        # Manual pin (UI toggle or per-turn context) forces a tier, highest priority.
        pin = _norm(context.get("pin", "") or c.pin)
        if pin:
            model = c.model_for_tier(pin)
            if model:
                return Route(model, max_tokens, f"pinned to {pin}{cap_note}")

        # Explicit Opus override — the only path to the premium tier.
        if (m := _matched(t, c.premium_phrases)):
            return Route(c.premium_model, max_tokens, f"override: '{m}' -> Opus{cap_note}")

        # Escalate to Sonnet when the turn earns it. Collect every reason so the log
        # explains *why* it escalated (tune the phrase lists from real transcripts).
        reasons: list[str] = []
        if (m := _matched(t, c.escalate_phrases)):
            reasons.append(f"wake phrase '{m}'")
        if (m := _matched(t, c.depth_phrases)):
            reasons.append(f"depth/analysis '{m}'")
        if context.get("needs_web"):
            reasons.append("caller flagged current/web data")
        elif (m := _matched(t, c.web_phrases)):
            reasons.append(f"current/web data '{m}'")
        if reasons:
            return Route(c.escalate_model, max_tokens,
                         "escalate -> Sonnet: " + ", ".join(reasons) + cap_note)

        # Default: the cheap workhorse.
        return Route(c.default_model, max_tokens, "default -> Haiku" + cap_note)
