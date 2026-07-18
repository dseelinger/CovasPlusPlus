"""C9 app-composition wiring: the audio layer builds and connects with fakes (offline)."""
from __future__ import annotations

import threading

from covas.app import App
from covas.providers.elevenlabs_tts import ElevenLabsTTS
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path, *, audio_enabled: bool) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] test\n", encoding="utf-8")
    return {
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": "", "enabled": audio_enabled,
                  "content_root": str(tmp_path)},   # C11: keep the skeleton out of the repo
        "sound_cues": {},
        "keys": {"push_to_talk": "right ctrl"},
    }


def test_audio_disabled_by_default_no_mixer(tmp_path):
    app = App(_cfg(tmp_path, audio_enabled=False),
              llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    try:
        assert app.mixer is None and app.audio is None
    finally:
        app.shutdown()


def test_audio_enabled_composes_layer_and_registers_controls(tmp_path):
    app = App(_cfg(tmp_path, audio_enabled=True),
              llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    try:
        assert app.mixer is not None and app.audio is not None
        # the voice-control tool reached the registry (so the LLM can call it)
        assert any(t["name"] == "control_ambient_audio" for t in app.registry.tools())
        # a ReceiveText NPC line flows gate -> voicer -> the (fake) TTS on the comms bus
        app.audio.on_event({"type": "ed_event", "event": "ReceiveText", "Channel": "npc",
                            "From_Localised": "Station Control", "Message": "Docking granted."})
        assert app.tts.voices_seen  # synth_pcm was called for the comms line
    finally:
        app.shutdown()


def test_reload_audio_content_rescans_and_hotswaps_live(tmp_path):
    """#110: App.reload_audio_content re-scans the [audio].content_root drop-in folders and swaps
    them into the live AudioLayer — no restart. End-to-end through the same content_root seam."""
    app = App(_cfg(tmp_path, audio_enabled=True),
              llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    try:
        assert app.audio is not None
        # Drop a chatter line in AFTER startup (the startup scan saw the empty tree).
        chatter = tmp_path / "content" / "chatter" / "station_traffic.txt"
        chatter.parent.mkdir(parents=True, exist_ok=True)
        chatter.write_text("Dropped-in line.\n", encoding="utf-8")
        counts = app.reload_audio_content()
        assert counts["chatter"] == 1
        assert app.audio._registry.get("station_traffic").phrasings == ("Dropped-in line.",)  # noqa: SLF001
    finally:
        app.shutdown()


def test_reload_audio_content_no_layer_returns_empty(tmp_path):
    """With the audio layer off, the reload is a fail-soft no-op ({}), never an error."""
    app = App(_cfg(tmp_path, audio_enabled=False),
              llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())
    try:
        assert app.audio is None and app.reload_audio_content() == {}
    finally:
        app.shutdown()


def test_elevenlabs_provider_routes_speech_through_mixer_not_a_second_stream(monkeypatch, tmp_path):
    """COVAS speech with a mixer opens a mixer SpeechStream, NOT its own device stream."""
    from covas.mixer import COVAS, BusMixer, SpeechStream

    mix = BusMixer({"audio": {"mix_sample_rate": 16000}})
    captured: dict = {}

    def fake_speak(cfg, text, cancel, *, open_sink=None):
        captured["open_sink"] = open_sink
        if open_sink is not None:
            captured["sink"] = open_sink(16000)

    monkeypatch.setattr("covas.tts.speak", fake_speak)

    prov = ElevenLabsTTS({"elevenlabs": {"output_format": "pcm_16000"}}, mixer=mix, bus=COVAS)
    prov.speak("hello", threading.Event())
    assert captured["open_sink"] is not None                 # routed through the mixer
    assert isinstance(captured["sink"], SpeechStream)
    assert captured["sink"].bus == COVAS                     # on the clean COVAS bus

    # No mixer -> legacy path: open_sink is None (opens its own device stream as before).
    captured.clear()
    ElevenLabsTTS({"elevenlabs": {"output_format": "pcm_16000"}}).speak("hi", threading.Event())
    assert captured["open_sink"] is None
