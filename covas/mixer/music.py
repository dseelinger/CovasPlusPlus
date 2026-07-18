"""Ambient music (C7) — a curated, context-tagged local library, crossfaded on state changes.

Music is a CURATED, PRE-GENERATED library, never a live API. It rides the Music bus (C1),
crossfading between tracks when the game context changes. Three separable pieces so the logic
is pure/offline and only real playback needs a device:

  * `MusicLibrary` — the registry: context tag -> track set (from [music.tracks]).
  * `music_context(states)` — maps the live eligibility state set (C3) to ONE music context,
    by priority (combat > near-star > deep-space > populated > default). Deterministic.
  * crossfade envelopes — pure gain math over PCM buffers (equal-power), unit-testable.
  * `MusicDirector` — tracks the current context/track and decides transitions; pure. The app
    realizes a returned transition by crossfading track buffers on the Music bus.

Generation is a DELIBERATE SEAM, NOT a runtime dependency: Suno has no official public API
(mid-2026), so `generate_track` raises. Nothing in the playback path imports or awaits it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .buses import MUSIC
from .cues import Cue
from .eligibility import (
    DEEP_SPACE,
    HARDPOINTS,
    IN_DANGER,
    INTERDICTED,
    NEAR_STAR,
    POPULATED,
    SCOOPING_FUEL,
    UNPOPULATED,
)

# ---- context tags ----------------------------------------------------------------------------
CTX_COMBAT = "combat_adjacent"
CTX_NEAR_STAR = "near_star"
CTX_NEBULA = "nebula"
CTX_DEEP_SPACE = "deep_space"
CTX_POPULATED = "populated"
CTX_DEFAULT = "default"

MUSIC_CONTEXTS: tuple[str, ...] = (
    CTX_COMBAT, CTX_NEAR_STAR, CTX_NEBULA, CTX_DEEP_SPACE, CTX_POPULATED, CTX_DEFAULT,
)


def music_context(states) -> str:
    """Map the live eligibility state set to ONE music context, highest-priority first. Nebula
    isn't derivable from current game state (no such state token) — it's a supported library tag
    for future use, not auto-selected here. Deterministic."""
    s = {str(x) for x in (states or ())}
    if s & {"in_danger", "interdicted", "hardpoints"}:
        return CTX_COMBAT
    if s & {"near_star", "scooping_fuel"}:
        return CTX_NEAR_STAR
    if s & {"deep_space", "unpopulated"}:
        return CTX_DEEP_SPACE
    if "populated" in s:
        return CTX_POPULATED
    return CTX_DEFAULT


# ---- crossfade envelopes (pure) -------------------------------------------------------------
def equal_power_gains(n: int) -> tuple[np.ndarray, np.ndarray]:
    """(gain_out, gain_in) equal-power crossfade curves of length n: gain_out**2 + gain_in**2 == 1
    at every sample, so total acoustic power stays constant across the blend."""
    if n <= 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
    t = np.linspace(0.0, 1.0, n, dtype=np.float64)
    return np.cos(t * np.pi / 2).astype(np.float32), np.sin(t * np.pi / 2).astype(np.float32)


def fade_in(x: np.ndarray, n: int) -> np.ndarray:
    """Ramp the first `n` samples 0 -> 1 (equal-power). Pure; returns a new buffer."""
    x = np.asarray(x, dtype=np.float32).copy()
    n = min(int(n), x.shape[0])
    if n > 0:
        _, gin = equal_power_gains(n)
        x[:n] *= gin
    return x


def fade_out(x: np.ndarray, n: int) -> np.ndarray:
    """Ramp the last `n` samples 1 -> 0 (equal-power). Pure; returns a new buffer."""
    x = np.asarray(x, dtype=np.float32).copy()
    n = min(int(n), x.shape[0])
    if n > 0:
        gout, _ = equal_power_gains(n)
        x[x.shape[0] - n:] *= gout
    return x


def crossfade(a: np.ndarray, b: np.ndarray, overlap: int) -> np.ndarray:
    """Equal-power crossfade from `a` into `b` over `overlap` samples: a's tail fades out while
    b's head fades in, summed over the overlap region. Output length = len(a)+len(b)-overlap.
    With overlap <= 0 it's a plain concatenation. Pure + deterministic."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    overlap = int(min(overlap, a.shape[0], b.shape[0]))
    if overlap <= 0:
        return np.concatenate([a, b]).astype(np.float32)
    gout, gin = equal_power_gains(overlap)
    head = a[: a.shape[0] - overlap]
    blend = a[a.shape[0] - overlap:] * gout + b[:overlap] * gin
    tail = b[overlap:]
    return np.concatenate([head, blend, tail]).astype(np.float32)


# ---- the library ----------------------------------------------------------------------------
class MusicLibrary:
    """Context tag -> ordered track set. Deterministic selection with a default-context fallback.
    Track paths are local, git-ignored assets (supply your own); an empty context is simply
    silent."""

    def __init__(self, tracks: Optional[dict[str, list[str]]] = None) -> None:
        self._tracks: dict[str, list[str]] = {
            str(ctx): [str(p) for p in (paths or [])]
            for ctx, paths in (tracks or {}).items()
        }

    @classmethod
    def from_cfg(cls, cfg: dict) -> "MusicLibrary":
        tracks = (cfg.get("music", {}) or {}).get("tracks", {}) or {}
        return cls({str(k): list(v) for k, v in tracks.items() if isinstance(v, (list, tuple))})

    def contexts(self) -> list[str]:
        """Contexts that actually have at least one track."""
        return [c for c, t in self._tracks.items() if t]

    def tracks_for(self, context: str) -> list[str]:
        return list(self._tracks.get(context, []))

    def select(self, context: str, index: int = 0) -> str | None:
        """A track for `context`, rotated by `index`. Falls back to the default context, then
        None (silent) if neither has tracks. Deterministic — no randomness."""
        pool = self._tracks.get(context) or self._tracks.get(CTX_DEFAULT) or []
        if not pool:
            return None
        return pool[index % len(pool)]


@dataclass(frozen=True)
class MusicTransition:
    """A decided change for the app's music player to realize (via `crossfade`). `crossfade` is
    False for the very first track (a plain fade-in, nothing to blend from)."""

    from_track: Optional[str]
    to_track: str
    context: str
    crossfade: bool
    reason: str


class MusicDirector:
    """Tracks the current music context + track and decides transitions on state change. PURE
    (no device): `update()` returns a MusicTransition to realize, or None to keep playing. Off
    by default alongside the rest of the audio-cue layer."""

    def __init__(self, library: MusicLibrary, *, enabled: bool = False) -> None:
        self._lib = library
        self._enabled = enabled
        self._context: Optional[str] = None
        self._track: Optional[str] = None
        self._rot: int = 0

    @classmethod
    def from_cfg(cls, cfg: dict) -> "MusicDirector":
        m = cfg.get("music", {}) or {}
        return cls(MusicLibrary.from_cfg(cfg), enabled=bool(m.get("enabled", False)))

    def set_library(self, library: MusicLibrary) -> None:
        """Swap the track library WITHOUT touching the current context/track/rotation (live drop-in
        content reload, issue #110). The currently-playing track keeps playing and an in-progress
        crossfade is never interrupted — new tracks are picked up only on the NEXT genuine context
        change (a same-context `update()` still returns None). A single attribute rebind, so a
        concurrent `update()` reader sees the old or the new library, never a torn one."""
        self._lib = library

    @property
    def current_track(self) -> Optional[str]:
        return self._track

    @property
    def current_context(self) -> Optional[str]:
        return self._context

    def update(self, states) -> Optional[MusicTransition]:
        """Given the live eligibility state set, decide whether to change tracks. Returns a
        transition when the context changed (and the new context has a track), else None."""
        if not self._enabled:
            return None
        ctx = music_context(states)
        if ctx == self._context and self._track is not None:
            return None  # same context, already playing -> keep going
        track = self._lib.select(ctx, self._rot)
        if track is None:
            return None  # no music for this context -> leave the current track playing
        prev = self._track
        transition = MusicTransition(
            from_track=prev, to_track=track, context=ctx,
            crossfade=prev is not None,
            reason=f"context {self._context or '(none)'} -> {ctx}",
        )
        self._context = ctx
        self._track = track
        self._rot += 1
        return transition


# ---- cue-registry inventory (structural safety) ---------------------------------------------
# Each music context is represented as a cue so the registry contract covers it too (a real bus,
# a declared eligibility set). Nebula has no state token yet -> an empty (valid, silent) set: a
# documented seam. Music playback itself is continuous/crossfaded via MusicDirector, NOT a
# one-shot governed cue — these entries are the registry's drift-free inventory of contexts.
_CONTEXT_STATES: dict[str, set[str]] = {
    CTX_POPULATED: {POPULATED},
    CTX_DEEP_SPACE: {DEEP_SPACE, UNPOPULATED},
    CTX_NEAR_STAR: {NEAR_STAR, SCOOPING_FUEL},
    CTX_COMBAT: {IN_DANGER, INTERDICTED, HARDPOINTS},
    CTX_NEBULA: set(),
}


def music_cues(library: MusicLibrary) -> list[Cue]:
    """A cue per music context (bus MUSIC, the context's eligibility states, its track set as
    samples, the context tag) — the registry's inventory of music contexts."""
    return [
        Cue(f"music_{ctx}", MUSIC, frozenset(states), context_tag=ctx,
            samples=tuple(library.tracks_for(ctx)))
        for ctx, states in _CONTEXT_STATES.items()
    ]


def register_music(registry, library: MusicLibrary) -> None:  # noqa: ANN001 — a CueRegistry
    for cue in music_cues(library):
        registry.register(cue)


# ---- generation seam (NOT a runtime dependency) ---------------------------------------------
def generate_track(context: str) -> str:
    """SEAM ONLY — deliberately unimplemented. Suno has no official public API (mid-2026), so
    fresh-track generation is OUT of the runtime path. If an official API ever lands, implement
    it HERE; nothing in the playback path may import or await it. Kept as a marked hook so the
    intent is explicit rather than silently absent."""
    raise NotImplementedError(
        "Music generation is a deliberate seam, not a runtime dependency (no official Suno API). "
        f"Requested context: {context!r}."
    )
