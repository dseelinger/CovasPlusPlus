"""Unit tests for the C9 audio-layer composition + controls. Offline, no device, no network."""
from __future__ import annotations

from covas.mixer import AudioControlsCapability, AudioLayer, BusMixer
from covas.mixer.comms import TIER_VERBATIM


class _FakeTTS:
    """Records comms/chatter lines; never touches audio hardware or the network."""

    def __init__(self):
        self.said: list[tuple[str, str | None]] = []

    def synth_pcm(self, text, voice_id=None):
        self.said.append((text, voice_id))
        return b"", 16000


def _layer(**audio):
    cfg = {"audio": {"mix_sample_rate": 16000, "cues": {"enabled": True},
                     "comms": {"enabled": True}, **audio}}
    mix = BusMixer(cfg)
    tts = _FakeTTS()
    return AudioLayer(cfg, mix, tts, ed_ctx=None, llm=None), tts, mix


def test_layer_ignores_non_ed_events():
    layer, tts, _ = _layer()
    layer.on_event({"type": "log", "text": "hi"})
    layer.on_event({"type": "status", "state": "Idle"})
    assert tts.said == []


def test_receive_text_npc_line_is_voiced_verbatim_on_comms():
    layer, tts, _ = _layer()
    layer.on_event({"type": "ed_event", "event": "ReceiveText", "Channel": "npc",
                    "From_Localised": "Station Control", "Message": "Docking granted."})
    assert tts.said == [("Docking granted.", None)]   # no comms voices configured -> default id


def test_receive_text_firehose_is_dropped():
    layer, tts, _ = _layer()
    layer.on_event({"type": "ed_event", "event": "ReceiveText", "Channel": "wing",
                    "From_Localised": "CMDR Stranger", "Message": "hey"})
    assert tts.said == []                              # a real-player broadcast is never voiced


def test_comms_off_suppresses_voicing():
    layer, tts, _ = _layer()
    layer.set_comms(False)
    layer.on_event({"type": "ed_event", "event": "ReceiveText", "Channel": "npc",
                    "From_Localised": "Control", "Message": "Cleared."})
    assert tts.said == []


def test_master_mute_suppresses_everything():
    layer, tts, _ = _layer()
    layer.set_muted(True)
    layer.on_event({"type": "ed_event", "event": "ReceiveText", "Channel": "npc",
                    "From_Localised": "Control", "Message": "Cleared."})
    assert tts.said == []


def test_interdiction_disabled_by_default_config():
    # No [audio.interdiction].enabled -> the layered cue stays silent.
    layer, tts, _ = _layer()
    layer.on_event({"type": "ed_event", "event": "Interdiction", "IsPlayer": False})
    assert tts.said == []


def test_interdiction_fires_when_enabled():
    layer, tts, _ = _layer(interdiction={"enabled": True})
    layer.on_event({"type": "ed_event", "event": "Interdiction", "IsPlayer": False})
    # the COVAS threat line + the pirate comms line reach TTS (the sting is a missing file -> skip)
    spoken = [t for t, _v in tts.said]
    assert any("shields up" in s.lower() or "brace" in s.lower() or "weapons" in s.lower()
               for s in spoken)


def test_variants_off_means_verbatim_comms():
    layer, _tts, _ = _layer()
    # With llm=None the comms voicer has no generator -> verbatim tier always.
    text, tier, _reason = layer._comms.resolve_text(  # noqa: SLF001
        _voiceable("Proceed to pad 7."), None)
    assert tier == TIER_VERBATIM and text == "Proceed to pad 7."


def _voiceable(msg, sender="Control", channel="npc"):
    from covas.mixer import evaluate
    return evaluate({"event": "ReceiveText", "Channel": channel,
                     "From_Localised": sender, "Message": msg})


def _pool(*ids):
    return [{"provider": "elevenlabs", "ref": vid, "gender": "neutral"} for vid in ids]


def _layer_pooled(pool_ids=("VA", "VB", "VC", "VD")):
    """A layer with a configured EL voice pool, so the cast picks real voice ids we can observe
    via the fake TTS (voice_id == the chosen Voice.ref)."""
    cfg = {"elevenlabs": {"voice_id": "PERSONA"},
           "audio": {"mix_sample_rate": 16000, "cues": {"enabled": True},
                     "comms": {"enabled": True},
                     "voices": {"cast_provider": "elevenlabs", "pool": list(_pool(*pool_ids))}}}
    return AudioLayer(cfg, BusMixer(cfg), _FakeTTS(), ed_ctx=None, llm=None), None


# ---- random-but-sticky cast (C10+) ---------------------------------------------------------

def test_comms_voice_is_sticky_within_a_system():
    layer, _ = _layer_pooled()
    layer._comms_play("Docking granted.", _voiceable("Docking granted.", sender="Station Control"))
    first = layer.tts.said[-1][1]
    assert first in {"VA", "VB", "VC", "VD"}                 # a pool voice, not the persona
    layer._comms_play("Proceed to pad 7.", _voiceable("Proceed to pad 7.", sender="Station Control"))
    assert layer.tts.said[-1][1] == first                   # same speaker -> same voice


def test_comms_voices_recast_on_system_jump_and_population_captured():
    layer, _ = _layer_pooled()
    layer._comms_play("Hello.", _voiceable("Hello.", sender="Station Control"))
    assert len(layer._comms_voices._assigned) == 1          # noqa: SLF001
    layer.on_event({"type": "ed_event", "event": "FSDJump",
                    "StarSystem": "Sol", "Population": 4200})
    assert layer._comms_voices._assigned == {}              # noqa: SLF001 — new system, fresh cast
    assert layer._population == 4200.0


def test_player_voice_persists_across_a_system_jump():
    layer, _ = _layer_pooled()
    layer._comms_play("gg", _voiceable("gg", sender="CMDR Ada", channel="player"))
    voice = layer.tts.said[-1][1]
    layer.on_event({"type": "ed_event", "event": "FSDJump",
                    "StarSystem": "Sol", "Population": 4200})
    layer._comms_play("wp", _voiceable("wp", sender="CMDR Ada", channel="player"))
    assert layer.tts.said[-1][1] == voice                   # players keep their voice across jumps


def test_chatter_uses_a_pool_voice_per_line():
    layer, _ = _layer_pooled()
    assert layer._speak_bus("Traffic's steady.", "comms") is True
    assert layer.tts.said[-1][1] in {"VA", "VB", "VC", "VD"}


def test_settings_rebuild_keeps_the_random_el_pool():
    # Regression: with no configured pool, the random default pool is seeded from the live EL list
    # fetched at startup. A later settings change rebuilds via apply_settings WITHOUT re-fetching;
    # that must reuse the cached EL list, not collapse the whole cast back to the single persona.
    cfg = {"elevenlabs": {"voice_id": "PERSONA"},
           "audio": {"mix_sample_rate": 16000, "cues": {"enabled": True},
                     "comms": {"enabled": True},
                     "voices": {"cast_provider": "elevenlabs"}}}  # no pool -> random default
    layer = AudioLayer(cfg, BusMixer(cfg), _FakeTTS(), ed_ctx=None, llm=None)
    layer.rebuild_cast(el_voices=[{"voice_id": "PERSONA"}, {"voice_id": "A"}, {"voice_id": "B"}])
    assert {v.ref for v in layer._cast.pool} == {"A", "B"}       # noqa: SLF001 — seeded, minus persona

    layer.apply_settings()                                       # e.g. user toggled a setting
    assert {v.ref for v in layer._cast.pool} == {"A", "B"}       # noqa: SLF001 — pool survives, not just PERSONA


# ---- voice controls ------------------------------------------------------------------------

def test_control_tool_toggles_layer_state():
    layer, _tts, _mix = _layer()
    cap = AudioControlsCapability(layer)
    assert "muted" in cap.run_tool("control_ambient_audio", {"target": "chatter", "action": "off"}).lower()
    assert layer.chatter_on is False
    cap.run_tool("control_ambient_audio", {"target": "all", "action": "off"})
    assert layer.muted is True
    cap.run_tool("control_ambient_audio", {"target": "all", "action": "on"})
    assert layer.muted is False


def test_control_music_volume_up_down():
    layer, _tts, _mix = _layer(buses={"music": {"volume_db": -12.0}})
    cap = AudioControlsCapability(layer)
    cap.run_tool("control_ambient_audio", {"target": "music", "action": "up"})
    assert layer.cfg["audio"]["buses"]["music"]["volume_db"] == -9.0
    cap.run_tool("control_ambient_audio", {"target": "music", "action": "down"})
    assert layer.cfg["audio"]["buses"]["music"]["volume_db"] == -12.0


def test_control_capability_help_and_event_forwarding():
    layer, tts, _ = _layer()
    cap = AudioControlsCapability(layer)
    meta = cap.help_meta()
    assert meta.category == "ambient audio" and meta.slots
    # on_event forwards to the layer
    cap.on_event({"type": "ed_event", "event": "ReceiveText", "Channel": "npc",
                  "From_Localised": "Control", "Message": "Hello."})
    assert tts.said == [("Hello.", None)]


def test_enable_flags_are_consumed_by_the_layer():
    # Every audio enable flag flows into a live component (the C9 no-dead-config audit).
    cfg = {"audio": {"mix_sample_rate": 16000, "enabled": True,
                     "cues": {"enabled": True}, "comms": {"enabled": False},
                     "interdiction": {"enabled": True}},
           "music": {"enabled": True}}
    layer = AudioLayer(cfg, BusMixer(cfg), _FakeTTS(), ed_ctx=None, llm=None)
    assert layer.chatter_on and layer.sfx_on          # [audio.cues].enabled
    assert layer.comms_on is False                    # [audio.comms].enabled
    assert layer.music_on is True                     # [music].enabled
    assert layer._interdiction.enabled is True        # [audio.interdiction].enabled (was dead)


def test_audio_controls_registers_cleanly_in_the_registry():
    from covas.capabilities.base import CapabilityRegistry
    layer, _tts, _ = _layer()
    reg = CapabilityRegistry()
    reg.register(AudioControlsCapability(layer))   # help metadata must be complete
    assert reg.contract_violations() == []
    assert any(t["name"] == "control_ambient_audio" for t in reg.tools())
