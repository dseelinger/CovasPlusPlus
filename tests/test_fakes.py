"""Unit tests for the fake providers + dev-mode mock wiring.

The fakes must satisfy the base.py Protocols (so they drop in for the real providers),
behave predictably for tests, and be what the factory returns when mock is enabled.
"""
from __future__ import annotations

import threading

from covas.config import mock_enabled
from covas.providers import factory
from covas.providers.base import LLMProvider, STTProvider, TTSProvider
from tests.fakes import FakeLLM, FakeSTT, FakeTTS

# --- Protocol conformance --------------------------------------------------

def test_fakes_satisfy_protocols():
    assert isinstance(FakeLLM(), LLMProvider)
    assert isinstance(FakeTTS(), TTSProvider)
    assert isinstance(FakeSTT(), STTProvider)


# --- behavior --------------------------------------------------------------

def test_fake_stt_returns_canned_text():
    assert FakeSTT(text="cmdr says hi").transcribe(object()) == "cmdr says hi"


def test_fake_tts_records_and_plays_nothing():
    tts = FakeTTS()
    tts.speak("hello", threading.Event())
    tts.speak("again", threading.Event())
    assert tts.spoken == ["hello", "again"]


def test_fake_llm_yields_text_and_forwards_events():
    seen: list[tuple[str, object]] = []
    llm = FakeLLM(text="mock reply", events=[("search", "elite dangerous"),
                                             ("usage", {"cost_usd": 0.0})])
    out = list(llm.stream_reply([], threading.Event(), lambda k, d: seen.append((k, d))))
    assert out == [("text", "mock reply")]
    assert seen == [("search", "elite dangerous"), ("usage", {"cost_usd": 0.0})]


def test_fake_llm_respects_cancel():
    cancel = threading.Event()
    cancel.set()
    out = list(FakeLLM().stream_reply([], cancel, lambda k, d: None))
    assert out == []


# --- factory returns fakes when mock is on ---------------------------------

def test_mock_enabled_from_config():
    assert mock_enabled({"dev": {"mock": True}}) is True
    assert mock_enabled({"dev": {"mock": False}}) is False
    assert mock_enabled({}) is False


def test_mock_enabled_env_overrides_config(monkeypatch):
    monkeypatch.setenv("COVAS_MOCK", "1")
    assert mock_enabled({"dev": {"mock": False}}) is True
    monkeypatch.setenv("COVAS_MOCK", "off")
    assert mock_enabled({"dev": {"mock": True}}) is False


def test_factory_returns_fakes_in_mock_mode():
    cfg = {"dev": {"mock": True}}
    assert isinstance(factory.make_llm(cfg), FakeLLM)
    assert isinstance(factory.make_tts(cfg), FakeTTS)
    assert isinstance(factory.make_stt(cfg), FakeSTT)
