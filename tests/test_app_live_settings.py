"""Unit tests: live-apply of Settings-page changes (issue #90).

Providers are hot-swapped WITHOUT a restart. These prove, offline and free (DESIGN §9):
  * an [llm]/[<provider>] change rebuilds self.llm; a [tts]/[<voice>] change rebuilds self.tts
    (passing the EXISTING mixer) — and an UNRELATED change rebuilds NEITHER;
  * a failing rebuild is fail-soft: it RETAINS the previous instance and never half-swaps;
  * the swap is safe against an in-flight turn — a turn runs on the provider it captured at its
    start (turn-local binding), even if self.tts is rebound mid-turn;
  * the classification drift guard: every settings_schema key is either live or restart-required.
"""
from __future__ import annotations

import threading
import time

import pytest

from covas import app as app_mod
from covas import settings_schema as schema
from covas.app import App, LIVE_SECTIONS, RESTART_REQUIRED
from tests.fakes import FakeLLM, FakeSTT, FakeTTS


def _cfg(tmp_path) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    return {
        "llm": {"provider": "anthropic"},
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "available_models": ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8"],
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "tts": {"provider": "elevenlabs"},
        "elevenlabs": {"model": "eleven_flash_v2_5", "voice_id": "abc",
                       "voice_name": "Sarah", "speed": 1.0},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "whisper": {"model": "small", "n_threads": 4},
        "keys": {"push_to_talk": "right ctrl"},
    }


@pytest.fixture(autouse=True)
def _no_persist(monkeypatch):
    """Keep update_settings/reset_setting from writing overrides.json to the real data dir."""
    monkeypatch.setattr(app_mod, "save_overrides", lambda o: None)


def _make_app(tmp_path):
    return App(_cfg(tmp_path), llm=FakeLLM(), tts=FakeTTS(), stt=FakeSTT())


def _join_reloads(timeout: float = 2.0) -> None:
    """Join the daemon rebuild threads (_reload_llm/_reload_tts run off the settings thread)."""
    deadline = time.time() + timeout
    for t in list(threading.enumerate()):
        if t.name in ("reload-llm", "reload-tts") and t.is_alive():
            t.join(timeout=max(0.0, deadline - time.time()))


def test_llm_rebuilds_on_llm_section_change(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    sentinel = FakeLLM(text="new")
    calls = []
    monkeypatch.setattr(app_mod, "make_llm", lambda cfg: (calls.append(cfg) or sentinel))
    monkeypatch.setattr(app_mod, "make_tts", lambda cfg, mixer=None: pytest.fail("TTS rebuilt"))

    app.update_settings({"anthropic": {"model": "claude-opus-5"}})
    _join_reloads()

    assert calls, "make_llm should have been called"
    assert app.llm is sentinel


def test_tts_rebuilds_on_tts_section_change_reusing_mixer(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    app.mixer = object()  # a stand-in mixer; the rebuild must pass THIS, never a new one
    sentinel = FakeTTS()
    seen = {}
    monkeypatch.setattr(app_mod, "make_tts",
                        lambda cfg, mixer=None: (seen.update(mixer=mixer) or sentinel))
    monkeypatch.setattr(app_mod, "make_llm", lambda cfg: pytest.fail("LLM rebuilt"))

    app.update_settings({"elevenlabs": {"voice_id": "xyz"}})
    _join_reloads()

    assert app.tts is sentinel
    assert seen["mixer"] is app.mixer  # the EXISTING mixer, reused


def test_no_rebuild_on_unrelated_change(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    prev_llm, prev_tts = app.llm, app.tts
    monkeypatch.setattr(app_mod, "make_llm", lambda cfg: pytest.fail("LLM rebuilt"))
    monkeypatch.setattr(app_mod, "make_tts", lambda cfg, mixer=None: pytest.fail("TTS rebuilt"))

    app.update_settings({"web_search": {"enabled": True}})
    _join_reloads()

    assert app.llm is prev_llm
    assert app.tts is prev_tts


def test_failed_llm_rebuild_retains_previous(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    prev = app.llm

    def _boom(cfg):
        raise RuntimeError("bad key")

    monkeypatch.setattr(app_mod, "make_llm", _boom)
    app.update_settings({"llm": {"provider": "openai"}})
    _join_reloads()

    assert app.llm is prev  # fail-soft: the working provider is kept, no half-swap


def test_turn_local_tts_survives_midturn_swap(tmp_path, monkeypatch):
    """A turn speaks through the TTS it captured at its start, even if self.tts is rebound while
    the LLM is still streaming (decision #2 next-turn semantics)."""
    app = _make_app(tmp_path)
    original_tts = app.tts
    swapped_tts = FakeTTS()

    class _SwappingLLM(FakeLLM):
        def stream_reply(self, *a, **k):
            app.tts = swapped_tts  # hot-swap lands MID-TURN, after the turn bound its local
            yield ("text", "Scoop some fuel, Commander.")

    app.llm = _SwappingLLM()
    app._run_turn("what's next", threading.Event())

    assert original_tts.spoken == ["Scoop some fuel, Commander."]
    assert swapped_tts.spoken == []  # the swap is deferred to the next turn


def test_input_device_change_routes_through_recorder_reconcile(tmp_path, monkeypatch):
    """A [audio].input_device change rebuilds the Recorder via the reconcile path (decision #7,
    the seam #89 reuses). An unrelated audio change must NOT trigger it."""
    app = _make_app(tmp_path)
    calls = []
    monkeypatch.setattr(app, "_reconcile_recorder", lambda: calls.append(True))

    app.update_settings({"audio": {"thinking_bed": False}})  # unrelated audio key
    assert calls == []

    app.update_settings({"audio": {"input_device": "Some Other Mic"}})
    assert calls == [True]


def test_speak_honors_turn_local_text_only(tmp_path):
    """_speak gates on the TURN-LOCAL text_only when given one, not the live flag (issue #90
    review): a mid-turn swap to a keyless provider must not silently drop a reply the turn
    started able to voice, and a turn that started keyless must not suddenly speak."""
    app = _make_app(tmp_path)

    app.text_only = True             # live: keyless NOW...
    voiced = FakeTTS()
    app._speak("hi", threading.Event(), tts=voiced, text_only=False)   # ...but the turn wasn't
    assert voiced.spoken == ["hi"]   # spoke on the captured provider despite the live flip

    app.text_only = False            # live: has a voice NOW...
    silent = FakeTTS()
    app._speak("hi", threading.Event(), tts=silent, text_only=True)    # ...but the turn started keyless
    assert silent.spoken == []       # stayed silent per the turn's OWN state


def test_reload_tts_refreshes_audio_layer_voice(tmp_path, monkeypatch):
    """A live TTS swap re-points the ambient audio layer too (issue #90 review half-swap fix):
    without this the layer keeps voicing ambient/comms/crew on the OLD provider."""
    app = _make_app(tmp_path)
    seen: dict = {}

    class _AudioStub:
        def set_providers(self, **kw):
            seen.update(kw)

    app.audio = _AudioStub()
    sentinel = FakeTTS()
    monkeypatch.setattr(app_mod, "make_tts", lambda cfg, mixer=None: sentinel)
    monkeypatch.setattr(app, "_build_cast_synth", lambda: "CAST")
    monkeypatch.setattr(app_mod, "make_llm", lambda cfg: pytest.fail("LLM rebuilt"))

    app.update_settings({"elevenlabs": {"voice_id": "xyz"}})
    _join_reloads()

    assert app.tts is sentinel
    assert seen.get("tts") is sentinel and seen.get("cast_synth") == "CAST"


def test_reload_llm_refreshes_audio_layer_generators(tmp_path, monkeypatch):
    """A live LLM swap re-points the ambient layer's chatter-flavor / comms-variant generators too
    (issue #90 review half-swap fix)."""
    app = _make_app(tmp_path)
    seen: dict = {}

    class _AudioStub:
        def set_providers(self, **kw):
            seen.update(kw)

    app.audio = _AudioStub()
    sentinel = FakeLLM()
    monkeypatch.setattr(app_mod, "make_llm", lambda cfg: sentinel)
    monkeypatch.setattr(app_mod, "make_tts", lambda cfg, mixer=None: pytest.fail("TTS rebuilt"))

    app.update_settings({"anthropic": {"model": "claude-opus-5"}})
    _join_reloads()

    assert app.llm is sentinel
    assert seen.get("llm") is sentinel and "cheap_model" in seen


def test_input_device_change_deferred_during_capture(tmp_path, monkeypatch):
    """A mic change that lands while PTT is held is DEFERRED, not applied mid-capture (issue #90
    review): rebuilding the Recorder under an open stream would drop the utterance + leak the
    stream. It applies at the capture boundary via _apply_pending_recorder (after stop() closes
    the old stream and on_key has cleared ptt_held)."""
    app = _make_app(tmp_path)
    builds: list = []
    monkeypatch.setattr(app_mod, "Recorder", lambda cfg: builds.append(True))

    app.ptt_held = True
    app._reconcile_recorder()                                # arrives mid-capture
    assert builds == [] and app._recorder_dirty is True      # deferred, not rebuilt

    app.ptt_held = False                                     # on_key clears this before on_ptt_up
    app._apply_pending_recorder()                            # capture boundary
    assert builds == [True] and app._recorder_dirty is False


def test_drift_guard_every_schema_key_is_classified():
    """Every settings key must fall under LIVE_SECTIONS ∪ RESTART_REQUIRED — a new, unclassified
    setting fails here until it's explicitly placed (single source of truth, decision #8)."""
    unclassified = []
    for s in schema.SCHEMA:
        top = s.key.split(".")[0]
        if s.key in RESTART_REQUIRED or top in LIVE_SECTIONS:
            continue
        unclassified.append(s.key)
    assert not unclassified, f"unclassified settings: {unclassified}"


def test_restart_and_live_are_disjoint_in_intent():
    """A key marked restart-required must not ALSO look purely live via its section being the only
    thing there — sanity that the two lists are curated, not accidental."""
    # audio.enabled / audio.mix_sample_rate are restart-required even though 'audio' is a live
    # section (most audio.* keys are live): the per-key RESTART_REQUIRED wins for those two.
    assert "audio.enabled" in RESTART_REQUIRED
    assert "audio" in LIVE_SECTIONS
    assert "ui" not in LIVE_SECTIONS and {"ui.host", "ui.port"} <= RESTART_REQUIRED
    assert "dev" not in LIVE_SECTIONS  # dev/test-only; not a UI setting (issue #130)


def test_dev_mock_not_a_setting_but_mechanism_lives(monkeypatch):
    """Issue #130: `dev.mock` is NOT a UI Setting (can't creep back onto the Settings page or be
    voice-toggled), but the underlying mechanism (`config.mock_enabled` on `[dev].mock`) is intact."""
    from covas.config import mock_enabled
    monkeypatch.delenv("COVAS_MOCK", raising=False)  # isolate from the env override
    assert "dev.mock" not in schema.by_key           # gone from the schema entirely
    assert not any(s.group == "Developer" for s in schema.SCHEMA)  # empty group removed too
    assert "dev.mock" not in RESTART_REQUIRED         # and off the restart-required list
    # The mechanism still reads [dev].mock from config (COVAS_MOCK env override tested elsewhere).
    assert mock_enabled({"dev": {"mock": True}}) is True
    assert mock_enabled({"dev": {"mock": False}}) is False
