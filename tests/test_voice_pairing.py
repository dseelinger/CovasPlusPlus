"""Unit tests for auto persona->voice pairing (issue #96).

All offline: the matcher builds the batched LLM input from personas + the ElevenLabs catalog, the
result is cached and keyed so it recomputes ONLY on change, an explicit user voice always wins, and
every failure path yields NO pairing (never an exception). No network, no LLM, no ElevenLabs.
"""
from __future__ import annotations

import json
import types

from covas import elevenlabs as el
from covas import voice_pairing as vp
from tests.fakes import FakeLLM

_PERSONAS = [
    {"name": "Classic", "body": "Calm, professional ship AI. Measured and warm.", "source": "preset"},
    {"name": "Gruff", "body": "A grizzled old spacer. Blunt, gravelly, no nonsense.", "source": "preset"},
]

_VOICES = [
    {"voice_id": "v_warm", "name": "Sarah", "category": "premade",
     "labels": {"gender": "female", "age": "young", "accent": "american"},
     "description": "A calm, warm narrator voice."},
    {"voice_id": "v_gruff", "name": "Bruno", "category": "professional",
     "labels": {"gender": "male", "age": "old", "accent": "british"},
     "description": "A gravelly, weathered character voice."},
]


# ---- list_voices_detailed: the richer catalog (extend of list_voices) ------------------------

def test_list_voices_detailed_carries_metadata_and_filters_famous(monkeypatch):
    roster = [
        {"voice_id": "v1", "name": "Sarah", "category": "premade",
         "labels": {"gender": "female", "accent": "american"}, "description": "warm"},
        {"voice_id": "v2", "name": "John Wayne™", "category": "professional",
         "sharing": {"category": "famous"}, "labels": {"gender": "male"}},
    ]
    fake = types.SimpleNamespace(json=lambda: {"voices": roster}, raise_for_status=lambda: None)
    monkeypatch.setattr(el, "_key", lambda cfg: "test-key")
    monkeypatch.setattr(el.requests, "get", lambda *a, **k: fake)

    out = el.list_voices_detailed({"elevenlabs": {"api_key_file": "unused"}})
    assert [v["name"] for v in out] == ["Sarah"]              # famous dropped
    v = out[0]
    assert v["labels"]["gender"] == "female" and v["labels"]["accent"] == "american"
    assert v["description"] == "warm"


# ---- pairing_key: recompute ONLY when personas or the voice list change ----------------------

def test_pairing_key_is_stable_and_order_independent():
    ids = ["v_warm", "v_gruff"]
    k1 = vp.pairing_key(_PERSONAS, ids)
    k2 = vp.pairing_key(list(reversed(_PERSONAS)), list(reversed(ids)))
    assert k1 == k2                                           # order doesn't matter


def test_pairing_key_changes_on_persona_or_voice_change():
    base = vp.pairing_key(_PERSONAS, ["v_warm", "v_gruff"])
    edited = vp.pairing_key(
        [{**_PERSONAS[0], "body": "Rewritten body."}, _PERSONAS[1]], ["v_warm", "v_gruff"])
    added_voice = vp.pairing_key(_PERSONAS, ["v_warm", "v_gruff", "v_new"])
    assert base != edited                                    # a persona edit busts the key
    assert base != added_voice                               # a new account voice busts the key


# ---- the batched LLM input the matcher builds -----------------------------------------------

def test_build_pairing_prompt_includes_personas_and_catalog():
    prompt = vp.build_pairing_prompt(_PERSONAS, _VOICES)
    assert "Classic" in prompt and "grizzled old spacer" in prompt      # persona name + body
    assert "voice_id=v_warm" in prompt and "name='Sarah'" in prompt     # catalog id + name
    assert "gender=female" in prompt and "gravelly" in prompt           # labels + description
    assert '"pairings"' in prompt                                       # the requested JSON shape


# ---- parse + validate: invented ids / unknown personas are dropped ---------------------------

def test_parse_pairing_response_validates_and_canonicalizes():
    reply = json.dumps({"pairings": [
        {"persona": "classic", "voice_id": "v_warm", "reason": "warm fits"},   # ci name -> canonical
        {"persona": "Gruff", "voice_id": "v_gruff"},
        {"persona": "Gruff", "voice_id": "v_bogus"},        # invalid id -> dropped
        {"persona": "Nobody", "voice_id": "v_warm"},        # unknown persona -> dropped
    ]})
    out = vp.parse_pairing_response(reply, {"v_warm", "v_gruff"}, ["Classic", "Gruff"])
    assert out == {"Classic": "v_warm", "Gruff": "v_gruff"}


def test_parse_pairing_response_tolerates_fenced_or_garbage():
    fenced = "Sure!\n```json\n{\"pairings\":[{\"persona\":\"Classic\",\"voice_id\":\"v_warm\"}]}\n```"
    assert vp.parse_pairing_response(fenced, {"v_warm"}, ["Classic"]) == {"Classic": "v_warm"}
    assert vp.parse_pairing_response("not json at all", {"v_warm"}, ["Classic"]) == {}
    assert vp.parse_pairing_response("", {"v_warm"}, ["Classic"]) == {}


# ---- pair_voices: cache-keyed, recompute only on change, fail-soft ----------------------------

class _CountingGen:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        return self.reply


def _reply():
    return json.dumps({"pairings": [
        {"persona": "Classic", "voice_id": "v_warm"},
        {"persona": "Gruff", "voice_id": "v_gruff"},
    ]})


def test_pair_voices_computes_saves_then_reuses_cache(tmp_path):
    cache = tmp_path / "voice_pairings.json"
    gen = _CountingGen(_reply())

    first = vp.pair_voices(_PERSONAS, _VOICES, gen, cache_path=cache)
    assert first is not None and not first.from_cache
    assert first.mapping == {"Classic": "v_warm", "Gruff": "v_gruff"}
    assert gen.calls == 1 and cache.exists()                 # computed once, persisted

    # A second run with the SAME personas + voices reuses the cache — no second LLM call.
    second = vp.pair_voices(_PERSONAS, _VOICES, gen, cache_path=cache)
    assert second is not None and second.from_cache
    assert second.mapping == first.mapping
    assert gen.calls == 1                                     # generator NOT called again


def test_pair_voices_recomputes_when_the_set_changes(tmp_path):
    cache = tmp_path / "voice_pairings.json"
    gen = _CountingGen(_reply())
    vp.pair_voices(_PERSONAS, _VOICES, gen, cache_path=cache)
    assert gen.calls == 1
    # Add an account voice -> the key changes -> recompute.
    voices2 = _VOICES + [{"voice_id": "v_new", "name": "Zoe", "labels": {}, "description": ""}]
    vp.pair_voices(_PERSONAS, voices2, gen, cache_path=cache)
    assert gen.calls == 2


def test_pair_voices_failsoft_returns_none(tmp_path):
    cache = tmp_path / "voice_pairings.json"
    # No generator, no personas, no voices, and a raising generator all yield None (no crash).
    assert vp.pair_voices(_PERSONAS, _VOICES, None, cache_path=cache) is None
    assert vp.pair_voices([], _VOICES, _CountingGen(_reply()), cache_path=cache) is None
    assert vp.pair_voices(_PERSONAS, [], _CountingGen(_reply()), cache_path=cache) is None

    def boom(_prompt):
        raise RuntimeError("llm down")

    assert vp.pair_voices(_PERSONAS, _VOICES, boom, cache_path=cache) is None
    assert not cache.exists()                                # nothing persisted on failure


def test_pair_voices_drops_a_cached_voice_no_longer_in_the_catalog(tmp_path):
    cache = tmp_path / "voice_pairings.json"
    # Seed the cache directly with a mapping whose Gruff voice is gone from the current catalog.
    key = vp.pairing_key(_PERSONAS, ["v_warm", "v_gruff"])
    vp.save_cache(cache, key, {"Classic": "v_warm", "Gruff": "v_gone"})
    # Same key (compute is by ids, and we pass the same ids), but v_gone isn't a real voice now.
    only_warm = [_VOICES[0], {"voice_id": "v_gruff", "name": "Bruno", "labels": {}, "description": ""}]
    res = vp.pair_voices(_PERSONAS, only_warm, None, cache_path=cache)
    assert res is not None and res.mapping == {"Classic": "v_warm"}   # stale voice filtered out


# ---- the thin LLM adapter accumulates streamed text -----------------------------------------

def test_make_pairing_generator_accumulates_and_passes_model():
    llm = FakeLLM(text='{"pairings": []}')
    gen = vp.make_pairing_generator(llm, model="cheap-model")
    assert gen("some prompt") == '{"pairings": []}'
    assert llm.model_seen == "cheap-model"                   # cheap tier threaded through


# ---- voice_for_persona: an EXPLICIT user choice ALWAYS wins ----------------------------------

def test_voice_for_persona_explicit_beats_pairing():
    pairings = {"Classic": "v_warm", "Gruff": "v_gruff"}
    explicit = {"Gruff": "v_user_pick"}
    assert vp.voice_for_persona(explicit, pairings, "Gruff") == "v_user_pick"   # user wins
    assert vp.voice_for_persona(explicit, pairings, "classic") == "v_warm"      # ci, paired default
    assert vp.voice_for_persona({}, {}, "Classic") is None                      # neither -> keep default
    assert vp.voice_for_persona(explicit, pairings, "") is None


# ---- locale-aware voice pairing (issue #182 layer 4 / #198) ----------------------------------
# An Edge/Azure catalog: each voice is a normalized {ref, name, gender, locale} dict. Enough
# German + English voices to prove the steer picks a de-* voice and respects gender.
_LOCALE_VOICES = [
    {"ref": "en-US-AriaNeural", "name": "Aria", "gender": "female", "locale": "en-US"},
    {"ref": "en-US-GuyNeural", "name": "Guy", "gender": "male", "locale": "en-US"},
    {"ref": "de-DE-KatjaNeural", "name": "Katja", "gender": "female", "locale": "de-DE"},
    {"ref": "de-DE-ConradNeural", "name": "Conrad", "gender": "male", "locale": "de-DE"},
    {"ref": "de-AT-JonasNeural", "name": "Jonas", "gender": "male", "locale": "de-AT"},
]


def test_pick_language_voice_no_language_is_noop():
    # No target code (unmapped/blank reply language) -> keep the current voice, no steer.
    out = vp.pick_language_voice(_LOCALE_VOICES, None, current="en-US-GuyNeural")
    assert out.voice_id == "en-US-GuyNeural" and out.steered is False and out.mismatch is False


def test_pick_language_voice_keeps_a_voice_that_already_speaks_it():
    out = vp.pick_language_voice(_LOCALE_VOICES, "de", current="de-DE-KatjaNeural")
    assert out.voice_id == "de-DE-KatjaNeural" and out.steered is False and out.mismatch is False


def test_pick_language_voice_steers_a_mispronouncing_default_and_keeps_gender():
    # reply=German with a male English voice -> steer to a de-* voice of the SAME gender.
    out = vp.pick_language_voice(_LOCALE_VOICES, "de", current="en-US-GuyNeural")
    assert out.steered is True and out.mismatch is False
    assert out.voice_id == "de-DE-ConradNeural"   # male German, not the female Katja


def test_pick_language_voice_respects_explicit_choice_but_flags_mismatch():
    # An EXPLICIT user voice is NOT overridden — we flag the mismatch instead of steering.
    out = vp.pick_language_voice(_LOCALE_VOICES, "de", current="en-US-GuyNeural", explicit=True)
    assert out.voice_id == "en-US-GuyNeural" and out.steered is False and out.mismatch is True


def test_pick_language_voice_flags_when_no_voice_speaks_the_language():
    english_only = [v for v in _LOCALE_VOICES if v["locale"].startswith("en-")]
    out = vp.pick_language_voice(english_only, "de", current="en-US-GuyNeural")
    assert out.voice_id == "en-US-GuyNeural" and out.steered is False and out.mismatch is True


def test_pick_language_voice_untagged_provider_is_left_alone():
    # ElevenLabs/OpenAI voices carry no locale -> assumed multilingual, never steered.
    untagged = [{"voice_id": "el_multi", "name": "Rachel", "gender": "female", "locale": ""}]
    out = vp.pick_language_voice(untagged, "de", current="el_multi")
    assert out.voice_id == "el_multi" and out.steered is False and out.mismatch is False


def _cfg(provider: str, voice: str, reply: str = "German", match: bool = True) -> dict:
    key = {"edge": ("edge", "voice"), "azure": ("azure", "voice"),
           "elevenlabs": ("elevenlabs", "voice_id")}[provider]
    return {
        "tts": {"provider": provider},
        key[0]: {key[1]: voice},
        "language": {"reply": reply, "match_voice": match},
    }


def test_reply_voice_patch_builds_provider_patch_for_edge():
    patch, out = vp.reply_voice_patch(_cfg("edge", "en-US-GuyNeural"), _LOCALE_VOICES)
    assert patch == {"edge": {"voice": "de-DE-ConradNeural"}}
    assert out.steered is True


def test_reply_voice_patch_none_when_already_correct():
    patch, out = vp.reply_voice_patch(_cfg("edge", "de-DE-KatjaNeural"), _LOCALE_VOICES)
    assert patch is None and out.steered is False and out.mismatch is False


def test_reply_voice_patch_english_reply_never_steers():
    # English reply with an English voice -> no change (the common case stays put).
    patch, out = vp.reply_voice_patch(_cfg("edge", "en-US-AriaNeural", reply="English"),
                                      _LOCALE_VOICES)
    assert patch is None and out.steered is False


def test_reply_voice_patch_honors_match_voice_opt_out():
    patch, _out = vp.reply_voice_patch(_cfg("edge", "en-US-GuyNeural", match=False), _LOCALE_VOICES)
    assert patch is None


def test_reply_voice_patch_skips_untagged_provider():
    # ElevenLabs is multilingual; even with a non-English reply there's nothing to steer.
    patch, out = vp.reply_voice_patch(_cfg("elevenlabs", "el_multi"),
                                      [{"voice_id": "el_multi", "locale": ""}])
    assert patch is None and out.mismatch is False
