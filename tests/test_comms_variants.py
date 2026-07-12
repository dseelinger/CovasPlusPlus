"""Unit tests for the C5 comms variant pipeline — validator + verbatim fallback. Offline, no TTS."""
from __future__ import annotations

from covas.mixer import (
    CommsVoicer,
    CueGovernor,
    GovernorConfig,
    build_variant_prompt,
    clamp_tier,
    comms_voice_id,
    evaluate,
    validate_variant,
)
from covas.mixer.comms import TIER_PARAPHRASE, TIER_RIFF, TIER_VERBATIM, VoiceableComms


def _rt(channel="npc", *, msg="Docking request granted, proceed to pad 7", from_localised="Station Control"):
    return {"event": "ReceiveText", "From": "Station", "From_Localised": from_localised,
            "Message": msg, "Message_Localised": msg, "Channel": channel}


# ---- validator: the safety heart -----------------------------------------------------------

def test_clean_paraphrase_passes():
    ok, _ = validate_variant("Docking request granted, proceed to pad 7",
                             "You're cleared to dock; head to pad 7.")
    assert ok


def test_validator_rejects_added_proper_noun():
    ok, why = validate_variant("Docking request granted.", "Cleared to dock at Ackerman.")
    assert not ok and "Ackerman".lower() in why


def test_validator_rejects_changed_number():
    ok, why = validate_variant("Proceed to pad 7.", "Proceed to pad 9.")
    assert not ok and "number" in why
    # dropping the number is fine (a riff can omit detail)
    assert validate_variant("Proceed to pad 7.", "You're cleared in.")[0]
    # same value, different spelling ('07' vs '7') is NOT a change
    assert validate_variant("Pad 07 is yours.", "Head to pad 7.")[0]


def test_validator_rejects_invented_threat_or_alarm():
    assert not validate_variant("Docking granted.", "Docking granted — prepare to be destroyed.")[0]
    assert not validate_variant("Welcome, Commander.", "Mayday! We are under attack!")[0]
    # a threat word ALREADY in the source may be reused (same word form)
    assert validate_variant("We are under attack!", "Careful — we're under attack!")[0]


def test_validator_rejects_empty():
    assert not validate_variant("Docking granted.", "   ")[0]


# ---- tier clamping: player is never paraphrased --------------------------------------------

def test_clamp_tier():
    assert clamp_tier(TIER_RIFF, TIER_VERBATIM) == TIER_VERBATIM     # player ceiling
    assert clamp_tier(TIER_RIFF, TIER_RIFF) == TIER_RIFF
    assert clamp_tier(TIER_PARAPHRASE, TIER_RIFF) == TIER_PARAPHRASE


class _RecordingPlay:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    def __call__(self, text, voice):
        self.calls.append((text, voice))
        return self.ok


def test_player_line_is_voiced_verbatim_never_generated():
    rec = evaluate(_rt("player", msg="need backup at Sol", from_localised="CMDR Ada"))
    generated = []

    def gen(source, tier):
        generated.append((source, tier))
        return "totally different words"

    play = _RecordingPlay()
    out = CommsVoicer(play, generate=gen).voice(rec, tier=TIER_RIFF)
    assert out.spoken and out.tier == TIER_VERBATIM
    assert play.calls == [("need backup at Sol", "male")]
    assert generated == []                       # the generator was never even called


def test_npc_clean_riff_is_spoken():
    rec = evaluate(_rt("npc"))
    play = _RecordingPlay()
    out = CommsVoicer(play, generate=lambda s, t: "You're cleared to dock; head to pad 7.").voice(rec)
    assert out.spoken and out.tier == TIER_RIFF
    assert play.calls[0][0] == "You're cleared to dock; head to pad 7."


def test_tampered_variant_falls_back_to_verbatim():
    rec = evaluate(_rt("npc", msg="Docking granted."))
    play = _RecordingPlay()
    # Generator injects a proper noun + threat -> validator rejects -> verbatim source is voiced.
    out = CommsVoicer(play, generate=lambda s, t: "Docking granted at Ackerman — prepare to die.").voice(rec)
    assert out.spoken and out.tier == TIER_VERBATIM
    assert "fallback" in out.reason
    assert play.calls == [("Docking granted.", "default")]


def test_generator_error_falls_back_to_verbatim():
    rec = evaluate(_rt("npc", msg="Traffic is clear."))

    def boom(source, tier):
        raise RuntimeError("llm down")

    play = _RecordingPlay()
    out = CommsVoicer(play, generate=boom).voice(rec)
    assert out.spoken and out.tier == TIER_VERBATIM
    assert play.calls == [("Traffic is clear.", "default")]


def test_no_generator_means_verbatim():
    rec = evaluate(_rt("npc", msg="Fly safe, Commander."))
    play = _RecordingPlay()
    out = CommsVoicer(play).voice(rec)            # generate=None
    assert out.spoken and out.tier == TIER_VERBATIM
    assert play.calls[0][0] == "Fly safe, Commander."


def test_dropped_record_is_not_voiced():
    rec = evaluate(_rt("wing", from_localised="CMDR Stranger"))   # firehose -> dropped
    play = _RecordingPlay()
    out = CommsVoicer(play).voice(rec)
    assert not out.spoken and play.calls == []


# ---- governor + dedup integration ----------------------------------------------------------

def test_governor_dedup_suppresses_repeat_template():
    clock = [0.0]
    gov = CueGovernor(GovernorConfig(enabled=True, min_interval=0.0, default_cooldown=300.0),
                      clock=lambda: clock[0])
    voicer = CommsVoicer(_RecordingPlay(), governor=gov, clock=lambda: clock[0])

    a = evaluate(_rt("npc", msg="Traffic control: 3 ships ahead"))
    b = evaluate(_rt("npc", msg="Traffic control: 8 ships ahead"))   # same template, renumbered
    assert voicer.voice(a).spoken
    clock[0] = 30.0
    out = voicer.voice(b)
    assert not out.spoken and "governed" in out.reason


def test_failed_play_does_not_arm_governor():
    gov = CueGovernor(GovernorConfig(enabled=True, min_interval=0.0, default_cooldown=300.0),
                      clock=lambda: 0.0)
    voicer = CommsVoicer(_RecordingPlay(ok=False), governor=gov, clock=lambda: 0.0)
    rec = evaluate(_rt("npc", msg="Standby."))
    assert not voicer.voice(rec).spoken
    # cooldown not armed -> a second identical line is still allowed to try
    voicer2 = CommsVoicer(_RecordingPlay(ok=True), governor=gov, clock=lambda: 0.0)
    assert voicer2.voice(rec).spoken


# ---- prompt + voice-id bridge --------------------------------------------------------------

def test_build_variant_prompt_mentions_source_and_rules():
    p = build_variant_prompt("Docking granted.", TIER_RIFF)
    assert "Docking granted." in p and "reworded line" in p


def test_comms_voice_id_mapping_and_fallback():
    cfg = {"audio": {"comms": {"voices": {"male": "VID_M", "default": "VID_D"}}}}
    assert comms_voice_id(cfg, "male") == "VID_M"
    assert comms_voice_id(cfg, "female") == "VID_D"     # unset -> default
    assert comms_voice_id({}, "male") is None            # nothing configured -> provider default


def test_make_variant_generator_with_fake_llm():
    from covas.providers.fakes import FakeLLM

    from covas.mixer import make_variant_generator
    gen = make_variant_generator(FakeLLM(text="reworded line"), model="claude-haiku-4-5")
    assert gen("Docking granted.", TIER_PARAPHRASE) == "reworded line"


def test_voiceablecomms_shape_is_stable():
    rec = evaluate(_rt("npc"))
    assert isinstance(rec, VoiceableComms) and rec.voiceable
