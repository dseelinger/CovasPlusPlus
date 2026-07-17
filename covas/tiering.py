"""Capability / token tiering — auto-selected optimization levels (issue #84).

COVAS advertises ~47 tools every turn (~10.4K tokens of JSON tool schemas). That is fine on a
prompt-caching, high-TPM endpoint (Anthropic, OpenAI, Gemini) but fatal on a low-TPM free tier
(Groq free = 12K TPM -> HTTP 413/429) and wasteful on a non-caching paid endpoint. A SECOND cost
axis is the background LLM calls the Commander never pressed PTT for — proactive callouts, chatter
flavor, comms variants — which burn request-limited free tiers. This module tiers BOTH axes at once.

The mechanism is a **token budget packed by priority**. Each capability DECLARES which tiering
*group* it belongs to (a one-line `TIERING_GROUP` class attribute; untagged capabilities default to
`core` so they still work). Each GROUP carries a measured tool-schema `token_cost` and a `priority`
rank. A LEVEL is a token budget: groups are included in priority order until the budget is spent, so
the cheapest-but-most-essential tools survive first. The five named levels (`Full`/`Standard`/`Lean`/
`Minimal`/`Bare`) are just budget presets, each ALSO carrying three background-call flags.

The chosen level is resolved ONCE at startup (stable within a session, so prompt caching stays
warm — v2 per-turn context-gating is deliberately out of scope). The filter is applied in exactly
ONE place — `CapabilityRegistry.tools_for_level` feeds the level-filtered tool list to the
provider's `stream_reply` — never as branches in `app.py` (capabilities-over-loop-edits).

This module is PURE (no config/network I/O): callers pass values in, so it stays unit-testable and
`pytest` offline. Existing config gates (keybinds only if enabled, memory only if enabled, ED
context only if journal monitoring on) run FIRST, at registration time; the level filter is applied
ON TOP of whatever capabilities were registered.

Follow-ups deferred out of this change (see DESIGN_AND_ROADMAP §4):
  * SCHEMA-TRIM — shrinking each tool's own JSON schema (an orthogonal, per-tool win);
  * v2 per-turn context-gating — swapping the tool set mid-session by what the turn needs
    (breaks prompt caching, so explicitly not built here).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    """One tiering group: a cluster of capabilities sharing a measured tool-schema cost and a
    priority rank. Lower `priority` = more essential = packed into the budget first."""
    name: str
    priority: int
    token_cost: int


# The canonical tiering groups, with the token costs MEASURED in issue #84 (JSON tool-schema bytes
# tokenized). Priority order encodes "what to keep when the budget is tight": conversation-critical
# utility + the checklist first, live-Commander context next, then the heavier optional clusters
# (engineering, Spansh/EDSM search) last. A capability names its group via `TIERING_GROUP`.
GROUPS: dict[str, "Group"] = {
    "core":            Group("core",            0, 1450),  # help / settings / clipboard / version / ship-spec / HUD / audio
    "checklist":       Group("checklist",       1, 930),
    "commander_state": Group("commander_state", 2, 600),   # ED context / loadout / on-foot SRV
    "memory":          Group("memory",          3, 540),
    "location":        Group("location",        4, 740),   # where-am-I / carrier / community goals
    "keybinds":        Group("keybinds",        5, 400),   # keybinds / macros / honk / reflex / comms-send
    "engineering":     Group("engineering",     6, 2195),  # engineers / blueprints / stored / on-foot eng
    "search":          Group("search",          7, 3539),  # Spansh + EDSM station/system/body/route/riches/mining
}

# An untagged capability lands here — a sensible default so adding a capability without a tiering
# declaration still works (it survives at every level except Bare, like the rest of core utility).
DEFAULT_GROUP = "core"


@dataclass(frozen=True)
class Level:
    """A named optimization level = a token budget (which tiering groups make the cut) PLUS the
    three background-call flags (the second cost axis). Immutable; the five presets live in LEVELS."""
    name: str
    budget: int
    # Background LLM paths this level permits. When False the app FALLS BACK to the canned/pooled
    # path (already the default) and never spawns the generator — canned chatter/comms cost nothing.
    proactive: bool          # proactive ED callouts (covas/capabilities/proactive_capability.py)
    chatter_flavor: bool     # LLM chatter musings (covas/mixer/runtime.py _text_generator)
    comms_variants: bool     # LLM comms re-voicing (covas/mixer/variants.py)


# Budgets are chosen to land exactly on the named capability sets given GROUPS' priority+cost. See
# the cumulative ladder: core 1450, +checklist 2380, +commander_state 2980, +memory 3520, +location
# 4260, +keybinds 4660, +engineering 6855, +search 10394. Each budget sits in the gap that includes
# the intended prefix and excludes the next group.
_BUDGET_FULL = 10 ** 9          # everything
_BUDGET_STANDARD = 5000         # through keybinds; drops engineering + search
_BUDGET_LEAN = 3000             # core + checklist + commander_state
_BUDGET_MINIMAL = 2400          # core + checklist
_BUDGET_BARE = 0                # no tools

LEVELS: dict[str, "Level"] = {
    "Full":     Level("Full",     _BUDGET_FULL,     proactive=True,  chatter_flavor=True,  comms_variants=True),
    "Standard": Level("Standard", _BUDGET_STANDARD, proactive=True,  chatter_flavor=False, comms_variants=False),
    "Lean":     Level("Lean",     _BUDGET_LEAN,     proactive=False, chatter_flavor=False, comms_variants=False),
    "Minimal":  Level("Minimal",  _BUDGET_MINIMAL,  proactive=False, chatter_flavor=False, comms_variants=False),
    "Bare":     Level("Bare",     _BUDGET_BARE,     proactive=False, chatter_flavor=False, comms_variants=False),
}

# Display order (richest -> leanest). The `optimization_level` setting accepts these plus "auto".
LEVEL_NAMES: list[str] = ["Full", "Standard", "Lean", "Minimal", "Bare"]


def included_groups(budget: int) -> set[str]:
    """The tiering-group names whose cumulative cost fits within `budget`, packed in priority order
    and STOPPING at the first group that doesn't fit (so a level is a clean priority prefix). Pure."""
    out: set[str] = set()
    spent = 0
    for g in sorted(GROUPS.values(), key=lambda g: g.priority):
        if spent + g.token_cost <= budget:
            out.add(g.name)
            spent += g.token_cost
        else:
            break
    return out


def included_groups_for_level(level: "Level | str") -> set[str]:
    """The included tiering groups for a Level (or a level name)."""
    lvl = level if isinstance(level, Level) else LEVELS[normalize_level_name(level)]
    return included_groups(lvl.budget)


def group_of_capability(cap: object) -> str:
    """The tiering group a capability belongs to. Read from an optional `TIERING_GROUP` attribute
    (a plain string, or a zero-arg callable); anything missing or naming an unknown group falls back
    to DEFAULT_GROUP so an untagged/misdeclared capability still resolves to a real group."""
    raw = getattr(cap, "TIERING_GROUP", None)
    if callable(raw):
        try:
            raw = raw()
        except Exception:  # noqa: BLE001 — a broken declaration must not break tool assembly
            raw = None
    name = str(raw).strip() if raw else ""
    return name if name in GROUPS else DEFAULT_GROUP


def normalize_level_name(name: str) -> str:
    """Resolve a user-entered level name to its canonical casing, or "" if it isn't a named level
    (e.g. "auto" or garbage). Case-insensitive so 'minimal'/'MINIMAL' both work."""
    want = str(name or "").strip().lower()
    for canon in LEVEL_NAMES:
        if canon.lower() == want:
            return canon
    return ""


# ---- auto-selection per provider (issue #84 default table) ---------------------------------------
# Anthropic / Gemini / OpenAI / DeepSeek / OpenRouter / paid Groq -> Full. Groq FREE is the only
# token-starved default (12K TPM), so a groq.com endpoint defaults to Minimal — a PAID Groq user
# overrides to Full in Settings (free vs paid isn't distinguishable from the URL, so the safe default
# wins). An unknown custom base_url -> Full, unless the user enters a TPM (then the TPM decides).

def level_for_tpm(tpm: int) -> str:
    """Map a tokens-per-minute budget to the safest named level that still fits a turn. The Full
    tool set alone is ~10.4K tokens, so a 12K-TPM free tier (Groq) needs Minimal; the thresholds
    step up from there. Never returns Bare (that extreme is manual-only)."""
    try:
        t = int(tpm)
    except (TypeError, ValueError):
        return "Full"
    if t <= 0:
        return "Full"
    if t < 15000:
        return "Minimal"
    if t < 30000:
        return "Lean"
    if t < 60000:
        return "Standard"
    return "Full"


def auto_level_name(provider: str, base_url: str = "", custom_tpm: int | None = None) -> str:
    """The auto-selected level name for a provider (+ its OpenAI-compatible base_url). A supplied
    `custom_tpm` (> 0) wins for ANY endpoint — the Commander told us their real limit. Otherwise a
    groq.com endpoint defaults to Minimal and everything else to Full. Never returns Bare."""
    if custom_tpm:
        return level_for_tpm(custom_tpm)
    prov = str(provider or "").strip().lower()
    url = str(base_url or "").strip().lower()
    if prov == "openai" and "groq.com" in url:
        return "Minimal"
    return "Full"


def resolve_level(cfg: dict) -> "Level":
    """The active Level for a config: the `[llm].optimization_level` override if it names one of the
    five, else auto-selected from the provider (+ base_url + optional `[llm].custom_tpm`). Resolved
    ONCE at startup so the tool set — and the prompt cache — stay stable for the whole session."""
    llm = (cfg.get("llm") or {})
    canon = normalize_level_name(str(llm.get("optimization_level", "auto")))
    if canon:
        return LEVELS[canon]
    provider = (llm.get("provider") or "anthropic")
    base_url = ((cfg.get("openai") or {}).get("base_url") or "")
    tpm = llm.get("custom_tpm")
    try:
        tpm = int(tpm) if tpm else None
    except (TypeError, ValueError):
        tpm = None
    return LEVELS[auto_level_name(provider, base_url, tpm)]


def describe_level(cfg: dict) -> str:
    """A one-line human summary of the active level + why (auto vs manual) + the background axis,
    for the startup log so the chosen optimization is visible."""
    lvl = resolve_level(cfg)
    setting = str((cfg.get("llm") or {}).get("optimization_level", "auto")).strip()
    how = "manual" if normalize_level_name(setting) else "auto"
    groups = sorted(included_groups(lvl.budget), key=lambda n: GROUPS[n].priority)
    tools = "no tools" if not groups else ", ".join(groups)
    bg = ("proactive " + ("on" if lvl.proactive else "off")
          + ", chatter-flavor " + ("on" if lvl.chatter_flavor else "off")
          + ", comms-variants " + ("on" if lvl.comms_variants else "off"))
    return f"Optimization level: {lvl.name} ({how}) — tools: {tools}; background: {bg}."
