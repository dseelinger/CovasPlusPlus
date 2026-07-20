"""Unit tests for opt-in crash capture (issue #186) — offline, free.

Redaction + report formatting are pure; write_report is exercised against a tmp logs dir. The
default-OFF contract and the secret/PII scrubbing are the load-bearing guarantees.
"""
from __future__ import annotations

import sys

from covas import crashlog


def _cfg(enabled=False, logdir="logs"):
    return {"crash_report": {"enabled": enabled}, "logging": {"dir": logdir},
            "llm": {"provider": "anthropic"}, "tts": {"provider": "edge"}}


def test_off_by_default():
    assert crashlog.enabled({}) is False
    assert crashlog.enabled(_cfg(enabled=False)) is False
    assert crashlog.enabled(_cfg(enabled=True)) is True


def test_redaction_scrubs_keys_paths_and_dpapi():
    raw = ("sk-ant-abcdef, DPAPI:QUJDREVGe, xi-api-key: 9f8e7d6c5b, "
           r"C:\Users\Doug\AppData\Roaming\COVAS++\logs")
    out = crashlog.redact(raw)
    assert "sk-ant-abcdefg" not in out and "sk-<redacted>" in out
    assert "DPAPI:QUJDREVGe" not in out and "DPAPI:<redacted>" in out
    assert "9f8e7d6c5b" not in out
    assert r"Users\Doug" not in out and r"Users\<user>" in out


def test_format_report_is_redacted_and_has_context():
    try:
        raise ValueError("boom sk-ant-supersecretkey12345")
    except ValueError as e:
        rep = crashlog.format_report(type(e), e, e.__traceback__, _cfg(True),
                                     version="9.9.9", when="2026-07-19T00:00:00")
    assert "COVAS++ crash report" in rep and "version: 9.9.9" in rep
    assert "providers: llm=anthropic tts=edge" in rep
    assert "ValueError" in rep and "boom" in rep
    assert "supersecretkey12345" not in rep          # the key in the message is scrubbed


def test_write_report_noop_when_disabled(tmp_path):
    try:
        raise RuntimeError("x")
    except RuntimeError as e:
        path = crashlog.write_report(_cfg(enabled=False, logdir=str(tmp_path)),
                                     type(e), e, e.__traceback__)
    assert path is None
    assert list(tmp_path.glob("crash-*.log")) == []


def test_write_report_writes_redacted_file_when_enabled(tmp_path):
    from datetime import datetime
    try:
        raise RuntimeError("fail sk-abcdefghijkl")
    except RuntimeError as e:
        path = crashlog.write_report(_cfg(enabled=True, logdir=str(tmp_path)),
                                     type(e), e, e.__traceback__,
                                     now=datetime(2026, 7, 19, 1, 2, 3))
    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "RuntimeError" in body and "sk-abcdefghijkl" not in body
    assert path.name == "crash-20260719-010203.log"


def test_install_hook_captures_and_chains(tmp_path, monkeypatch):
    # install() always installs; the hook writes only when enabled, and chains to the previous hook.
    chained = {"called": False}
    monkeypatch.setattr(sys, "excepthook", lambda *a: chained.__setitem__("called", True))
    cfg = _cfg(enabled=True, logdir=str(tmp_path))
    assert crashlog.install(cfg) is True
    try:
        raise KeyError("secret sk-zzzzzzzzzzzz")
    except KeyError as e:
        sys.excepthook(type(e), e, e.__traceback__)
    files = list(tmp_path.glob("crash-*.log"))
    assert len(files) == 1 and "sk-zzzzzzzzzzzz" not in files[0].read_text(encoding="utf-8")
    assert chained["called"] is True                 # previous hook still ran


def test_install_hook_is_live_no_write_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # snapshot -> restored at teardown
    cfg = _cfg(enabled=False, logdir=str(tmp_path))
    crashlog.install(cfg)
    try:
        raise ValueError("v")
    except ValueError as e:
        sys.excepthook(type(e), e, e.__traceback__)
    assert list(tmp_path.glob("crash-*.log")) == []  # disabled -> nothing written
    # Flip it live (same cfg dict the hook closed over) -> now it writes.
    cfg["crash_report"]["enabled"] = True
    try:
        raise ValueError("v2")
    except ValueError as e:
        sys.excepthook(type(e), e, e.__traceback__)
    assert len(list(tmp_path.glob("crash-*.log"))) == 1
