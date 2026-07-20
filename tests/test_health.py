"""Unit tests for the structured setup/health check (issue #181) — offline, free.

The provider-reachability probes are injected, so the whole report/verdict machinery and the
human-readable error mapping run without any network, key, or audio hardware (DESIGN §9).
"""
from __future__ import annotations

import covas.health as health
from covas.health import (HealthReport, run_health, friendly_provider_error,
                          check_anthropic, check_audio, check_keys_and_files,
                          check_updates, check_system, OK, WARN, FAIL)
from covas.providers._retry import ProviderError


# --- report aggregation ----------------------------------------------------

def test_report_ok_and_problems_aggregate_by_status():
    r = HealthReport()
    s = r.section("X")
    s.add(OK, "fine"); s.add(WARN, "meh"); s.add(FAIL, "broken")
    assert r.problems == ["broken"]
    assert r.warnings == ["meh"]
    assert r.ok is False
    # A report with no FAILs is OK even with warnings.
    r2 = HealthReport(); r2.section("Y").add(WARN, "optional missing")
    assert r2.ok is True


def test_to_dict_is_json_shaped():
    r = HealthReport(); r.section("Audio").add(FAIL, "No microphone found", "plug one in")
    d = r.to_dict()
    assert d["ok"] is False and d["problems"] == ["No microphone found"]
    assert d["sections"][0]["checks"][0] == {
        "status": "fail", "label": "No microphone found", "detail": "plug one in"}


# --- human-readable error mapping (no tracebacks) --------------------------

def test_friendly_error_classifies_auth():
    msg = friendly_provider_error("Anthropic", ProviderError("bad key", provider="Anthropic", status=401))
    assert "key looks wrong" in msg.lower() and "Settings" in msg
    assert "Traceback" not in msg


def test_friendly_error_classifies_network_and_rate_limit():
    net = friendly_provider_error("ElevenLabs", TimeoutError("connection timed out"))
    assert "network" in net.lower() or "connection" in net.lower()
    rl = friendly_provider_error("Anthropic", ProviderError("slow down", provider="Anthropic", status=429))
    assert "rate" in rl.lower()


def test_friendly_error_generic_is_one_line_no_stack():
    e = ValueError("something odd\n  File \"x.py\"\n    raise")
    out = friendly_provider_error("Gemini", e)
    assert "\n" not in out and "something odd" in out


# --- provider checks with injected probes ----------------------------------

def test_anthropic_check_ok_with_probe():
    r = HealthReport()
    check_anthropic(r, "sk-key", probe=lambda k: 12)
    c = r.sections[-1].checks[0]
    assert c.status == OK and "12 models" in c.label


def test_anthropic_check_fail_is_friendly():
    r = HealthReport()
    def boom(_k): raise ProviderError("nope", provider="Anthropic", status=401)
    check_anthropic(r, "sk-key", probe=boom)
    c = r.sections[-1].checks[0]
    assert c.status == FAIL and "key looks wrong" in c.detail.lower()


def test_anthropic_check_skipped_without_key():
    r = HealthReport()
    check_anthropic(r, None, probe=lambda k: 1)
    assert r.sections[-1].checks[0].status == WARN


def test_audio_check_flags_missing_microphone():
    r = HealthReport()
    check_audio(r, probe=lambda: {"inputs": 0, "outputs": 2, "default_out": "Speakers"})
    statuses = {c.label: c.status for c in r.sections[-1].checks}
    assert any(s == FAIL for lbl, s in statuses.items() if "microphone" in lbl.lower())


def test_audio_check_ok_with_devices():
    r = HealthReport()
    check_audio(r, probe=lambda: {"inputs": 1, "outputs": 1,
                                  "default_in": "Mic", "default_out": "Speakers"})
    assert all(c.status == OK for c in r.sections[-1].checks)


# --- keys & files ----------------------------------------------------------

def test_missing_anthropic_key_fails_and_missing_elevenlabs_only_warns(monkeypatch):
    monkeypatch.setattr("covas.firstrun.anthropic_key", lambda cfg: None)
    monkeypatch.setattr("covas.firstrun.elevenlabs_key", lambda cfg: None)
    r = HealthReport()
    check_keys_and_files(r, {"personality": {"file": "nonexistent.txt"}})
    checks = r.sections[-1].checks
    anth = next(c for c in checks if "Anthropic" in c.label)
    el = next(c for c in checks if "ElevenLabs" in c.label)
    assert anth.status == FAIL          # required
    assert el.status == WARN            # optional


def test_present_keys_pass(monkeypatch):
    monkeypatch.setattr("covas.firstrun.anthropic_key", lambda cfg: "sk-abc")
    monkeypatch.setattr("covas.firstrun.elevenlabs_key", lambda cfg: "el-abc")
    r = HealthReport()
    a, e = check_keys_and_files(r, {"personality": {"file": "nonexistent.txt"}})
    assert a == "sk-abc" and e == "el-abc"
    assert next(c for c in r.sections[-1].checks if "Anthropic" in c.label).status == OK


# --- update notifier + system requirements (issue #186) --------------------

def test_update_check_warns_when_newer_available():
    r = HealthReport()
    check_updates(r, current="0.18.0",
                  probe=lambda cur: {"available": True, "latest": "0.19.0", "current": cur})
    c = r.sections[-1].checks[0]
    assert c.status == WARN and "0.19.0" in c.label


def test_update_check_ok_when_current():
    r = HealthReport()
    check_updates(r, current="0.18.0", probe=lambda cur: {"available": False, "latest": None})
    assert r.sections[-1].checks[0].status == OK


def test_update_check_is_fail_soft():
    r = HealthReport()
    def boom(_c): raise ConnectionError("offline")
    check_updates(r, current="0.18.0", probe=boom)
    assert r.sections[-1].checks[0].status == WARN   # never FAIL on a network hiccup


def test_system_check_warns_on_low_ram():
    r = HealthReport()
    check_system(r, {"whisper": {"model": "large-v3"}}, probe=lambda: 6.0)
    labels = " ".join(c.label.lower() for c in r.sections[-1].checks)
    statuses = [c.status for c in r.sections[-1].checks]
    assert WARN in statuses and "8 gb" in labels        # flags the <8GB minimum


def test_system_check_ok_on_ample_ram():
    r = HealthReport()
    check_system(r, {"whisper": {"model": "small.en"}}, probe=lambda: 32.0)
    assert all(c.status == OK for c in r.sections[-1].checks)


def test_system_check_handles_unknown_ram():
    r = HealthReport()
    check_system(r, {"whisper": {"model": "small.en"}}, probe=lambda: None)
    assert r.sections[-1].checks[0].status == OK        # degrades gracefully, no crash


# --- offline orchestration (network probes skipped) ------------------------

def test_run_health_offline_produces_a_report(monkeypatch):
    monkeypatch.setattr("covas.firstrun.anthropic_key", lambda cfg: "sk-abc")
    monkeypatch.setattr("covas.firstrun.elevenlabs_key", lambda cfg: None)
    # Deterministic audio (no real device query in the offline run).
    monkeypatch.setattr(health, "_probe_audio",
                        lambda: {"inputs": 1, "outputs": 1, "default_in": "M", "default_out": "S"})
    report = run_health({"personality": {"file": "x"}, "llm": {"provider": "anthropic"}}, network=False)
    titles = [s.title for s in report.sections]
    assert "Python packages" in titles and "Keys & files" in titles and "Audio devices" in titles
    # No network sections when network=False.
    assert "Anthropic" not in titles
    assert isinstance(report.to_dict(), dict)
