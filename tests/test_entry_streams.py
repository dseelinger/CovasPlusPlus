"""Regression: the windowed-build std-stream sink must not crash on odd Unicode.

A windowed (console=False) frozen build leaves sys.stdout/stderr as None, so the entry point
swaps in a null sink. If that sink is opened with the Windows default (cp1252) encoding, the
first non-cp1252 glyph the app prints — a model that emits an emoji or Arabic, an em-dash —
raises UnicodeEncodeError and crashes the turn mid-reply. `_null_sink` must open utf-8 with
errors='replace' so such a glyph is dropped, never raised. Pure/offline — no window, no app.
"""
from __future__ import annotations

import run_covas_app


def test_null_sink_swallows_non_cp1252_without_raising():
    s = run_covas_app._null_sink()
    try:
        # emoji + Arabic + em-dash — all unencodable in cp1252; must not raise.
        s.write("glad to be home \U0001F642 مرحبا — done")
        s.flush()
    finally:
        s.close()


def test_ensure_writable_std_streams_replaces_none(monkeypatch):
    monkeypatch.setattr(run_covas_app.sys, "stdout", None)
    monkeypatch.setattr(run_covas_app.sys, "stderr", None)
    run_covas_app._ensure_writable_std_streams()
    for stream in (run_covas_app.sys.stdout, run_covas_app.sys.stderr):
        assert stream is not None
        stream.write("\U0001F680 éè —")  # rocket + accents + em-dash
        stream.flush()
