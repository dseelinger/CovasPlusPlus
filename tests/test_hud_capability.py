"""Unit tests for the companion HUD (issue #47) — offline, free, HEADLESS (DESIGN §9).

The HUD is a VIEW over injected state, so these exercise the pure data adapter (`HudModel`
-> `HudSnapshot`) and the capability's visibility reconciliation with a FAKE view. Nothing
here imports tkinter or opens a window: the real `HudView` / `make_view` are never touched, so
the default `pytest` run stays hermetic and headless. The four display groups the issue names
— voice-loop state, current checklist step, route progress, last proactive callout — are each
driven off crafted bus events + injected fakes and asserted on the snapshot.
"""
from __future__ import annotations

from covas.capabilities.hud_capability import (
    HudCapability, HudModel, HudSnapshot, checklist_line, make_view,
)


# --- fakes -----------------------------------------------------------------

class FakeChecklist:
    """Minimal `Checklist`-like stub: `next_pending` returns (pending, done, total)."""

    def __init__(self, pending, done, total):
        self._pending, self._done, self._total = pending, done, total

    def next_pending(self, count=1):
        return self._pending[:count], self._done, self._total


class FakeView:
    """Stands in for `HudView` — records show/hide/close without any tkinter."""

    def __init__(self):
        self.visible = None      # None = never told; True/False = last state
        self.closed = False
        self.shows = 0
        self.hides = 0

    def show(self):
        self.visible = True
        self.shows += 1

    def hide(self):
        self.visible = False
        self.hides += 1

    def close(self):
        self.closed = True


def _model(**kw) -> HudModel:
    return HudModel(**kw)


# --- checklist_line (pure helper) ------------------------------------------

def test_checklist_line_formats_first_pending_with_progress():
    chk = FakeChecklist(pending=[(3, "Scan the nav beacon")], done=2, total=10)
    assert checklist_line(chk) == "Scan the nav beacon  (2/10 done)"


def test_checklist_line_none_when_empty_list():
    assert checklist_line(FakeChecklist(pending=[], done=0, total=0)) is None


def test_checklist_line_all_done_when_no_pending_but_items_exist():
    assert checklist_line(FakeChecklist(pending=[], done=4, total=4)) == "All 4 items done"


def test_checklist_line_survives_a_raising_checklist():
    class Boom:
        def next_pending(self, count=1):
            raise RuntimeError("disk gone")
    assert checklist_line(Boom()) is None


# --- voice-loop state (status events) --------------------------------------

def test_default_snapshot_is_idle_and_blank():
    snap = _model().snapshot()
    assert snap == HudSnapshot(voice_state="Idle", checklist=None, route=None, callout=None)


def test_status_event_updates_voice_state():
    m = _model()
    m.on_event({"type": "status", "state": "Listening"})
    assert m.snapshot().voice_state == "Listening"


def test_working_states_fold_to_thinking():
    m = _model()
    for raw in ("Transcribing", "Searching", "Thinking"):
        m.on_event({"type": "status", "state": raw})
        assert m.snapshot().voice_state == "Thinking"


def test_speaking_state_is_shown():
    m = _model()
    m.on_event({"type": "status", "state": "Speaking"})
    assert m.snapshot().voice_state == "Speaking"


def test_unknown_state_is_shown_verbatim():
    m = _model()
    m.on_event({"type": "status", "state": "Reticulating"})
    assert m.snapshot().voice_state == "Reticulating"


# --- checklist step (injected provider) ------------------------------------

def test_snapshot_reads_injected_checklist_line():
    chk = FakeChecklist(pending=[(1, "Dock at Jameson")], done=0, total=3)
    m = _model(checklist_provider=lambda: checklist_line(chk))
    assert m.snapshot().checklist == "Dock at Jameson  (0/3 done)"


# --- route progress (ed_events + injected navroute) ------------------------

_NAVROUTE = {"Route": [
    {"StarSystem": "Sol", "StarClass": "G"},
    {"StarSystem": "Alpha Centauri", "StarClass": "K"},
    {"StarSystem": "Sirius", "StarClass": "A"},
    {"StarSystem": "Colonia", "StarClass": "M"},
]}


def test_route_line_none_without_a_plotted_route():
    assert _model().snapshot().route is None


def test_navroute_event_loads_route_and_reports_jumps_remaining():
    m = _model(load_navroute=lambda: _NAVROUTE)
    m.on_event({"type": "ed_event", "event": "NavRoute"})
    # Origin + 3 systems = 3 jumps to make, destination is the last.
    assert m.snapshot().route == "3 jumps to Colonia"


def test_route_advances_on_jump_and_singularizes_last_jump():
    m = _model(load_navroute=lambda: _NAVROUTE)
    m.on_event({"type": "ed_event", "event": "NavRoute"})
    m.on_event({"type": "ed_event", "event": "FSDJump", "StarSystem": "Alpha Centauri"})
    m.on_event({"type": "ed_event", "event": "FSDJump", "StarSystem": "Sirius"})
    assert m.snapshot().route == "1 jump to Colonia"


def test_route_reports_arrival_at_destination():
    m = _model(load_navroute=lambda: _NAVROUTE)
    m.on_event({"type": "ed_event", "event": "NavRoute"})
    for sysname in ("Alpha Centauri", "Sirius", "Colonia"):
        m.on_event({"type": "ed_event", "event": "FSDJump", "StarSystem": sysname})
    assert m.snapshot().route == "Arrived at Colonia"


def test_fsdtarget_annotates_scoopable_next_star():
    m = _model(load_navroute=lambda: _NAVROUTE)
    m.on_event({"type": "ed_event", "event": "NavRoute"})
    m.on_event({"type": "ed_event", "event": "FSDTarget", "Name": "Alpha Centauri"})  # class K
    assert m.snapshot().route == "3 jumps to Colonia  ·  next scoopable"


def test_fsdtarget_warns_on_non_scoopable_next_star():
    navroute = {"Route": [
        {"StarSystem": "Sol", "StarClass": "G"},
        {"StarSystem": "Wolf 359", "StarClass": "M"},
        {"StarSystem": "Deadend", "StarClass": "Y"},  # brown dwarf — not scoopable
    ]}
    m = _model(load_navroute=lambda: navroute)
    m.on_event({"type": "ed_event", "event": "NavRoute"})
    m.on_event({"type": "ed_event", "event": "FSDTarget", "Name": "Deadend"})
    assert m.snapshot().route == "2 jumps to Deadend  ·  next NOT scoopable"


def test_navroute_clear_drops_the_route():
    m = _model(load_navroute=lambda: _NAVROUTE)
    m.on_event({"type": "ed_event", "event": "NavRoute"})
    m.on_event({"type": "ed_event", "event": "NavRouteClear"})
    assert m.snapshot().route is None


def test_replot_resets_scoopable_annotation():
    m = _model(load_navroute=lambda: _NAVROUTE)
    m.on_event({"type": "ed_event", "event": "NavRoute"})
    m.on_event({"type": "ed_event", "event": "FSDTarget", "Name": "Alpha Centauri"})
    assert "scoopable" in (m.snapshot().route or "")
    m.on_event({"type": "ed_event", "event": "NavRoute"})  # replot
    assert m.snapshot().route == "3 jumps to Colonia"  # annotation cleared


# --- last proactive callout (log events) -----------------------------------

def test_proactive_callout_line_is_captured_without_prefix():
    m = _model()
    m.on_event({"type": "log", "who": "COVAS", "text": "(proactive) Arrived in Sol, Commander."})
    assert m.snapshot().callout == "Arrived in Sol, Commander."


def test_route_callout_line_is_captured():
    m = _model()
    m.on_event({"type": "log", "who": "COVAS", "text": "(route) 2 jumps remaining to Colonia."})
    assert m.snapshot().callout == "2 jumps remaining to Colonia."


def test_normal_reply_is_not_treated_as_a_callout():
    m = _model()
    m.on_event({"type": "log", "who": "COVAS", "text": "The nearest station is Jameson Memorial."})
    assert m.snapshot().callout is None


def test_non_covas_log_is_ignored():
    m = _model()
    m.on_event({"type": "log", "who": "system", "text": "(proactive) not really COVAS"})
    assert m.snapshot().callout is None


# --- robustness ------------------------------------------------------------

def test_model_ignores_junk_events():
    m = _model()
    for junk in (None, "nope", 42, {}, {"type": "usage"}, {"type": "ed_event"}):
        m.on_event(junk)  # must not raise
    assert m.snapshot().voice_state == "Idle"


# --- capability visibility reconciliation (with a fake view) ---------------

def test_capability_advertises_no_tools():
    cap = HudCapability(_model(), is_enabled=lambda: False,
                        view_factory=lambda p: FakeView())
    assert cap.tools() == []


def test_capability_help_meta_is_complete():
    from covas.capabilities.base import help_meta_problems
    cap = HudCapability(_model(), is_enabled=lambda: False,
                        view_factory=lambda p: FakeView())
    assert help_meta_problems(cap.help_meta()) == []


def test_disabled_at_startup_never_builds_a_window():
    built = []
    HudCapability(_model(), is_enabled=lambda: False,
                  view_factory=lambda p: built.append(1) or FakeView())
    assert built == []  # window is created lazily, only on first enable


def test_enabled_at_startup_creates_and_shows_the_window():
    view = FakeView()
    HudCapability(_model(), is_enabled=lambda: True, view_factory=lambda p: view)
    assert view.visible is True and view.shows == 1


def test_reconcile_toggles_the_window_on_and_off():
    view = FakeView()
    enabled = {"v": False}
    cap = HudCapability(_model(), is_enabled=lambda: enabled["v"],
                        view_factory=lambda p: view)
    assert view.visible is None  # not created while disabled

    enabled["v"] = True
    cap.reconcile()   # the app calls this directly after a settings change
    assert view.visible is True

    enabled["v"] = False
    cap.reconcile()
    assert view.visible is False


def test_headless_factory_returning_none_is_fail_soft():
    logs = []
    HudCapability(_model(), is_enabled=lambda: True,
                  view_factory=lambda p: None, log=logs.append)
    # No window, no crash; and it doesn't retry the failed factory on the next reconcile.
    calls = {"n": 0}

    def counting_factory(p):
        calls["n"] += 1
        return None
    cap2 = HudCapability(_model(), is_enabled=lambda: True,
                         view_factory=counting_factory)
    cap2.reconcile()
    assert calls["n"] == 1  # built once (at construction), not retried
    assert any("no 2D overlay" in m or "HUD enabled" in m for m in logs)


def test_capability_feeds_the_model_from_on_event():
    m = _model()
    cap = HudCapability(m, is_enabled=lambda: False, view_factory=lambda p: FakeView())
    cap.on_event({"type": "status", "state": "Speaking"})
    assert m.snapshot().voice_state == "Speaking"


def test_shutdown_closes_the_window():
    view = FakeView()
    cap = HudCapability(_model(), is_enabled=lambda: True, view_factory=lambda p: view)
    cap.shutdown()
    assert view.closed is True


def test_on_event_never_raises_on_bad_input():
    cap = HudCapability(_model(), is_enabled=lambda: False,
                        view_factory=lambda p: FakeView())
    for junk in (None, "x", 7, {"type": "settings"}):
        cap.on_event(junk)  # must not raise


# --- web HUD surface (issue #103) — third sink over the same reconcile contract ----

def _cap_with_web(web_enabled, web_factory):
    """A capability with the 2D surface off and only the web surface wired, so these tests
    exercise the third `_reconcile_surface` call in isolation."""
    return HudCapability(_model(), is_enabled=lambda: False,
                         view_factory=lambda p: None,
                         web_is_enabled=web_enabled, web_view_factory=web_factory)


def test_web_surface_created_and_shown_on_first_enable():
    view = FakeView()
    _cap_with_web(lambda: True, lambda p: view)
    assert view.visible is True and view.shows == 1


def test_web_surface_disabled_at_startup_is_not_built():
    built = []
    _cap_with_web(lambda: False, lambda p: built.append(1) or FakeView())
    assert built == []  # lazy — created only on first enable


def test_web_surface_reconcile_toggles_show_hide():
    view = FakeView()
    enabled = {"v": False}
    cap = _cap_with_web(lambda: enabled["v"], lambda p: view)
    assert view.visible is None
    enabled["v"] = True
    cap.reconcile()
    assert view.visible is True
    enabled["v"] = False
    cap.reconcile()
    assert view.visible is False


def test_web_surface_shutdown_closes_the_view():
    view = FakeView()
    cap = _cap_with_web(lambda: True, lambda p: view)
    cap.shutdown()
    assert view.closed is True


def test_web_factory_returning_none_is_fail_soft_and_logs_control_panel():
    logs = []
    HudCapability(_model(), is_enabled=lambda: False, view_factory=lambda p: None,
                  web_is_enabled=lambda: True, web_view_factory=lambda p: None,
                  log=logs.append)
    assert any("run_covas_ui.py" in m for m in logs)  # headless => told to start the control panel


def test_on_web_ui_ready_retries_a_web_surface_enabled_before_flask():
    """A web HUD enabled before the control panel exists first sees a None factory; once Flask
    signals ready, on_web_ui_ready clears the one-shot latch and the surface attaches (#103)."""
    view = FakeView()
    ready = {"v": False}

    def factory(p):
        return view if ready["v"] else None

    cap = HudCapability(_model(), is_enabled=lambda: False, view_factory=lambda p: None,
                        web_is_enabled=lambda: True, web_view_factory=factory)
    assert view.visible is None          # factory returned None at startup (no control panel)
    ready["v"] = True
    cap.on_web_ui_ready()
    assert view.visible is True          # attached on the retry


class _WebView:
    """A WebHudView-like fake: records URL announcements on show, resets on hide/close."""

    def __init__(self):
        self.shows = 0
        self.hides = 0
        self.closed = False

    def show(self):
        self.shows += 1

    def hide(self):
        self.hides += 1

    def close(self):
        self.closed = True


def test_webhudview_announces_url_once_per_enable():
    from covas.capabilities.hud_capability import WebHudView
    logs = []
    v = WebHudView("http://127.0.0.1:8765/hud", log=logs.append)
    v.show()
    v.show()  # a second reconcile while still enabled must not re-log
    assert sum("openkneeboard" in m.lower() for m in logs) == 1
    assert any("/hud" in m for m in logs)
    v.hide()
    v.show()  # re-enabled => announce again
    assert sum("openkneeboard" in m.lower() for m in logs) == 2
