"""Proactive callouts — the companion initiates speech on notable ED events (DESIGN §5).

When Elite Dangerous monitoring (DESIGN §5) is on, the journal/status watchers publish
`{"type":"ed_event", ...}` on the bus. This capability reacts to a *whitelisted* subset of
those — arrival (FSDJump/Docked), mission complete, low fuel, overheating, death — and asks
the app to speak a short in-character line, WITHOUT a push-to-talk press.

Two pieces, split so the gating logic is pure and unit-testable:

  * `ProactivePolicy` — the decision (pure). Given an event name and the current time it
    answers "should we speak?", enforcing the per-event whitelist, per-event + global
    cooldowns, and a runtime mute. Holds no threads, no I/O, an injectable clock.
  * `ProactiveCapability` — the wiring. Implements the capability `on_event` hook: it runs
    the policy and, when it says yes, calls back into the app to generate + speak the line
    (the app owns the LLM/TTS + the "never interrupt a user turn" coordination). Also
    exposes mute/unmute tools so the Commander can silence callouts by voice.

Cost: the app routes these through the cheap tier (Router.cheap_route) with a small token
cap — a callout is one sentence. Everything here is opt-in and off by default.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# Events worth announcing by default. Keys are the semantic event names the watchers
# publish (journal event names + status transitions); values are on/off toggles. The
# whole feature is off by default ([proactive].enabled = false), so these only matter
# once it's opted into — then each can still be silenced individually. Deliberately
# excludes combat/interdiction spam so the companion isn't chatty when it matters least.
DEFAULT_EVENTS: dict[str, bool] = {
    "FSDJump": True,          # arrived in a new system
    "Docked": True,          # docked at a station
    "MissionCompleted": True,
    "LowFuel": True,         # fuel dropped below 25%
    "Overheating": True,     # ship > 100% heat
    "Died": True,
    # On-foot / SRV awareness (#54) — same cooldown/whitelist discipline as the rest.
    "ScanOrganic": True,     # exobiology sample logged ("one more to analyse")
    "OxygenLow": True,       # on-foot suit oxygen below 25%
    "HealthLow": True,       # on-foot health critical
    "SrvHullLow": True,      # SRV hull dropped below 30%
}

# A qualifying event won't re-fire for this many seconds (per event type), and no two
# callouts of ANY type fire within `min_interval` — so a burst of transitions (jump ->
# supercruise exit -> dock) yields at most one line. Both tunable in [proactive].
DEFAULT_COOLDOWN = 120.0
DEFAULT_MIN_INTERVAL = 20.0
# One-sentence callouts don't need many tokens; keeps the cheap tier cheap.
DEFAULT_MAX_TOKENS = 120
# Dedicated cooldown for place/history remarks (#138): a busy engineering session docks the same
# base repeatedly, so place-aware enrichment is gated FAR longer than the normal per-event cooldown
# — occasional colour, not narration of every dock. Separate axis from the per-event cooldown.
DEFAULT_PLACE_COOLDOWN = 900.0
# Long-hyperspace flavor remark (#149). "Longer than normal" = the plotted jump distance is at or
# beyond this many light-years. Most non-explorer builds jump well under 50 ly, and even engineered
# explorers only exceed it on their longest hops, so 50 ly reliably marks an above-average jump
# without firing on routine ones. The remark rides its own cooldown so back-to-back long hops on a
# highway don't each get a line.
DEFAULT_LONG_JUMP_LY = 50.0
DEFAULT_LONG_JUMP_COOLDOWN = 300.0


def _as_float(value: object, default: float) -> float:
    """Coerce a possibly null/non-numeric override to float, defaulting fail-soft (never raises)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    """Coerce a possibly null/non-numeric override to int, defaulting fail-soft (never raises)."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ProactiveConfig:
    """Immutable snapshot of the proactive policy, built from `[proactive]`. Kept separate
    from the mutable runtime state (last-fire times, mute) so the gating stays explainable."""
    enabled: bool = False
    cooldown: float = DEFAULT_COOLDOWN
    min_interval: float = DEFAULT_MIN_INTERVAL
    max_tokens: int = DEFAULT_MAX_TOKENS
    place_cooldown: float = DEFAULT_PLACE_COOLDOWN
    long_jump_enabled: bool = True
    long_jump_ly: float = DEFAULT_LONG_JUMP_LY
    long_jump_cooldown: float = DEFAULT_LONG_JUMP_COOLDOWN
    events: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_EVENTS))

    @classmethod
    def from_cfg(cls, cfg: dict) -> "ProactiveConfig":
        p = cfg.get("proactive", {}) or {}
        d = cls()
        events = p.get("events")
        if isinstance(events, dict):
            # Normalize to {name: bool}; an explicit table replaces the defaults so the
            # Commander can pare the list down as well as extend it.
            events = {str(k): bool(v) for k, v in events.items()}
        else:
            events = dict(DEFAULT_EVENTS)
        # Coerce numerics fail-soft: a null/non-numeric override for ONE field falls back to its
        # default instead of raising and aborting the whole config build — which would silently
        # disable every proactive callout (the sibling HonkConfig/_as_float pattern).
        return cls(
            enabled=bool(p.get("enabled", False)),
            cooldown=_as_float(p.get("cooldown"), d.cooldown),
            min_interval=_as_float(p.get("min_interval"), d.min_interval),
            max_tokens=_as_int(p.get("max_tokens"), d.max_tokens),
            place_cooldown=_as_float(p.get("place_cooldown"), d.place_cooldown),
            long_jump_enabled=bool(p.get("long_jump_enabled", d.long_jump_enabled)),
            long_jump_ly=_as_float(p.get("long_jump_ly"), d.long_jump_ly),
            long_jump_cooldown=_as_float(p.get("long_jump_cooldown"), d.long_jump_cooldown),
            events=events,
        )

    def allows(self, event_name: str) -> bool:
        """Whether `event_name` is whitelisted (present and enabled)."""
        return bool(self.events.get(event_name, False))


class ProactivePolicy:
    """The 'should we speak?' gate. Pure decision logic + a little mutable state (per-event
    last-fire times and a runtime mute). Construct once; drive `should_speak`/`mark_fired`
    with a caller-supplied monotonic clock so tests can advance time deterministically."""

    def __init__(self, cfg: ProactiveConfig, *, muted: bool = False) -> None:
        self.cfg = cfg
        self._muted = muted
        self._last: dict[str, float] = {}   # event name -> last fire time
        self._last_any: float = float("-inf")
        # Separate last-fire clocks for the extra remark axes (#138 place/history, #149 long jump),
        # so each has its own dedicated cooldown independent of the per-event whitelist cooldown.
        self._last_place: float = float("-inf")
        self._last_long_jump: float = float("-inf")

    @classmethod
    def from_cfg(cls, cfg: dict) -> "ProactivePolicy":
        return cls(ProactiveConfig.from_cfg(cfg))

    # -- runtime mute (the global quiet switch) ---------------------------------------
    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        return self._muted

    # -- the gate ---------------------------------------------------------------------
    def should_speak(self, event_name: str, now: float) -> tuple[bool, str]:
        """Decide whether to voice a callout for `event_name` at time `now` (seconds, from
        a monotonic clock). Returns (allowed, reason) — the reason is logged so the rules
        can be tuned. This is pure: it never mutates state. Call `mark_fired` only once the
        app has actually committed to speaking, so a skipped/deferred line doesn't burn the
        cooldown."""
        c = self.cfg
        if not c.enabled:
            return False, "proactive disabled"
        if self._muted:
            return False, "muted"
        if not c.allows(event_name):
            return False, f"{event_name} not whitelisted"
        if now - self._last_any < c.min_interval:
            return False, f"global cooldown ({c.min_interval:.0f}s)"
        last = self._last.get(event_name)
        if last is not None and now - last < c.cooldown:
            return False, f"{event_name} cooldown ({c.cooldown:.0f}s)"
        return True, f"{event_name} callout"

    def mark_fired(self, event_name: str, now: float) -> None:
        """Record that a callout for `event_name` was spoken at `now`, arming both its
        per-event cooldown and the global interval."""
        self._last[event_name] = now
        self._last_any = now

    # -- dedicated place/history cooldown (#138) --------------------------------------
    def should_place_remark(self, now: float) -> bool:
        """Whether a place/history remark may ride the current arrival callout — gated ONLY by the
        dedicated place cooldown (the caller has already decided the arrival itself is speaking, and
        the facts are already known to be notable). Pure; never mutates state."""
        return (now - self._last_place) >= self.cfg.place_cooldown

    def mark_place_remark(self, now: float) -> None:
        """Arm the place cooldown after a place/history remark was included at `now`."""
        self._last_place = now

    # -- dedicated long-jump flavor gate (#149) ---------------------------------------
    def should_long_jump(self, now: float) -> tuple[bool, str]:
        """Whether a long-hyperspace flavor remark may fire at `now`. Honours the SAME master
        switches as every other callout — proactive enable + runtime mute — plus its own enable and
        a dedicated long-jump cooldown, and shares the global `min_interval` so it can't stack on
        another callout. Pure; never mutates. The caller has already checked the jump is long."""
        c = self.cfg
        if not c.enabled:
            return False, "proactive disabled"
        if self._muted:
            return False, "muted"
        if not c.long_jump_enabled:
            return False, "long-jump flavor off"
        if now - self._last_any < c.min_interval:
            return False, f"global cooldown ({c.min_interval:.0f}s)"
        if now - self._last_long_jump < c.long_jump_cooldown:
            return False, f"long-jump cooldown ({c.long_jump_cooldown:.0f}s)"
        return True, "long jump"

    def mark_long_jump(self, now: float) -> None:
        """Arm the long-jump cooldown (and the global interval) after a flavor remark fired."""
        self._last_long_jump = now
        self._last_any = now


# Mute/unmute exposed as LLM tools so "COVAS, stop the callouts" works by voice — the
# feature is trivially mutable without touching config. Only advertised while proactive
# is enabled (the capability is registered only then), so they cost nothing otherwise.
PROACTIVE_TOOLS = [
    {
        "name": "mute_proactive",
        "description": (
            "Silence the companion's proactive Elite Dangerous callouts (spoken lines it "
            "volunteers on events like arriving in a system, completing a mission, or low "
            "fuel — not replies to the Commander). Use when the Commander asks for quiet, "
            "e.g. 'stop the callouts' / 'be quiet' / 'no more announcements'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "unmute_proactive",
        "description": (
            "Re-enable the companion's proactive Elite Dangerous callouts after they were "
            "muted. Use for 'turn callouts back on' / 'you can speak up again'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


def event_phrase(event: dict) -> str:
    """A short human phrase for a proactive-worthy event, for the LLM prompt. Reuses the
    watchers' describers (so 'FSDJump' -> 'Jumped to Sol', 'LowFuel' -> 'Fuel dropped below
    25%'); falls back to a de-camel-cased event name for anything without a describer."""
    from ..ed import describe_journal_event, describe_transition
    name = str(event.get("event", ""))
    return describe_journal_event(event) or describe_transition(name) or _humanize(name)


def _humanize(name: str) -> str:
    """'MissionCompleted' -> 'Mission completed' — a readable fallback phrase."""
    if not name:
        return "Something happened"
    spaced = "".join(f" {c}" if c.isupper() else c for c in name).strip()
    return spaced[0].upper() + spaced[1:].lower()


def build_prompt(event: dict, context_summary: str | None,
                 facts: dict | None = None) -> str:
    """The user-message prompt for a proactive callout. The (cached) personality system
    prompt keeps the companion in character; this just states what happened and asks for a
    single short spoken reaction the Commander didn't request.

    `facts` (issue #138) is an OPTIONAL dict of GROUNDED place/visit facts (engineer base, own
    carrier, landmark, visit counts). When present, the model must voice them ACCURATELY and may
    NOT invent names or numbers — this is the same grounding discipline as everywhere else. It's
    kept in the user prompt (never the cached system prompt), so it can't bust prompt caching."""
    phrase = event_phrase(event)
    lines = [
        "You are speaking UNPROMPTED — the Commander did not ask anything. Something just "
        f"happened in Elite Dangerous: {phrase}.",
    ]
    if context_summary:
        lines.append(f"Current situation: {context_summary}")
    if facts:
        # Import here to keep the module import graph light and avoid a cycle at load time.
        from ..ed.place_classifier import render_facts
        rendered = render_facts(facts)
        if rendered:
            lines.append(
                "Grounded facts about where you've arrived — voice these ACCURATELY and do "
                f"NOT invent any names, places, or numbers: {rendered}."
            )
    lines.append(
        "React with ONE short, in-character spoken line (a heads-up, quip, or "
        "acknowledgement). Do not ask a question or expect a reply. Keep it under 20 words."
    )
    return "\n".join(lines)


class ProactiveCapability:
    """Reacts to bus `ed_event`s and, when the policy allows, asks the app to speak.

    `speak` is the app callback `(event_name, event) -> bool`: it originates the spoken
    line (cheap-tier LLM + TTS via the existing cancel path) and returns True only if it
    actually started — i.e. the app was idle and not mid-user-turn. The cooldown is armed
    only on a True result, so a callout skipped because the Commander was talking can fire
    once they're done rather than being silently swallowed by the cooldown.
    """

    def __init__(
        self,
        policy: ProactivePolicy,
        speak: Callable[[str, dict], bool],
        *,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.policy = policy
        self._speak = speak
        self._clock = clock
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return PROACTIVE_TOOLS

    def run_tool(self, name: str, inp: dict) -> str:
        if name == "mute_proactive":
            self.policy.set_muted(True)
            return "Proactive callouts muted."
        if name == "unmute_proactive":
            self.policy.set_muted(False)
            return "Proactive callouts unmuted."
        return f"Unknown tool: {name}"

    def on_event(self, event: dict) -> None:
        """Bus hook (dispatched by the app's event pump). Gate the event through the policy
        and, if it passes, hand off to the app to speak. Must never raise — it runs on the
        shared event-pump thread, and a watcher event must not take that thread down."""
        try:
            if not isinstance(event, dict) or event.get("type") != "ed_event":
                return
            name = event.get("event")
            if not name:
                return
            ok, reason = self.policy.should_speak(name, self._clock())
            if not ok:
                return
            started = self._speak(name, event)
            if started:
                self.policy.mark_fired(name, self._clock())
                if self._log is not None:
                    self._log(reason)
        except Exception:  # noqa: BLE001 — never crash the event pump on a bad event
            pass
