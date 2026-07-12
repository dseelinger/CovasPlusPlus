"""EXHAUSTIVE tests for the C4 comms channel gate — the core safety contract. Offline, no TTS.

The one rule that must never break: the classifier NEVER returns "voice" for a real-player
broadcast. Everything else fails closed — an unclassifiable line is dropped.
"""
from __future__ import annotations

import pytest

from covas.mixer import (
    Cue,
    CueGovernor,
    GovernorConfig,
    capture,
    classify,
    dedup_key,
    evaluate,
    is_receive_text,
    message_template,
)
from covas.mixer.comms import (
    FIREHOSE,
    TIER_RIFF,
    TIER_VERBATIM,
    VOICE_DEFAULT,
    VOICE_FEMALE,
    VOICE_MALE,
    npc_voice,
)


def _rt(channel=None, *, msg="Message text", from_localised="Station Control", from_raw="Station"):
    ev = {"event": "ReceiveText", "From": from_raw, "From_Localised": from_localised,
          "Message": msg, "Message_Localised": msg}
    if channel is not None:
        ev["Channel"] = channel
    return ev


# ---- the exhaustive channel matrix ---------------------------------------------------------

def test_player_channel_is_voiced_verbatim_male():
    d = classify(_rt("player", from_localised="CMDR Jameson"))
    assert d.voiceable and d.kind == "player"
    assert d.voice == VOICE_MALE and d.max_tier == TIER_VERBATIM


def test_player_channel_voiced_even_with_cmdr_prefix():
    # The CMDR prefix is EXPECTED on a real player DM and must not gate the 'player' channel.
    assert classify(_rt("player", from_localised="CMDR Zeta")).voiceable


def test_npc_channel_is_variant_eligible():
    d = classify(_rt("npc", from_localised="Station Control"))
    assert d.voiceable and d.kind == "npc" and d.max_tier == TIER_RIFF


@pytest.mark.parametrize("channel", sorted(FIREHOSE))
def test_every_firehose_channel_is_dropped(channel):
    d = classify(_rt(channel, from_localised="CMDR Somebody"))
    assert not d.voiceable and d.kind == "dropped"


def test_ambiguous_non_cmdr_is_voiced_as_npc():
    for channel in (None, "", "weird_new_channel"):
        d = classify(_rt(channel, from_localised="Orbital Traffic"))
        assert d.voiceable and d.kind == "npc", channel


def test_ambiguous_cmdr_prefixed_is_dropped():
    for channel in (None, "", "weird_new_channel"):
        d = classify(_rt(channel, from_localised="CMDR Stranger"))
        assert not d.voiceable and d.kind == "dropped", channel


def test_ambiguous_cmdr_detected_via_raw_from_decorate():
    # No From_Localised prefix, but the raw From is $cmdr_decorate-wrapped -> still a commander.
    ev = _rt(None, from_localised="Stranger", from_raw="$cmdr_decorate:#name=Stranger;")
    assert not classify(ev).voiceable


def test_missing_channel_key_entirely():
    ev = {"event": "ReceiveText", "From_Localised": "Beacon", "Message": "ping"}
    assert classify(ev).voiceable          # non-CMDR ambiguous -> npc-like
    ev2 = {"event": "ReceiveText", "From_Localised": "CMDR Ping", "Message": "ping"}
    assert not classify(ev2).voiceable     # CMDR ambiguous -> dropped


def test_non_dict_and_garbage_fail_closed():
    assert not classify(None).voiceable
    assert not classify("nope").voiceable


def test_classifier_never_voices_a_real_player_broadcast():
    # The single invariant, asserted across every firehose channel with a CMDR sender.
    for channel in FIREHOSE:
        assert not classify(_rt(channel, from_localised="CMDR RandomStranger")).voiceable


# ---- voice selection (deterministic, never random) -----------------------------------------

def test_npc_voice_is_deterministic_by_honorific():
    assert npc_voice({"From_Localised": "Mrs Hedley"}) == VOICE_FEMALE
    assert npc_voice({"From_Localised": "Sir Reginald"}) == VOICE_MALE
    assert npc_voice({"From_Localised": "Station Control"}) == VOICE_DEFAULT
    # Same input -> same output, always.
    assert npc_voice({"From_Localised": "Lady Ada"}) == npc_voice({"From_Localised": "Lady Ada"})


# ---- empty message is not voiceable (fail-closed) ------------------------------------------

def test_empty_message_is_not_voiceable():
    rec = evaluate(_rt("npc", msg="   "))
    assert not rec.voiceable and rec.reason == "empty message"


# ---- template-identity dedup ---------------------------------------------------------------

def test_template_collapses_numbers_and_sender_names():
    a = _rt("npc", msg="Docking granted, pad 07", from_localised="Jameson Ring")
    b = _rt("npc", msg="Docking granted, pad 12", from_localised="Ackerman Market")
    assert dedup_key(a) == dedup_key(b)            # renumbered same announcement -> one template


def test_template_masks_allcaps_and_digits():
    assert message_template("PAD 09 for CMDR", sender_tokens=()) == "* # for *"
    # distinct announcements stay distinct
    assert dedup_key(_rt("npc", msg="Welcome to the station")) != \
        dedup_key(_rt("npc", msg="Prepare for combat"))


def test_dedup_wires_into_the_c3_governor():
    # Two renumbered instances share a dedup_key; a governor keyed on it cools the second down,
    # so station spam isn't re-voiced per jump.
    a = _rt("npc", msg="Traffic control: 3 ships ahead")
    b = _rt("npc", msg="Traffic control: 8 ships ahead")
    key = dedup_key(a)
    assert key == dedup_key(b)
    gov = CueGovernor(GovernorConfig(enabled=True, min_interval=0.0, default_cooldown=300.0))
    cue_a = Cue(key, "comms", {"docked"})
    cue_b = Cue(dedup_key(b), "comms", {"docked"})
    assert gov.allow(cue_a, 0.0)[0]
    gov.mark_fired(cue_a, 0.0)
    assert not gov.allow(cue_b, 30.0)[0]           # same template -> cooled down


# ---- capture guards non-ReceiveText --------------------------------------------------------

def test_capture_only_handles_receive_text():
    assert capture({"event": "FSDJump"}) is None
    assert capture({"type": "log"}) is None
    rec = capture(_rt("npc"))
    assert rec is not None and rec.voiceable
    assert is_receive_text(_rt("npc")) and not is_receive_text({"event": "Docked"})


# ---- the record carries what C5 needs ------------------------------------------------------

def test_record_shape_for_c5():
    rec = evaluate(_rt("player", from_localised="CMDR Ada", msg="need backup at Sol"))
    assert rec.voiceable and rec.kind == "player"
    assert rec.text == "need backup at Sol"
    assert rec.voice == VOICE_MALE and rec.max_tier == TIER_VERBATIM
    assert rec.sender == "CMDR Ada"
    dropped = evaluate(_rt("wing", from_localised="CMDR Ada"))
    assert not dropped.voiceable and dropped.voice == "" and dropped.max_tier == ""
