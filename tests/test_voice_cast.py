"""Unit tests for the C10 voice cast — deterministic assignment, routing, exclusion. Offline."""
from __future__ import annotations

from types import SimpleNamespace

from covas.mixer import CastSynth, Voice, build_cast
from covas.mixer.voices import EL, PIPER, VoiceCast


def _cfg(pool=None, *, cast_provider="piper", player_ref="", persona="PERSONA_ID"):
    return {"elevenlabs": {"voice_id": persona},
            "audio": {"voices": {"cast_provider": cast_provider, "player_ref": player_ref,
                                 "pool": pool or []}}}


def _pool(*genders):
    return [{"provider": "piper", "ref": f"v{i}.onnx", "gender": g} for i, g in enumerate(genders)]


def _rec(kind="npc", voice="default", sender="Station Control"):
    return SimpleNamespace(kind=kind, voice=voice, sender=sender, channel="npc")


# ---- deterministic assignment --------------------------------------------------------------

def test_assign_is_deterministic_and_stable():
    cast = build_cast(_cfg(_pool("male", "female", "neutral", "male")))
    a = cast.assign("CMDR Jameson")
    assert cast.assign("CMDR Jameson") is a           # same identity -> same voice, always
    assert cast.assign("Station Zero") is cast.assign("Station Zero")


def test_assign_spreads_across_the_pool():
    cast = build_cast(_cfg(_pool("neutral", "neutral", "neutral", "neutral")))
    used = {cast.assign(f"speaker-{i}").ref for i in range(40)}
    assert len(used) >= 3                              # different speakers spread across voices


def test_gender_hint_narrows_to_matching_voices():
    cast = build_cast(_cfg(_pool("male", "female", "female")))
    for i in range(20):
        assert cast.assign(f"her-{i}", gender_hint="female").gender == "female"
        assert cast.assign(f"him-{i}", gender_hint="male").gender == "male"


def test_gender_hint_ignored_when_no_matching_voice():
    cast = build_cast(_cfg(_pool("female", "female")))
    v = cast.assign("someone", gender_hint="male")     # no male voice -> falls back to the pool
    assert v.gender == "female"


def test_empty_pool_degrades_to_persona():
    cast = build_cast(_cfg([]))
    v = cast.assign("anyone")
    assert v.provider == EL and v.ref == "PERSONA_ID"  # today's single-voice behaviour


# ---- for_record: player vs npc -------------------------------------------------------------

def test_player_dm_gets_the_fixed_player_voice():
    cast = build_cast(_cfg(_pool("male", "female")))
    v = cast.for_record(_rec(kind="player", voice="male", sender="CMDR Ada"))
    assert v is cast.player() and v.gender == "male"   # first male pool voice


def test_npc_assigned_by_sender_identity():
    cast = build_cast(_cfg(_pool("neutral", "neutral", "neutral")))
    a = cast.for_record(_rec(sender="Ackerman Market"))
    b = cast.for_record(_rec(sender="Jameson Ring"))
    # each speaker is stable across calls
    assert cast.for_record(_rec(sender="Ackerman Market")).ref == a.ref
    assert cast.for_record(_rec(sender="Jameson Ring")).ref == b.ref


def test_player_ref_overrides_the_pool_pick():
    cast = build_cast(_cfg(_pool("male"), player_ref="voices/dm.onnx"))
    p = cast.player()
    assert p.ref == "voices/dm.onnx" and p.provider == PIPER and p.gender == "male"


# ---- provider routing ----------------------------------------------------------------------

def test_pool_entries_carry_their_provider():
    pool = [{"provider": "piper", "ref": "a.onnx", "gender": "male"},
            {"provider": "elevenlabs", "ref": "EL_A", "gender": "female"}]
    cast = build_cast(_cfg(pool), el_voices=[{"voice_id": "EL_A"}])
    provs = {v.ref: v.provider for v in cast.pool}
    assert provs == {"a.onnx": PIPER, "EL_A": EL}
    assert cast.persona().provider == EL               # COVAS/persona always ElevenLabs


# ---- the exclusion hook --------------------------------------------------------------------

def test_famous_el_voice_is_excluded_from_the_pool():
    pool = [{"provider": "elevenlabs", "ref": "GOOD", "gender": "neutral"},
            {"provider": "elevenlabs", "ref": "FAMOUS", "gender": "neutral"}]
    # el_voices is the allowed (famous-filtered) list -> FAMOUS isn't in it -> dropped.
    cast = build_cast(_cfg(pool), el_voices=[{"voice_id": "GOOD"}])
    refs = {v.ref for v in cast.pool}
    assert refs == {"GOOD"}


def test_injected_exclude_predicate_drops_a_voice():
    cast = build_cast(_cfg(_pool("male", "female")),
                      exclude=lambda v: v.ref == "v0.onnx")
    assert all(v.ref != "v0.onnx" for v in cast.pool)


# ---- random ElevenLabs default pool --------------------------------------------------------

def test_empty_pool_seeds_random_el_library_minus_persona():
    # No configured pool + random_el (default) + a live EL list -> the whole library (minus the
    # persona voice) becomes the random cast pool, all on ElevenLabs.
    cast = build_cast(_cfg([], persona="PERSONA_ID"),
                      el_voices=[{"voice_id": "PERSONA_ID"}, {"voice_id": "A"}, {"voice_id": "B"}])
    refs = {v.ref for v in cast.pool}
    assert refs == {"A", "B"}                          # persona excluded
    assert all(v.provider == EL for v in cast.pool)


def test_random_el_off_keeps_the_single_persona_voice():
    cfg = _cfg([], persona="PERSONA_ID")
    cfg["audio"]["voices"]["random_el"] = False
    cast = build_cast(cfg, el_voices=[{"voice_id": "A"}, {"voice_id": "B"}])
    assert cast.pool == []                             # opted out -> no random pool
    assert cast.assign("anyone").ref == "PERSONA_ID"   # degrades to the persona


def test_configured_pool_suppresses_random_seeding():
    pool = [{"provider": "elevenlabs", "ref": "PINNED", "gender": "neutral"}]
    cast = build_cast(_cfg(pool), el_voices=[{"voice_id": "PINNED"}, {"voice_id": "OTHER"}])
    assert {v.ref for v in cast.pool} == {"PINNED"}    # explicit pool wins over the library


# ---- CastSynth provider routing ------------------------------------------------------------

class _FakePiper:
    def __init__(self, path):
        self.path = path
        self.calls = 0

    def synth_pcm(self, text):
        self.calls += 1
        return b"PIPER", 16000


def test_cast_synth_routes_and_caches():
    el_calls = []
    loaded = []

    def el(text, vid):
        el_calls.append((text, vid))
        return b"EL", 22050

    def loader(path):
        loaded.append(path)
        return _FakePiper(path)

    cs = CastSynth(el_synth=el, piper_loader=loader)
    assert cs(Voice(EL, "VID"), "hi") == (b"EL", 22050)
    assert el_calls == [("hi", "VID")]
    assert cs(Voice(PIPER, "m.onnx"), "a") == (b"PIPER", 16000)
    cs(Voice(PIPER, "m.onnx"), "b")
    assert loaded == ["m.onnx"]                        # loaded once, then cached


def test_cast_synth_missing_backend_and_errors_are_silent():
    cs = CastSynth(el_synth=None, piper_loader=None)
    assert cs(Voice(EL, "x"), "t") == (b"", 16000)     # no backend -> silence
    boom = CastSynth(el_synth=lambda t, v: (_ for _ in ()).throw(RuntimeError("x")))
    assert boom(Voice(EL, "x"), "t") == (b"", 16000)   # error -> silence, never raises


def test_voicecast_synth_uses_injected_callable():
    seen = []
    cast = VoiceCast([Voice(PIPER, "a.onnx")], persona=Voice(EL, "p"), player=Voice(PIPER, "a.onnx"),
                     synth=lambda v, t: seen.append((v.ref, t)) or (b"X", 16000))
    assert cast.synth(Voice(PIPER, "a.onnx"), "hello") == (b"X", 16000)
    assert seen == [("a.onnx", "hello")]
