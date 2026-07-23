"""Cue governor (C3) — the anti-over-talking budget.

Over-talking is the primary failure mode of an ambient audio layer, so throttling is
structural, not incidental. The governor enforces three hard limits and picks deterministically
(rotation, never random) so tests are stable:

  * a GLOBAL minimum interval — no two cues within `min_interval` seconds;
  * a GLOBAL rate cap — at most `max_per_minute` cues in any rolling 60 s window;
  * a PER-CUE cooldown — a cue won't repeat within its own `cooldown_s` (or `default_cooldown`).

Pure decision + a little mutable state (last-fire times, the rate window, a rotation pointer),
driven by a caller-supplied monotonic clock — the same shape as ProactivePolicy, so tests can
advance time deterministically.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .cues import Cue

DEFAULT_MIN_INTERVAL = 8.0     # seconds between ANY two cues
DEFAULT_MAX_PER_MINUTE = 6     # hard cap per rolling 60 s
DEFAULT_COOLDOWN = 30.0        # per-cue fallback when a cue declares none
_WINDOW = 60.0


@dataclass(frozen=True)
class GovernorConfig:
    """Immutable throttle config, built from [audio.cues]. Off by default — the whole audio
    cue layer is opt-in."""

    enabled: bool = False
    min_interval: float = DEFAULT_MIN_INTERVAL
    max_per_minute: int = DEFAULT_MAX_PER_MINUTE
    default_cooldown: float = DEFAULT_COOLDOWN

    @classmethod
    def from_cfg(cls, cfg: dict) -> GovernorConfig:
        c = (cfg.get("audio", {}) or {}).get("cues", {}) or {}
        d = cls()
        return cls(
            enabled=bool(c.get("enabled", d.enabled)),
            min_interval=float(c.get("min_interval", d.min_interval)),
            max_per_minute=int(c.get("max_per_minute", d.max_per_minute)),
            default_cooldown=float(c.get("default_cooldown", d.default_cooldown)),
        )


class CueGovernor:
    """The 'may this cue play now?' gate plus deterministic selection among eligible cues."""

    def __init__(self, cfg: GovernorConfig, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.cfg = cfg
        self._clock = clock
        self._last_cue: dict[str, float] = {}
        self._fired: deque[float] = deque()   # fire timestamps within the rolling window
        self._last_any: float = float("-inf")
        self._rotor: int = 0                  # advances each fire -> rotation, not repetition

    @classmethod
    def from_cfg(cls, cfg: dict, *, clock: Callable[[], float] = time.monotonic) -> CueGovernor:
        return cls(GovernorConfig.from_cfg(cfg), clock=clock)

    def _cooldown_for(self, cue: Cue) -> float:
        cd = getattr(cue, "cooldown_s", 0.0) or 0.0
        return float(cd) if cd > 0 else self.cfg.default_cooldown

    def _prune(self, now: float) -> None:
        while self._fired and now - self._fired[0] >= _WINDOW:
            self._fired.popleft()

    def allow(self, cue: Cue, now: float) -> tuple[bool, str]:
        """Whether `cue` may play at `now` (monotonic seconds). Pure — never mutates. Call
        `mark_fired` only once a cue actually plays, so a skipped one doesn't burn the budget."""
        c = self.cfg
        if not c.enabled:
            return False, "cue layer disabled"
        if now - self._last_any < c.min_interval:
            return False, f"global min interval ({c.min_interval:.0f}s)"
        # Count fires still inside the rolling window WITHOUT mutating (pruning happens on fire),
        # so allow() stays a pure query.
        in_window = sum(1 for t in self._fired if now - t < _WINDOW)
        if in_window >= c.max_per_minute:
            return False, f"rate cap ({c.max_per_minute}/min)"
        last = self._last_cue.get(cue.name)
        cd = self._cooldown_for(cue)
        if last is not None and now - last < cd:
            return False, f"{cue.name} cooldown ({cd:.0f}s)"
        return True, cue.name

    def mark_fired(self, cue: Cue, now: float) -> None:
        """Record that `cue` played at `now`: arms its cooldown, the global interval, the rate
        window, and advances the rotation pointer."""
        self._last_cue[cue.name] = now
        self._last_any = now
        self._prune(now)
        self._fired.append(now)
        self._rotor += 1

    def select(self, eligible: list[Cue], now: float) -> Cue | None:
        """Pick ONE cue to play from the eligible set, deterministically: iterate the cues in a
        stable order rotated by how many have already fired, and return the first the budget
        allows. Rotation (not random) spreads plays across eligible cues while staying testable.
        Returns None when none may play (disabled, throttled, all in cooldown, or empty)."""
        if not eligible:
            return None
        ordered = sorted(eligible, key=lambda c: c.name)
        n = len(ordered)
        for i in range(n):
            cue = ordered[(self._rotor + i) % n]
            ok, _ = self.allow(cue, now)
            if ok:
                return cue
        return None
