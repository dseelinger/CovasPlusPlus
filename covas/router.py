"""Cost router — per-turn cloud model tiering policy (DESIGN §4).

This module is *policy only*: given the spoken text and a little context, it decides which
**tier** a turn should use (and the concrete model id + `max_tokens`), and *why*. It holds no
provider logic and makes no network calls, so it's pure and unit-testable — the whole point is
that tuning cost never touches the LLM/loop code.

**Provider-agnostic (issue #11).** The router picks one of three canonical tiers; the concrete
model id for each tier comes from the ACTIVE `[llm].provider`'s tier map, so the *same* policy
drives Anthropic, OpenAI, Gemini, … — only the tier→model mapping differs per provider:
  - `cheap`    (default)  : in-cockpit banter, acks, checklist reads, status readouts.
  - `standard` (escalate) : turns needing current/web data, depth/analysis, or a wake phrase
    ("think hard", "ask the big brain").
  - `premium`             : only on an explicit override ("use opus" / "use the big model").
For **Anthropic** the map is `[router].{default,escalate,premium}_model` (Haiku/Sonnet/Opus) and
the router-off model is `[anthropic].model` — unchanged, so Anthropic users see no difference. Any
other provider advertises its map via `[<provider>].tiers.{cheap,standard,premium}` (or a single
`[<provider>].model` for all tiers); see `_provider_tiers`. This is the seam #12 (OpenAI) and #13
(Gemini) plug into.

A manual pin (UI toggle) forces a tier regardless of the text. An explicit "full breakdown"-style
request raises `max_tokens` so a long answer isn't truncated mid-sentence over TTS.

Extension point: a future cheap-classifier pass (a cheap-tier turn that tags the request
cheap/premium) would slot in as an alternative to the phrase rules in `decide()` — leave the seam,
don't build it yet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# The three canonical, provider-agnostic tiers the policy chooses between (plus "fixed" = the
# router-off single model). A provider maps each of these to one of its model ids.
CHEAP, STANDARD, PREMIUM = "cheap", "standard", "premium"
TIERS = (CHEAP, STANDARD, PREMIUM)


@dataclass(frozen=True)
class Route:
    """The routing decision for one turn: the chosen `tier`, the concrete `model` that tier maps
    to for the active provider, its token cap, and an explainable reason (logged so the rules can
    be tuned from real transcripts). `tier` is one of TIERS, or "fixed" when the router is off."""
    model: str
    max_tokens: int
    reason: str
    tier: str = ""


# Pin/alias tokens accepted from the UI toggle or a per-turn context, each mapped to a CANONICAL
# tier. Anthropic-flavored aliases (haiku/sonnet/opus) are kept so existing pins keep working.
_TIER_ALIASES = {
    CHEAP: CHEAP, "default": CHEAP, "haiku": CHEAP,
    STANDARD: STANDARD, "escalate": STANDARD, "sonnet": STANDARD,
    PREMIUM: PREMIUM, "opus": PREMIUM,
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
        # base_max_tokens is a reply-length policy (kept from [anthropic].max_tokens, which always
        # exists as the base cap); the tier->model map + router-off model come from the ACTIVE
        # provider so the same policy works for any LLM (issue #11).
        base_max = int((cfg.get("anthropic", {}) or {}).get("max_tokens", 1024))
        provider = str((cfg.get("llm", {}) or {}).get("provider", "anthropic")).strip().lower()
        tiers, fixed = _provider_tiers(cfg, provider)

        def phrases(key: str, default: list[str]) -> list[str]:
            v = r.get(key)
            return [str(x) for x in v] if isinstance(v, list) else default

        d = cls()  # for phrase defaults
        return cls(
            enabled=bool(r.get("enabled", False)),
            default_model=tiers[CHEAP],
            escalate_model=tiers[STANDARD],
            premium_model=tiers[PREMIUM],
            fixed_model=fixed,
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

    @property
    def tiers(self) -> dict:
        """The canonical tier -> model id map for the active provider."""
        return {CHEAP: self.default_model, STANDARD: self.escalate_model,
                PREMIUM: self.premium_model}

    def tier_for(self, token: str) -> str | None:
        """Resolve a pin/alias token ("cheap"/"haiku"/"sonnet"/"opus"/… ) to a CANONICAL tier."""
        return _TIER_ALIASES.get(_norm(token))

    def model_for_tier(self, tier: str) -> str | None:
        """Resolve a tier token (canonical or an alias) to its model id for the active provider."""
        canon = self.tier_for(tier)
        return self.tiers.get(canon) if canon else None


def _provider_tiers(cfg: dict, provider: str) -> "tuple[dict, str]":
    """The tier→model map + the router-off `fixed` model for the ACTIVE llm provider (issue #11).

    Anthropic keeps reading `[router].{default,escalate,premium}_model` + `[anthropic].model`, so
    nothing changes for existing Anthropic users. Any OTHER cloud provider advertises its own map
    via `[<provider>].tiers.{cheap,standard,premium}` (or a single `[<provider>].model` used for
    every tier); the router-off model is `[<provider>].model`. Pure — no I/O. This is the seam the
    OpenAI (#12) and Gemini (#13) LLM providers plug into. `[llm].provider` names the active one.
    """
    d = RouterConfig()
    if provider == "anthropic":
        r = cfg.get("router", {}) or {}
        a = cfg.get("anthropic", {}) or {}
        return ({CHEAP: str(r.get("default_model", d.default_model)),
                 STANDARD: str(r.get("escalate_model", d.escalate_model)),
                 PREMIUM: str(r.get("premium_model", d.premium_model))},
                str(a.get("model", d.fixed_model)))
    # Generic provider: its own [<provider>] section carries the map (or one shared model).
    p = cfg.get(provider, {}) or {}
    model = str(p.get("model", "")).strip() or d.fixed_model
    t = p.get("tiers", {}) or {}

    def pick(k: str) -> str:
        return str(t.get(k, "")).strip() or model

    return ({CHEAP: pick(CHEAP), STANDARD: pick(STANDARD), PREMIUM: pick(PREMIUM)}, model)


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
        return Route(self.cfg.default_model, cap, "proactive — cheap tier", CHEAP)

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

        # OFF: behave exactly like the fixed provider model default. One code path
        # for on/off keeps the decision logged either way.
        if not c.enabled:
            return Route(c.fixed_model, c.base_max_tokens, "router off — fixed tier", "fixed")

        t = _norm(text)

        # max_tokens is chosen independently of tier: an explicit "full breakdown"
        # request raises the cap so a long answer isn't cut off over TTS.
        breakdown = _matched(t, c.full_breakdown_phrases)
        max_tokens = c.full_breakdown_max_tokens if breakdown else c.base_max_tokens
        cap_note = f"; raised max_tokens for '{breakdown}'" if breakdown else ""

        # Manual pin (UI toggle or per-turn context) forces a tier, highest priority.
        pin = _norm(context.get("pin", "") or c.pin)
        if pin:
            canon = c.tier_for(pin)
            if canon:
                return Route(c.tiers[canon], max_tokens, f"pinned to {pin}{cap_note}", canon)

        # Explicit override — the only path to the premium tier.
        if (m := _matched(t, c.premium_phrases)):
            return Route(c.premium_model, max_tokens,
                         f"override: '{m}' -> premium tier{cap_note}", PREMIUM)

        # Escalate to the standard tier when the turn earns it. Collect every reason so the log
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
                         "escalate -> standard tier: " + ", ".join(reasons) + cap_note, STANDARD)

        # Default: the cheap workhorse tier.
        return Route(c.default_model, max_tokens, "default -> cheap tier" + cap_note, CHEAP)
