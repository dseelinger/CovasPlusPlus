"""Unit tests for proactive callouts (DESIGN §5, Prompt 5). Offline + free.

Covers the pure gating (whitelist / per-event + global cooldown / mute), the capability's
on_event dispatch and mute tools, the prompt helpers, and the App wiring that speaks a
callout only when idle — never over an in-progress user turn.
"""
from __future__ import annotations

from covas.app import App
from covas.capabilities.proactive_capability import (
    DEFAULT_EVENTS,
    ProactiveCapability,
    ProactiveConfig,
    ProactivePolicy,
    build_prompt,
    event_phrase,
)
from tests.fakes import FakeLLM, FakeSTT, FakeTTS

# --- ProactiveConfig.from_cfg ---------------------------------------------------------

def test_config_defaults_when_section_missing():
    c = ProactiveConfig.from_cfg({})
    assert c.enabled is False
    assert c.events == DEFAULT_EVENTS
    assert c.cooldown > 0 and c.min_interval > 0 and c.max_tokens > 0


def test_config_events_table_replaces_defaults():
    """An explicit [proactive.events] table is the whole whitelist — so it can pare down,
    not only extend. Only FSDJump listed -> only FSDJump is eligible."""
    c = ProactiveConfig.from_cfg({"proactive": {
        "enabled": True, "cooldown": 30, "min_interval": 5,
        "events": {"FSDJump": True, "Docked": False},
    }})
    assert c.enabled is True and c.cooldown == 30 and c.min_interval == 5
    assert c.allows("FSDJump") is True
    assert c.allows("Docked") is False        # explicitly off
    assert c.allows("MissionCompleted") is False  # not in the table at all


def test_config_bad_numeric_field_falls_back_instead_of_aborting():
    # A null/non-numeric override for ONE field must fall back to its default, not raise and
    # abort the whole build (which would silently disable every proactive callout).
    d = ProactiveConfig()
    c = ProactiveConfig.from_cfg({"proactive": {
        "enabled": True,
        "cooldown": None,            # explicit null (e.g. cleared in overrides.json)
        "min_interval": "soon",      # non-numeric string
        "max_tokens": None,
        "long_jump_ly": "far",
    }})
    assert c.enabled is True                     # the rest of the build still happened
    assert c.cooldown == d.cooldown
    assert c.min_interval == d.min_interval
    assert c.max_tokens == d.max_tokens
    assert c.long_jump_ly == d.long_jump_ly
    assert c.events == DEFAULT_EVENTS


# --- ProactivePolicy: the gate --------------------------------------------------------

def _policy(**over) -> ProactivePolicy:
    cfg = {"enabled": True, "cooldown": 100, "min_interval": 10,
           "events": {"FSDJump": True, "LowFuel": True}}
    cfg.update(over)
    return ProactivePolicy(ProactiveConfig.from_cfg({"proactive": cfg}))


def test_disabled_never_speaks():
    p = _policy(enabled=False)
    ok, reason = p.should_speak("FSDJump", now=1000.0)
    assert ok is False and "disabled" in reason


def test_non_whitelisted_event_blocked():
    p = _policy()
    ok, reason = p.should_speak("Undocked", now=1000.0)
    assert ok is False and "whitelisted" in reason


def test_qualifying_event_allowed():
    p = _policy()
    ok, reason = p.should_speak("FSDJump", now=1000.0)
    assert ok is True and "FSDJump" in reason


def test_per_event_cooldown_blocks_then_clears():
    p = _policy(cooldown=100, min_interval=0)  # isolate the per-event cooldown
    assert p.should_speak("FSDJump", now=1000.0)[0] is True
    p.mark_fired("FSDJump", now=1000.0)
    # same event again within the cooldown -> blocked
    ok, reason = p.should_speak("FSDJump", now=1050.0)
    assert ok is False and "cooldown" in reason
    # a *different* whitelisted event is unaffected by FSDJump's per-event cooldown
    assert p.should_speak("LowFuel", now=1050.0)[0] is True
    # past the cooldown, FSDJump is allowed again
    assert p.should_speak("FSDJump", now=1101.0)[0] is True


def test_global_min_interval_blocks_other_events():
    p = _policy(cooldown=0, min_interval=10)  # isolate the global interval
    assert p.should_speak("FSDJump", now=1000.0)[0] is True
    p.mark_fired("FSDJump", now=1000.0)
    # a different event within the global interval -> blocked by the global cooldown
    ok, reason = p.should_speak("LowFuel", now=1005.0)
    assert ok is False and "global" in reason
    # once the interval elapses, it's allowed
    assert p.should_speak("LowFuel", now=1011.0)[0] is True


def test_mute_blocks_and_unmute_restores():
    p = _policy()
    assert p.should_speak("FSDJump", now=1000.0)[0] is True
    p.set_muted(True)
    ok, reason = p.should_speak("FSDJump", now=1000.0)
    assert ok is False and "muted" in reason
    assert p.toggle_mute() is False        # back on
    assert p.should_speak("FSDJump", now=1000.0)[0] is True


# --- ProactiveCapability: on_event dispatch + tools -----------------------------------

class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Speaker:
    """Records calls; `started` controls the bool it returns (app idle vs busy)."""

    def __init__(self, started: bool = True) -> None:
        self.calls: list[str] = []
        self.started = started

    def __call__(self, name: str, event: dict) -> bool:
        self.calls.append(name)
        return self.started


def _capability(speaker, clock, **cfg_over) -> ProactiveCapability:
    cfg = {"enabled": True, "cooldown": 100, "min_interval": 10,
           "events": {"FSDJump": True}}
    cfg.update(cfg_over)
    policy = ProactivePolicy(ProactiveConfig.from_cfg({"proactive": cfg}))
    return ProactiveCapability(policy, speaker, clock=clock)


def test_on_event_ignores_non_ed_and_nameless():
    spk = _Speaker()
    cap = _capability(spk, _Clock())
    cap.on_event({"type": "log", "text": "hi"})           # not an ed_event
    cap.on_event({"type": "ed_event"})                     # no event name
    assert spk.calls == []


def test_on_event_speaks_and_arms_cooldown():
    spk, clock = _Speaker(started=True), _Clock()
    cap = _capability(spk, clock)
    cap.on_event({"type": "ed_event", "event": "FSDJump", "StarSystem": "Sol"})
    assert spk.calls == ["FSDJump"]
    # a second identical event immediately after is blocked by the cooldown it just armed
    cap.on_event({"type": "ed_event", "event": "FSDJump"})
    assert spk.calls == ["FSDJump"]


def test_on_event_busy_app_does_not_arm_cooldown():
    """When the app is busy (speak returns False), the cooldown is NOT armed, so the
    callout can fire once the Commander is done rather than being swallowed."""
    spk, clock = _Speaker(started=False), _Clock()
    cap = _capability(spk, clock)
    cap.on_event({"type": "ed_event", "event": "FSDJump"})
    assert spk.calls == ["FSDJump"]         # attempted...
    clock.t += 1                             # ...still within any cooldown window
    cap.on_event({"type": "ed_event", "event": "FSDJump"})
    assert spk.calls == ["FSDJump", "FSDJump"]  # retried because nothing was armed


def test_mute_unmute_tools():
    cap = _capability(_Speaker(), _Clock())
    assert "muted" in cap.run_tool("mute_proactive", {}).lower()
    assert cap.policy.muted is True
    assert "unmuted" in cap.run_tool("unmute_proactive", {}).lower()
    assert cap.policy.muted is False
    # muting via the tool actually stops on_event from speaking
    cap.run_tool("mute_proactive", {})
    spk = _Speaker()
    cap._speak = spk  # swap in a fresh recorder
    cap.on_event({"type": "ed_event", "event": "FSDJump"})
    assert spk.calls == []


# --- prompt helpers -------------------------------------------------------------------

def test_event_phrase_reuses_describers_and_humanizes():
    assert event_phrase({"event": "FSDJump", "StarSystem": "Sol"}) == "Jumped to Sol"
    assert event_phrase({"event": "LowFuel"}) == "Fuel dropped below 25%"
    # no describer -> readable fallback from the raw name
    assert event_phrase({"event": "SomethingOdd"}) == "Something odd"


def test_on_foot_srv_events_are_whitelisted_by_default():
    """The #54 callouts ship on by default (still gated by the master switch + cooldowns)."""
    for name in ("ScanOrganic", "OxygenLow", "HealthLow", "SrvHullLow"):
        assert DEFAULT_EVENTS.get(name) is True


def test_event_phrase_for_on_foot_srv_events():
    # ScanOrganic -> journal describer (count from ScanType); the status/SRV alerts -> transition
    # phrases; all readable spoken lines for the callout prompt.
    assert event_phrase({"event": "ScanOrganic", "ScanType": "Sample",
                         "Genus_Localised": "Bacterium"}) == \
        "Sample 2 of 3 of Bacterium logged — one more to analyse"
    assert event_phrase({"event": "OxygenLow"}) == "Oxygen running low"
    assert event_phrase({"event": "SrvHullLow"}) == "SRV hull getting low"


def test_on_foot_callout_respects_cooldown():
    """A whitelisted #54 event fires once, then is blocked by the cooldown it armed — the
    same discipline as every other callout (never bypasses the policy)."""
    p = ProactivePolicy(ProactiveConfig.from_cfg({"proactive": {
        "enabled": True, "cooldown": 100, "min_interval": 0,
        "events": {"OxygenLow": True}}}))
    assert p.should_speak("OxygenLow", now=1000.0)[0] is True
    p.mark_fired("OxygenLow", now=1000.0)
    ok, reason = p.should_speak("OxygenLow", now=1050.0)
    assert ok is False and "cooldown" in reason
    assert p.should_speak("OxygenLow", now=1101.0)[0] is True   # clears after the cooldown


def test_build_prompt_contains_event_and_context():
    prompt = build_prompt({"event": "FSDJump", "StarSystem": "Sol"},
                          "the Commander is in Sol; fuel 40%.")
    assert "Jumped to Sol" in prompt
    assert "fuel 40%" in prompt
    assert "UNPROMPTED" in prompt          # the model is told it wasn't asked


# --- #138 place/history enrichment ----------------------------------------------------

def test_build_prompt_carries_grounded_place_facts():
    """When facts are supplied, the prompt states them AND tells the model not to invent —
    grounding discipline (the model phrases, never fabricates)."""
    facts = {"place": "engineer base", "label": "Farseer Inc, Felicity Farseer's workshop",
             "detail": "engineers Frame Shift Drive, Thrusters", "visits_24h": 10}
    prompt = build_prompt({"event": "Docked", "StationName": "Farseer Inc"}, None, facts=facts)
    assert "Farseer Inc" in prompt
    assert "Felicity Farseer" in prompt
    assert "10 times in the last 24 hours" in prompt
    assert "do NOT invent" in prompt or "not invent" in prompt.lower()


def test_build_prompt_without_facts_is_unchanged():
    """No facts -> exactly today's generic callout (no grounding clause)."""
    prompt = build_prompt({"event": "Docked", "StationName": "Somewhere"}, None)
    assert "Grounded facts" not in prompt


def test_place_cooldown_gates_history_remarks():
    """The dedicated place cooldown is a separate axis from the per-event cooldown: a place
    remark is allowed, armed, then blocked until its own (longer) cooldown elapses."""
    p = ProactivePolicy(ProactiveConfig.from_cfg({"proactive": {
        "enabled": True, "place_cooldown": 900}}))
    assert p.should_place_remark(now=1000.0) is True
    p.mark_place_remark(now=1000.0)
    assert p.should_place_remark(now=1500.0) is False   # within the 900s place cooldown
    assert p.should_place_remark(now=1901.0) is True    # clears after it


def test_place_cooldown_default_from_config():
    c = ProactiveConfig.from_cfg({"proactive": {"enabled": True}})
    assert c.place_cooldown > 0


# --- App wiring -----------------------------------------------------------------------

def _cfg(tmp_path, **extra) -> dict:
    checklist = tmp_path / "checklist.md"
    checklist.write_text("- [ ] Scoop fuel\n", encoding="utf-8")
    cfg = {
        "anthropic": {"model": "claude-haiku-4-5", "max_tokens": 1024,
                      "thinking": {"default": "Off"}, "cache_ttl": "1h"},
        "web_search": {"enabled": False},
        "personality": {"enabled": False},
        "checklist": {"file": str(checklist)},
        "conversation": {"max_turns": 20},
        "logging": {"dir": str(tmp_path / "logs")},
        "audio": {"sample_rate": 16000, "input_device": ""},
        "sound_cues": {},
        "keys": {"push_to_talk": "right ctrl"},
    }
    cfg.update(extra)
    return cfg


def _proactive_cfg(tmp_path) -> dict:
    return _cfg(
        tmp_path,
        elite={"enabled": True, "journal_dir": str(tmp_path),
               "journal_poll_interval": 0.05, "status_poll_interval": 0.05},
        proactive={"enabled": True, "cooldown": 100, "min_interval": 10,
                   "max_tokens": 60, "events": {"FSDJump": True}},
    )


def test_proactive_off_by_default(tmp_path):
    """No [proactive] section -> no capability, no event pump, no mute tools."""
    app = App(_cfg(tmp_path), stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    assert app.proactive is None and app._pump is None
    assert "mute_proactive" not in {t["name"] for t in app.registry.tools()}


def test_proactive_wires_up_when_enabled(tmp_path):
    app = App(_proactive_cfg(tmp_path), stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS())
    try:
        assert app.proactive is not None
        assert app._pump is not None and app._pump.is_alive()
        assert {"mute_proactive", "unmute_proactive"} <= \
            {t["name"] for t in app.registry.tools()}
    finally:
        app._stop_event_pump()
        app._stop_ed_monitoring()


def test_speak_proactive_when_idle_speaks_cheap_and_stays_out_of_history(tmp_path):
    llm = FakeLLM(text="Arrived in Sol, Commander.")
    app = App(_proactive_cfg(tmp_path), stt=FakeSTT(), llm=llm, tts=FakeTTS())
    try:
        started = app._speak_proactive("FSDJump", {"event": "FSDJump", "StarSystem": "Sol"})
        assert started is True
        app.worker.join(timeout=3)
        assert app.tts.spoken == ["Arrived in Sol, Commander."]
        assert app.history == []                 # ambient — never enters the conversation
        assert app.state == "Idle"
        assert llm.model_seen == "claude-haiku-4-5"   # cheap tier
        assert llm.max_tokens_seen == 60              # [proactive].max_tokens
    finally:
        app._stop_event_pump()
        app._stop_ed_monitoring()


def test_speak_proactive_skips_when_busy(tmp_path):
    """A callout must never start on top of an in-progress user turn."""
    app = App(_proactive_cfg(tmp_path), stt=FakeSTT(), llm=FakeLLM(text="hi"), tts=FakeTTS())
    try:
        app.state = "Thinking"                   # simulate a user turn underway
        started = app._speak_proactive("FSDJump", {"event": "FSDJump"})
        assert started is False
        assert app.tts.spoken == []
    finally:
        app._stop_event_pump()
        app._stop_ed_monitoring()


def test_event_pump_dispatches_bus_event_to_callout(tmp_path):
    """End to end through the bus: publishing a qualifying ed_event drives a spoken line
    via the pump -> capability -> _speak_proactive path."""
    llm = FakeLLM(text="Jumped clean, Commander.")
    app = App(_proactive_cfg(tmp_path), stt=FakeSTT(), llm=llm, tts=FakeTTS())
    try:
        app.bus.publish({"type": "ed_event", "event": "FSDJump", "StarSystem": "Sol"})
        _wait_for(lambda: app.tts.spoken, timeout=3)
        assert app.tts.spoken == ["Jumped clean, Commander."]
    finally:
        app._stop_event_pump()
        app._stop_ed_monitoring()


def _wait_for(cond, timeout: float) -> None:
    """Poll `cond` until truthy or `timeout` (seconds) elapses, without a fixed sleep."""
    import time as _t
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        if cond():
            return
        _t.sleep(0.02)
