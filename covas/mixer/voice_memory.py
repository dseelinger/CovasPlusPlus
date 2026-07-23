"""Random-but-sticky voice memory (C10+) — a live speaker->voice map over the cast pool.

`VoiceCast.assign` is deterministic (a stable hash), which is great for reproducibility but means
the same station name always maps to the same voice across every session and every commander. The
user wants the ambient galaxy to feel *randomly cast yet consistent within a play session*:

  * COMMS speakers keep one RANDOM voice for as long as you're in a system, then get re-cast on the
    next jump — the "Liner captain" or "wedding barge" here sounds the same all the way through,
    but a different (random) captain in the next system sounds different. Use `capacity=None` and
    call `clear()` on a system change.
  * PLAYERS keep one RANDOM voice for the whole session, tracked as an LRU of the last N commanders
    (default 25) so a wing or an operation keeps stable per-person voices without growing forever.
    Use `capacity=25`.

ANTI-REPEAT VARIETY (issue #57): the ambient cast recurs often enough to sound like a shuffled
soundboard. On top of the "prefer a voice not currently assigned" rule, an optional `anti_repeat`
window remembers the last N voices this pool HANDED OUT and avoids re-picking them, so consecutive
per-line chatter (and freshly-cast speakers) spread across the pool instead of clustering on a few
voices. The window relaxes gracefully when the pool is too small to honor it, so it never deadlocks.

Pure aside from an injected `rng` (a `random.Random`), so tests seed it for determinism. Empty pool
degrades to the `fallback` voice (the persona) — i.e. today's single-voice behaviour — never raises.
"""
from __future__ import annotations

import random
from collections import OrderedDict, deque

from .voices import Voice


class StickyVoicePool:
    """Assigns pool voices to identities: random on first encounter, then stable.

    `capacity=None` keeps every identity until `clear()`; `capacity=N` keeps the last N (LRU),
    evicting the least-recently-assigned so long-running sessions don't grow unbounded.
    `anti_repeat=N` avoids re-handing-out any of the last N voices picked (0 = off, today's
    behaviour), widening the EFFECTIVE variety of the ambient cast."""

    def __init__(self, pool, *, rng: random.Random | None = None,
                 capacity: int | None = None, fallback: Voice | None = None,
                 anti_repeat: int = 0) -> None:
        self._pool: list[Voice] = list(pool or [])
        self._rng = rng or random.Random()
        self._capacity = capacity
        self._fallback = fallback
        self._assigned: OrderedDict[str, Voice] = OrderedDict()
        # Rolling window of the most-recently-picked voices, avoided on the next pick for variety.
        self._recent: deque[Voice] = deque(maxlen=max(0, int(anti_repeat)))

    @property
    def pool(self) -> list[Voice]:
        return list(self._pool)

    def _candidates(self, gender_hint: str | None) -> list[Voice]:
        """Pool voices matching a male/female hint when any exist, else the whole pool."""
        if gender_hint in ("male", "female"):
            gendered = [v for v in self._pool if v.gender == gender_hint]
            if gendered:
                return gendered
        return list(self._pool)

    def _pick(self, gender_hint: str | None) -> Voice | None:
        """A random voice from the candidates, PREFERRING one that is neither currently assigned
        (so distinct speakers sound distinct until the pool is exhausted) nor in the anti-repeat
        window (so the ambient cast doesn't cluster on a few voices). Relaxes step by step when the
        pool is too small to honor both constraints, so it never deadlocks. None on an empty pool.
        Records the pick in the anti-repeat window."""
        candidates = self._candidates(gender_hint)
        if not candidates:
            return None
        in_use = set(self._assigned.values())
        recent = set(self._recent)
        # Ideal: avoid both the in-use voices and the recently-handed-out ones; relax in order.
        fresh = ([v for v in candidates if v not in in_use and v not in recent]
                 or [v for v in candidates if v not in in_use]
                 or [v for v in candidates if v not in recent]
                 or candidates)
        choice = self._rng.choice(fresh)
        if self._recent.maxlen:
            self._recent.append(choice)
        return choice

    def random(self, gender_hint: str | None = None) -> Voice:
        """A fresh RANDOM voice with no memory — for per-line chatter, where each line should sound
        like a different anonymous speaker. Falls back to the fallback/persona on an empty pool."""
        return self._pick(gender_hint) or self._fallback or Voice("elevenlabs", "", "neutral")

    def assign(self, identity: str, gender_hint: str | None = None) -> Voice:
        """The sticky assignment: the SAME `identity` returns the SAME voice until it's cleared or
        evicted; a new identity draws a fresh random voice. Touching an identity marks it
        most-recently-used (LRU)."""
        key = str(identity or "")
        existing = self._assigned.get(key)
        if existing is not None:
            self._assigned.move_to_end(key)
            return existing
        voice = self._pick(gender_hint)
        if voice is None:
            return self._fallback or Voice("elevenlabs", "", "neutral")
        self._assigned[key] = voice
        self._assigned.move_to_end(key)
        if self._capacity is not None:
            while len(self._assigned) > self._capacity:
                self._assigned.popitem(last=False)   # evict the least-recently-used
        return voice

    def set_pool(self, pool, *, fallback: Voice | None = None) -> None:
        """Swap the pool (e.g. after the EL voice list lands or a settings change). Assignments to
        voices that are no longer in the pool are dropped so they re-cast from the new pool; stable
        ones are kept, so the player LRU survives a rebuild."""
        self._pool = list(pool or [])
        if fallback is not None:
            self._fallback = fallback
        keep = set(self._pool)
        for key in [k for k, v in self._assigned.items() if v not in keep]:
            del self._assigned[key]

    def clear(self) -> None:
        """Forget every assignment — call on a system jump for the comms (system-scoped) memory."""
        self._assigned.clear()
