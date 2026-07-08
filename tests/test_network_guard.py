"""Proves the conftest network guard is active: in a (default, non-integration) unit
test, opening a socket must fail loudly. This is what makes an accidental real API call
in some other test blow up instead of silently billing money."""
from __future__ import annotations

import socket

import pytest


def test_opening_a_socket_is_blocked():
    with pytest.raises(Exception) as exc:
        socket.socket()
    assert "network" in str(exc.value).lower()


def test_create_connection_is_blocked():
    with pytest.raises(Exception):
        socket.create_connection(("example.com", 80), timeout=1)
