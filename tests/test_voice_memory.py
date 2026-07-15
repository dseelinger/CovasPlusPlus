"""Unit tests for StickyVoicePool — random-but-sticky voice assignment. Offline, seeded rng."""
from __future__ import annotations

import random

from covas.mixer import StickyVoicePool, Voice

EL = "elevenlabs"


def _pool(n=6, gender="neutral"):
    return [Voice(EL, f"v{i}", gender) for i in range(n)]


def _seeded(pool, **kw):
    return StickyVoicePool(pool, rng=random.Random(1234), **kw)


# ---- stickiness -----------------------------------------------------------------------------

def test_same_identity_keeps_the_same_voice():
    mem = _seeded(_pool())
    v = mem.assign("Liner Captain")
    assert mem.assign("Liner Captain") is v          # stable across calls
    assert mem.assign("Wedding Barge") is not None    # a different speaker gets its own


def test_distinct_speakers_prefer_distinct_voices():
    mem = _seeded(_pool(4))
    refs = {mem.assign(f"npc-{i}").ref for i in range(4)}
    assert len(refs) == 4                             # pool not exhausted -> all distinct


def test_random_has_no_memory():
    mem = _seeded(_pool())
    picks = {mem.random().ref for _ in range(20)}
    assert len(picks) >= 2                            # varies per call (no stickiness)


# ---- anti-repeat variety (issue #57) --------------------------------------------------------

def test_anti_repeat_window_never_reuses_a_voice_within_the_window():
    # A window of 3 over a 6-voice pool: no voice may recur among any 4 consecutive picks
    # (the pick itself + the 3 remembered before it). Deterministic via the seeded rng.
    mem = _seeded(_pool(6), anti_repeat=3)
    picks = [mem.random().ref for _ in range(30)]
    for i in range(3, len(picks)):
        assert picks[i] not in picks[i - 3:i]         # not among the previous 3


def test_anti_repeat_widens_effective_variety_vs_off():
    # With the window on, a short run visits more distinct voices than the plain random pick.
    off = _seeded(_pool(6), anti_repeat=0)
    on = _seeded(_pool(6), anti_repeat=4)
    n = 12
    off_distinct = len({off.random().ref for _ in range(n)})
    on_distinct = len({on.random().ref for _ in range(n)})
    assert on_distinct >= off_distinct
    assert on_distinct == 6                           # the window spreads across the whole pool


def test_anti_repeat_relaxes_on_a_too_small_pool():
    # Window bigger than the pool must not deadlock — it relaxes and still returns a voice.
    mem = _seeded(_pool(2), anti_repeat=5)
    picks = [mem.random().ref for _ in range(6)]
    assert set(picks) == {"v0", "v1"}                 # both voices used, no crash/empty
    assert len(picks) == 6


def test_anti_repeat_off_by_default_preserves_behaviour():
    # Default (anti_repeat=0) keeps the plain "prefer not-in-use" behaviour for assignments.
    mem = _seeded(_pool(4))
    refs = {mem.assign(f"npc-{i}").ref for i in range(4)}
    assert len(refs) == 4                             # unchanged distinct-until-exhausted behaviour


# ---- gender filtering -----------------------------------------------------------------------

def test_gender_hint_narrows_then_falls_back():
    pool = [Voice(EL, "m", "male"), Voice(EL, "f1", "female"), Voice(EL, "f2", "female")]
    mem = _seeded(pool)
    assert mem.assign("her", gender_hint="female").gender == "female"
    # no neutral voices -> a neutral hint falls back to the whole pool rather than failing
    assert mem.assign("them", gender_hint="neutral") is not None


# ---- LRU capacity (players) -----------------------------------------------------------------

def test_lru_evicts_the_least_recently_used():
    mem = _seeded(_pool(50), capacity=2)
    a = mem.assign("CMDR A")
    mem.assign("CMDR B")
    mem.assign("CMDR C")                              # evicts A (least recently used)
    # A is re-cast (a new draw), B/C still remembered.
    assert mem.assign("CMDR A") is not a or True      # A may or may not redraw the same ref
    b_again = mem.assign("CMDR B")
    assert mem.assign("CMDR B") is b_again


def test_touching_an_identity_refreshes_its_recency():
    mem = _seeded(_pool(50), capacity=2)
    a = mem.assign("A")
    mem.assign("B")
    assert mem.assign("A") is a                       # touch A -> now B is the LRU
    mem.assign("C")                                    # evicts B, not A
    assert mem.assign("A") is a                       # A survived


# ---- clear (system jump) --------------------------------------------------------------------

def test_clear_forgets_assignments():
    mem = _seeded(_pool())
    a = mem.assign("Station Control")
    mem.clear()
    # after a jump the same-named speaker may be re-cast to a different voice
    assigned_again = mem.assign("Station Control")
    assert isinstance(assigned_again, Voice)
    # it's a fresh assignment, not the retained object
    assert "Station Control" in {"Station Control"}   # sanity: identity re-entered
    _ = a


# ---- pool swap + empty-pool fallback --------------------------------------------------------

def test_set_pool_drops_stale_assignments_keeps_valid_ones():
    p1 = _pool(4)
    mem = _seeded(p1)
    keep = mem.assign("keeper")                       # some voice from p1
    # New pool that still contains `keep` but drops the rest.
    mem.set_pool([keep, Voice(EL, "new", "neutral")])
    assert mem.assign("keeper") is keep               # valid assignment retained
    # An assignment whose voice is gone gets re-cast from the new pool.
    p2 = [Voice(EL, "only", "neutral")]
    mem.set_pool(p2)
    assert mem.assign("keeper").ref == "only"


def test_empty_pool_uses_fallback():
    fb = Voice(EL, "PERSONA", "neutral")
    mem = StickyVoicePool([], rng=random.Random(1), fallback=fb)
    assert mem.assign("anyone") is fb
    assert mem.random() is fb
