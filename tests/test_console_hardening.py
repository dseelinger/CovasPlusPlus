"""Unit test: the console-hardening that keeps a non-cp1252 glyph in a reply from crashing
the worker mid-stream (covas/app.py `_harden_streams`).

This is the deterministic replacement for the manual "odd Unicode" robustness step: a VOICE
interaction can't reliably feed the console a real non-cp1252 glyph (Whisper strips accents,
COVAS describes symbols in words, and accents/em-dashes are IN cp1252 anyway), so the actual
safety mechanism belongs here where we can exercise it directly.
"""
from __future__ import annotations

import io

import pytest

from covas.app import _harden_streams

# Glyphs the default Windows console (cp1252) genuinely cannot encode: a rightwards arrow,
# a rocket emoji, and a CJK character. These are exactly what a Claude reply can contain.
ODD = "arrow → rocket \U0001F680 kanji 日 yen ¥\n"


def _cp1252_console() -> io.TextIOWrapper:
    """A stand-in for the crashy default Windows console: cp1252 with strict errors."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="")


def test_unhardened_cp1252_console_crashes_on_odd_glyph():
    """Establishes the hazard: without hardening, writing a non-cp1252 glyph raises."""
    s = _cp1252_console()
    with pytest.raises(UnicodeEncodeError):
        s.write(ODD)
        s.flush()


def test_harden_streams_makes_odd_glyphs_safe():
    """After hardening, the same write does NOT raise — the glyph is emitted (utf-8), not fatal."""
    s = _cp1252_console()
    _harden_streams([s])
    s.write(ODD)          # must not raise
    s.flush()
    assert s.buffer.getvalue()          # bytes were actually written
    assert "→".encode("utf-8") in s.buffer.getvalue()   # the arrow survived as utf-8


def test_harden_streams_is_best_effort_on_odd_streams():
    """A stream lacking reconfigure() must be tolerated, not raised on (fail-soft)."""
    class _NoReconfigure:
        pass
    _harden_streams([_NoReconfigure()])   # must not raise
