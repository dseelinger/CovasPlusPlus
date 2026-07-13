"""Unit-test guardrails.

The default `pytest` run must be free and hermetic (DESIGN §9). This autouse fixture
blocks real network access so an accidental API/ElevenLabs/Ollama call in a unit test
fails loudly instead of silently going out to the wire (and billing money). Tests that
legitimately touch a real service are marked `@pytest.mark.integration` and skip the
guard — they're excluded from the default run anyway (`-m 'not integration'`).
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
