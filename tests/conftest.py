"""Unit-test guardrails.

The default `pytest` run must be free and hermetic (DESIGN §9). Two autouse fixtures enforce that:

  * `_block_network` blocks real socket access so an accidental API/ElevenLabs call fails
    loudly instead of silently going out to the wire (and billing money).
  * `_silence_audio` stubs sounddevice PLAYBACK so a unit test that drives a full turn (whose
    CuePlayer loads the shipped `covas/assets/cues/*.wav` and would call `sd.play`) doesn't blast
    audio through the developer's speakers. It's a no-op mute, not a behaviour change — no unit test
    opens a real device or asserts on playback (they use stub mixers / recorded submits), so results
    are identical, just quiet.

Tests that legitimately touch a real service/device are marked `@pytest.mark.integration` and skip
both guards — they're excluded from the default run anyway (`-m 'not integration'`).
"""
from __future__ import annotations

import socket

import pytest


class NetworkBlockedError(RuntimeError):
    """Raised when unit-test code tries to open a socket."""


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    # Keep a developer's ambient COVAS_MOCK=1 from silently flipping tests into mock
    # mode; tests that exercise the env override set it explicitly via monkeypatch.
    monkeypatch.delenv("COVAS_MOCK", raising=False)
    # Likewise, a stray COVAS_APP_DIR/COVAS_DATA_DIR (e.g. left set in the shell) must not
    # relocate config/overrides mid-suite. Tests that exercise these set them via monkeypatch.
    monkeypatch.delenv("COVAS_APP_DIR", raising=False)
    monkeypatch.delenv("COVAS_DATA_DIR", raising=False)

    # Integration tests may reach real services — leave their sockets alone.
    if request.node.get_closest_marker("integration"):
        return

    def _blocked(*_args, **_kwargs):
        raise NetworkBlockedError(
            "Unit tests must not touch the network (see tests/conftest.py). Mark tests "
            "that need a real service with @pytest.mark.integration."
        )

    # Cover both the socket class and the high-level connector most HTTP libs use.
    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture(autouse=True)
def _silence_audio(request, monkeypatch):
    """Mute real audio playback in unit tests. The legacy CuePlayer path (`covas/audio.py`, used
    when no bus mixer is active) calls `sounddevice.play()` on the shipped default cue WAVs, so a
    full-turn unit test would actually make noise. Patch the device-opening entry points to no-ops.
    Import sounddevice lazily and skip if it's absent (headless CI) — nothing to mute there."""
    if request.node.get_closest_marker("integration"):
        return  # integration tests may legitimately open a device
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 — no audio backend installed; nothing to silence
        return

    class _NullStream:
        """A stand-in for sd.OutputStream/RawOutputStream: accepts the same calls, plays nothing."""
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            pass

        def start(self):  # noqa: ANN201
            pass

        def write(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
            pass

        def stop(self):  # noqa: ANN201
            pass

        def abort(self):  # noqa: ANN201
            pass

        def close(self):  # noqa: ANN201
            pass

    monkeypatch.setattr(sd, "play", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sd, "wait", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sd, "stop", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sd, "OutputStream", _NullStream, raising=False)
    monkeypatch.setattr(sd, "RawOutputStream", _NullStream, raising=False)
