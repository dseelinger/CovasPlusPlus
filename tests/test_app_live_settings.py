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
        "whisper": {"model": "small", "device": "cpu", "compute_type": "int8"},
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
    assert "dev" not in LIVE_SECTIONS and "dev.mock" in RESTART_REQUIRED
