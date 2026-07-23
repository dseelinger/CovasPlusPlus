"""Unit tests for the ED window focuser (#105).

Offline + hermetic: a fake backend stands in for Win32, so the default `pytest` run never
enumerates or foregrounds a real window. Covers:
  * find_ed_window() matches by PROCESS image name (picks EliteDangerous64.exe, ignores others,
    None when absent) with a title-substring fallback;
  * the HOT-PATH guarantee — is_foreground()/the already-foreground ensure_foreground() never
    call the enumerate hook;
  * ensure_foreground() restores a minimised window and runs the AttachThreadInput dance;
  * focus_game run_tool (success / not-running / off-Windows) and the auto-focus pre-step in the
    keybind _execute + the comms injector when on, skipped when off;
  * the combat-reflex path can't focus (no focuser seam).
"""
from __future__ import annotations

import inspect

from covas.capabilities.keybind_capability import KeybindCapability, KeybindConfig
from covas.comms.injector import ClipboardTextInjector
from covas.keybinds.binds import KeyBinding
from covas.keybinds.focus import ELITE_IMAGE, WindowFocuser

# --- fakes -----------------------------------------------------------------

class _FakeBackend:
    """In-memory stand-in for Win32Backend. `windows` maps hwnd -> dict(pid, image, title,
    iconic, thread). Records enum_windows() calls (enum_calls) and the raise-to-front sequence so
    tests can assert the hot path never enumerates and the dance ran in order."""

    def __init__(self, windows: dict[int, dict], foreground: int | None) -> None:
        self._windows = windows
        self._fg = foreground
        self.enum_calls = 0
        self.calls: list[str] = []            # ordered dance record

    def foreground_window(self):
        return self._fg

    def enum_windows(self):
        self.enum_calls += 1
        return list(self._windows)

    def window_pid(self, hwnd):
        return self._windows.get(hwnd, {}).get("pid")

    def image_name(self, pid):
        for w in self._windows.values():
            if w.get("pid") == pid:
                return w.get("image")
        return None

    def window_title(self, hwnd):
        return self._windows.get(hwnd, {}).get("title", "")

    def window_thread(self, hwnd):
        return self._windows.get(hwnd, {}).get("thread", 0)

    def current_thread(self):
        return 9999

    def is_iconic(self, hwnd):
        return bool(self._windows.get(hwnd, {}).get("iconic", False))

    def restore(self, hwnd):
        self.calls.append(f"restore:{hwnd}")
        self._windows[hwnd]["iconic"] = False

    def attach_thread_input(self, a, b, attach):
        self.calls.append(f"attach:{a}:{b}:{attach}")
        return True

    def bring_to_top(self, hwnd):
        self.calls.append(f"top:{hwnd}")

    def set_foreground(self, hwnd):
        self.calls.append(f"fg:{hwnd}")
        self._fg = hwnd                        # the dance succeeds -> ED is now frontmost
        return True


def _ed_win(hwnd=100, iconic=False, title="Elite - Dangerous (CLIENT)", thread=11):
    return {hwnd: {"pid": 4242, "image": "EliteDangerous64.exe", "title": title,
                   "iconic": iconic, "thread": thread}}


def _other_win(hwnd=200, image="chrome.exe", title="A Browser", thread=22):
    return {hwnd: {"pid": 777, "image": image, "title": title, "iconic": False,
                   "thread": thread}}


# --- find_ed_window: match by process ---------------------------------------

def test_find_picks_elite_process_ignoring_others():
    wins = {**_other_win(200), **_ed_win(100), **_other_win(300, image="notepad.exe")}
    f = WindowFocuser(backend=_FakeBackend(wins, foreground=200))
    assert f.find_ed_window() == 100


def test_find_returns_none_when_no_elite_window():
    wins = {**_other_win(200), **_other_win(300, image="notepad.exe")}
    f = WindowFocuser(backend=_FakeBackend(wins, foreground=200))
    assert f.find_ed_window() is None


def test_find_title_fallback_when_process_name_differs():
    # A window whose image name isn't the canonical exe but whose title matches — the fallback.
    wins = {200: {"pid": 5, "image": "weird_launcher.exe",
                  "title": "Elite - Dangerous (CLIENT)", "iconic": False, "thread": 3}}
    f = WindowFocuser(backend=_FakeBackend(wins, foreground=200))
    assert f.find_ed_window() == 200


def test_elite_image_constant_is_lowercased():
    # The matcher compares case-insensitively; the constant must be the lowercase form.
    assert ELITE_IMAGE == ELITE_IMAGE.lower()


# --- is_foreground + HOT PATH ----------------------------------------------

def test_is_foreground_true_when_elite_focused_without_enumerating():
    be = _FakeBackend(_ed_win(100), foreground=100)
    f = WindowFocuser(backend=be)
    assert f.is_foreground() is True
    assert be.enum_calls == 0                  # hot path never sweeps


def test_is_foreground_false_when_other_window_focused():
    be = _FakeBackend({**_ed_win(100), **_other_win(200)}, foreground=200)
    f = WindowFocuser(backend=be)
    assert f.is_foreground() is False
    assert be.enum_calls == 0


def test_ensure_foreground_noop_when_already_front_does_not_enumerate():
    be = _FakeBackend(_ed_win(100), foreground=100)
    f = WindowFocuser(backend=be)
    assert f.ensure_foreground() is True
    assert be.enum_calls == 0                  # the whole point: no sweep on the common path
    assert be.calls == []                      # and no foreground-lock dance


# --- ensure_foreground: the dance ------------------------------------------

def test_ensure_foreground_runs_attach_dance_and_restores_minimised():
    # ED minimised and NOT focused (a browser is) -> restore, attach, bring-to-top, set-foreground.
    wins = {**_other_win(200, thread=22), **_ed_win(100, iconic=True, thread=11)}
    be = _FakeBackend(wins, foreground=200)
    f = WindowFocuser(backend=be)
    assert f.ensure_foreground() is True
    assert be.enum_calls == 1                  # it had to find the window
    assert be.calls == [
        "restore:100",
        "attach:22:9999:True",                 # share the fg (browser) thread's input queue
        "top:100",
        "fg:100",
        "attach:22:9999:False",                # always detach
    ]


def test_ensure_foreground_false_when_elite_not_running():
    be = _FakeBackend(_other_win(200), foreground=200)
    f = WindowFocuser(backend=be)
    assert f.ensure_foreground() is False
    assert be.calls == []                      # nothing to raise


def test_ensure_foreground_never_raises_on_backend_error():
    class _Boom(_FakeBackend):
        def foreground_window(self):
            raise RuntimeError("win32 blew up")
    f = WindowFocuser(backend=_Boom(_ed_win(100), foreground=100))
    assert f.ensure_foreground() is False      # swallowed -> fail soft


# --- capability focus_game tool --------------------------------------------

_LG = {"LandingGearToggle": KeyBinding(action="LandingGearToggle", key="Key_L")}
_SAFE = {"in_danger": False, "being_interdicted": False}


class _RecordingFocuser:
    """Records ensure_foreground calls; configurable find/ensure results for the tool paths."""

    def __init__(self, *, found=True, ensured=True) -> None:
        self._found = found
        self._ensured = ensured
        self.ensured = 0

    def find_ed_window(self):
        return 100 if self._found else None

    def ensure_foreground(self):
        self.ensured += 1
        return self._ensured


class _FakeExecutor:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, binding) -> None:
        self.pressed.append(binding.key)

    def hold(self, binding, seconds) -> None:
        self.pressed.append(binding.key)

    def release_all(self) -> None:
        pass


def _cap(*, focuser, cfg=None):
    return KeybindCapability(
        binds=_LG, executor=_FakeExecutor(),
        config=cfg or KeybindConfig(enabled=True),
        status_snapshot=(lambda: _SAFE), focuser=focuser)


def test_focus_game_advertised_only_with_focuser():
    with_f = _cap(focuser=_RecordingFocuser())
    without = _cap(focuser=None)
    assert "focus_game" in {t["name"] for t in with_f.tools()}
    assert "focus_game" not in {t["name"] for t in without.tools()}


def test_focus_game_success():
    cap = _cap(focuser=_RecordingFocuser(found=True, ensured=True))
    out = cap.run_tool("focus_game", {})
    assert "front" in out.lower()


def test_focus_game_not_running():
    cap = _cap(focuser=_RecordingFocuser(found=False))
    out = cap.run_tool("focus_game", {})
    assert "can't find" in out.lower() and "running" in out.lower()


def test_focus_game_off_windows_absent():
    cap = _cap(focuser=None)
    out = cap.run_tool("focus_game", {})
    assert "can't focus" in out.lower()        # feature absent, no crash


def test_focus_game_found_but_cannot_foreground():
    cap = _cap(focuser=_RecordingFocuser(found=True, ensured=False))
    out = cap.run_tool("focus_game", {})
    assert "couldn't bring" in out.lower()


# --- auto-focus pre-step in _execute ---------------------------------------

def test_execute_focuses_when_setting_on():
    foc = _RecordingFocuser()
    cap = _cap(focuser=foc, cfg=KeybindConfig(enabled=True, focus_before_inject=True))
    cap._execute(cap._macros["landing_gear"])
    assert foc.ensured == 1                     # pulled ED forward before pressing


def test_execute_does_not_focus_when_setting_off():
    foc = _RecordingFocuser()
    cap = _cap(focuser=foc, cfg=KeybindConfig(enabled=True, focus_before_inject=False))
    cap._execute(cap._macros["landing_gear"])
    assert foc.ensured == 0                     # ambient-focus behaviour preserved


def test_execute_survives_without_focuser():
    cap = _cap(focuser=None, cfg=KeybindConfig(enabled=True, focus_before_inject=True))
    # No focuser (off-Windows) must not break the press.
    out = cap._execute(cap._macros["landing_gear"])
    assert "sent" in out.lower()


# --- auto-focus pre-step in the comms injector -----------------------------

class _RecExec:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, binding) -> None:
        self.pressed.append(binding.action)


def test_comms_injector_focuses_before_paste_when_focuser_present():
    foc = _RecordingFocuser()
    inj = ClipboardTextInjector(executor=_RecExec(), copy=lambda _t: None, sleep=lambda _s: None,
                                focuser=foc)
    inj.inject("o7")
    assert foc.ensured == 1


def test_comms_injector_does_not_focus_without_focuser():
    foc = _RecordingFocuser()
    inj = ClipboardTextInjector(executor=_RecExec(), copy=lambda _t: None, sleep=lambda _s: None)
    inj.inject("o7")
    assert foc.ensured == 0                     # off -> ambient behaviour


def test_comms_injector_focus_failure_does_not_block_send():
    class _BadFocuser:
        def ensure_foreground(self):
            raise RuntimeError("nope")
    ex = _RecExec()
    inj = ClipboardTextInjector(executor=ex, copy=lambda _t: None, sleep=lambda _s: None,
                                focuser=_BadFocuser())
    inj.inject("o7")                            # must still paste despite the focus fault
    assert ex.pressed  # Ctrl+V was pressed


# --- combat reflexes must NOT focus (latency) ------------------------------

def test_reflex_capability_has_no_focuser_seam():
    from covas.capabilities.reflex_capability import ReflexCapability
    params = inspect.signature(ReflexCapability.__init__).parameters
    assert "focuser" not in params             # reflexes can't foreground (no seam) -> no latency


def test_non_injecting_capability_has_no_focuser_seam():
    # Auto-focus is scoped to the two injection sites; a non-injecting capability like HUD
    # placement never foregrounds ED, verified structurally by the absence of the seam.
    from covas.capabilities.hud_placement_capability import HudPlacementCapability
    params = inspect.signature(HudPlacementCapability.__init__).parameters
    assert "focuser" not in params
