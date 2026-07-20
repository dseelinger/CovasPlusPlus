"""Fake providers for unit tests (DESIGN §9). The canonical implementations live in
covas/providers/fakes.py so the dev-mode mock (wired via the factory) and the tests
share exactly one definition; this module just re-exports them for `from tests.fakes
import ...`."""
from __future__ import annotations

from covas.providers.fakes import FakeLLM, FakeSTT, FakeTTS, FakeWhisperCppModel

__all__ = ["FakeLLM", "FakeSTT", "FakeTTS", "FakeWhisperCppModel"]
