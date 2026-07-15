"""Companion HUD — a simplified, glanceable 2D overlay (issue #47, epic #40).

A transparent, always-on-top, NON-interactive window (a *view*, not a control surface)
showing the companion-centric data only COVAS++ has — the four groups the spike (#46, see
`docs/spikes/hud-spike-46.md`) and DESIGN §3.8 call out:

  1. **Voice-loop state** — listening / thinking / speaking (from the EventBus `status`).
  2. **Current checklist step** (from the checklist model).
  3. **Route progress** — jumps remaining + next-star scoopable (from the plotted NavRoute,
     the same live surface the route callouts read).
  4. **Last proactive callout** (the last ambient line COVAS volunteered).

The design mirrors the rest of the app's "pure core + injected I/O" discipline so the
default `pytest` run exercises it offline and WITHOUT opening a window:

  * `HudModel` — the pure data adapter. It ingests EventBus events (`status` / `log` /
    `ed_event`) and reads two injected getters (the checklist line, the plotted route) to
    produce an immutable `HudSnapshot` of the four display fields. No tkinter, no threads of
    its own, fully unit-testable — the HUD is a VIEW over this injected state.
  * `HudView` — a thin tkinter sink, created ONLY when the HUD is enabled AND a display is
    available. All tkinter imports/creation are guarded (`make_view` returns None when the
    toolkit or a display is missing), so a headless CI never opens a window. Every Tk call
    happens on the view's own thread; the outside world only sets thread-safe flags.
  * `HudCapability` — the wiring. Always registered (so the toggle works live), it feeds each
    bus event into the model and, on a `settings` change, reconciles the window's visibility
    against `[hud].enabled`.

Off by default (`[hud].enabled = false`). Toggle it two ways, both projecting from the same
settings schema: in the control-panel Settings page, or by voice ("turn the HUD on" — the
settings capability writes `[hud].enabled`). Fail soft throughout — a missing toolkit, no
display, or any Tk error degrades to "no overlay", never a crash of the voice loop.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from ..ed.route import RouteTracker, is_scoopable
from .base import HelpMeta

# Raw voice-loop states (App.set_state) folded to the three glanceable words the issue names.
# Anything unmapped is shown verbatim, so a new state degrades gracefully rather than vanishing.
_STATE_LABELS: dict[str, str] = {
    "Idle": "Idle",
    "Listening": "Listening",
    "Transcribing": "Thinking",
    "Thinking": "Thinking",
    "Searching": "Thinking",
    "Speaking": "Speaking",
}

# Prefixes the app stamps on the ambient lines it volunteers (see App._proactive_worker /
# _proactive_line_worker). These — and ONLY these — count as "callouts"; a normal reply to the
# Commander (logged as a bare "COVAS" line) is not a proactive callout and must not show here.
_CALLOUT_PREFIXES: tuple[str, ...] = ("(proactive)", "(route)")


@dataclass(frozen=True)
class HudSnapshot:
    """The four display fields, resolved to ready-to-show strings. Immutable, so it can be
    handed across threads (the model builds it; the view renders it) without a lock."""
    voice_state: str = "Idle"
    checklist: Optional[str] = None
    route: Optional[str] = None
    callout: Optional[str] = None


def checklist_line(checklist) -> Optional[str]:
    """The current checklist step for the HUD: the first PENDING item plus a done/total count
    ('Scan the nav beacon  (3/10 done)'), or None when there's nothing to show. Pure over a
    `Checklist`-like object (anything with `next_pending`); reads the file fresh so a hand-edit
    or a voice update shows up live. Never raises — a checklist glitch just blanks the row."""
    try:
        pending, done, total = checklist.next_pending(1)
    except Exception:  # noqa: BLE001 — the HUD must survive a checklist read error
        return None
    if not total:
        return None
    if not pending:
        return f"All {total} items done"
    _num, text = pending[0]
    return f"{text}  ({done}/{total} done)"


class HudModel:
    """The pure data adapter — EventBus + injected state -> a `HudSnapshot`.

    Thread-safe: `on_event` runs on the app's event-pump thread while `snapshot` runs on the
    view's Tk thread, so all mutable state is guarded by one lock. Holds no I/O of its own —
    the checklist line and the plotted route arrive through injected callables, which keeps
    the whole thing unit-testable offline (tests feed events + fakes, never a real window).

    Injected seams:
      * `checklist_provider() -> str | None` — the current checklist line (app: reads the
        `Checklist`; tests: a lambda).
      * `load_navroute() -> dict | None` — the parsed NavRoute.json, re-read on each `NavRoute`
        event so the route row follows a (re)plot (app: `read_navroute`; tests: a lambda).
    """

    def __init__(
        self,
        *,
        checklist_provider: Optional[Callable[[], Optional[str]]] = None,
        load_navroute: Optional[Callable[[], Optional[dict]]] = None,
        state: str = "Idle",
    ) -> None:
        self._lock = threading.Lock()
        self._state = str(state or "Idle")
        self._checklist_provider = checklist_provider or (lambda: None)
        self._load_navroute = load_navroute or (lambda: None)
        self._tracker = RouteTracker()
        # None = unknown (no target locked yet); True/False = the last locked target's star.
        self._next_scoopable: Optional[bool] = None
        self._callout: Optional[str] = None

    # -- ingest ------------------------------------------------------------------------
    def on_event(self, event: dict) -> None:
        """Fold one EventBus event into the model. Tolerant of anything — a non-dict or an
        event it doesn't care about is ignored, and it never raises (it shares the event-pump
        thread with every other capability)."""
        try:
            if not isinstance(event, dict):
                return
            kind = event.get("type")
            if kind == "status":
                self._on_status(event)
            elif kind == "log":
                self._on_log(event)
            elif kind == "ed_event":
                self._on_ed_event(event)
        except Exception:  # noqa: BLE001 — a bad event must not take the pump down
            pass

    def _on_status(self, event: dict) -> None:
        state = event.get("state")
        if state:
            with self._lock:
                self._state = str(state)

    def _on_log(self, event: dict) -> None:
        if str(event.get("who")) != "COVAS":
            return
        text = str(event.get("text") or "").strip()
        for prefix in _CALLOUT_PREFIXES:
            if text.startswith(prefix):
                line = text[len(prefix):].strip()
                if line:
                    with self._lock:
                        self._callout = line
                return

    def _on_ed_event(self, event: dict) -> None:
        name = event.get("event")
        with self._lock:
            if name == "NavRoute":
                data = self._load_navroute()
                if data:
                    self._tracker.load(data)
                    self._next_scoopable = None
            elif name == "NavRouteClear":
                self._tracker.clear()
                self._next_scoopable = None
            elif name == "FSDTarget":
                self._on_target(event)
            elif name == "FSDJump":
                system = event.get("StarSystem")
                if system and self._tracker.active:
                    self._tracker.on_jump(system)

    def _on_target(self, event: dict) -> None:
        """Record whether the newly-locked next star is scoopable. Called under the lock."""
        if not self._tracker.active:
            return
        target = event.get("Name")  # FSDTarget carries 'Name', not 'StarSystem'
        if not target:
            return
        step = self._tracker.step_for(target)
        star_class = step.star_class if step else event.get("StarClass")
        if star_class:
            self._next_scoopable = is_scoopable(star_class)

    # -- read --------------------------------------------------------------------------
    def snapshot(self) -> HudSnapshot:
        """Build the immutable four-field snapshot the view renders. The checklist getter may
        do file I/O, so it's called OUTSIDE the lock; everything else is read under it."""
        checklist = self._safe_checklist()
        with self._lock:
            state = self._state
            route = self._route_line()
            callout = self._callout
        return HudSnapshot(
            voice_state=_STATE_LABELS.get(state, state),
            checklist=checklist,
            route=route,
            callout=callout,
        )

    def _safe_checklist(self) -> Optional[str]:
        try:
            return self._checklist_provider()
        except Exception:  # noqa: BLE001
            return None

    def _route_line(self) -> Optional[str]:
        """The route-progress row, or None when no route is plotted. Called under the lock."""
        if not self._tracker.active:
            return None
        remaining = self._tracker.jumps_remaining()
        dest = self._tracker.destination
        if remaining is None or not dest:
            return None
        if remaining == 0:
            return f"Arrived at {dest}"
        unit = "jump" if remaining == 1 else "jumps"
        line = f"{remaining} {unit} to {dest}"
        if self._next_scoopable is True:
            line += "  ·  next scoopable"
        elif self._next_scoopable is False:
            line += "  ·  next NOT scoopable"
        return line


# ---- the tkinter view (guarded — never imported/created in the default test run) ----------

# A color unlikely to appear in the panel; keyed out for transparency + click-through on Windows.
_TRANSPARENT_KEY = "#010203"


class HudView:
    """A thin transparent, always-on-top tkinter window that renders a `HudSnapshot`.

    Threading model: the Tk root, its widgets, and every Tk call live on ONE dedicated daemon
    thread. The outside world only flips thread-safe flags (`show`/`hide`/`close`); a periodic
    poll on the Tk thread reads them and the latest snapshot and applies the changes. This
    sidesteps Tkinter's single-thread rule without cross-thread `after()` calls.

    Prefer `make_view()` to construct one — it starts the thread and returns None if tkinter or
    a display is unavailable, which is how the capability stays fail-soft/headless-safe.
    """

    POLL_MS = 400  # redraw cadence — glanceable, not a game loop; cheap next to ED

    def __init__(self, snapshot_provider: Callable[[], HudSnapshot],
                 *, log: Optional[Callable[[str], None]] = None) -> None:
        self._provider = snapshot_provider
        self._log = log
        self._lock = threading.Lock()
        self._visible = False
        self._closing = False
        self._ready = threading.Event()
        self._ok = False
        self._root = None
        self._rows: dict[str, object] = {}
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------------------
    def start(self, timeout: float = 5.0) -> bool:
        """Spawn the Tk thread and wait until it's built the window (or failed). Returns True
        only if the overlay is live — the caller treats False as "no 2D surface here"."""
        self._thread = threading.Thread(target=self._run, name="hud-view", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        return self._ok

    def show(self) -> None:
        with self._lock:
            self._visible = True

    def hide(self) -> None:
        with self._lock:
            self._visible = False

    def close(self) -> None:
        with self._lock:
            self._closing = True

    # -- Tk thread ---------------------------------------------------------------------
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as e:  # noqa: BLE001 — no tkinter (rare) -> no 2D overlay, fail soft
            self._logline(f"tkinter unavailable: {e}")
            self._ready.set()
            return
        try:
            root = tk.Tk()
            self._root = root
            self._build(tk, root)
            root.withdraw()  # start hidden; the capability calls show() when enabled
            self._ok = True
            self._ready.set()
            self._poll()
            root.mainloop()
        except Exception as e:  # noqa: BLE001 — a headless box / Tcl error -> no overlay
            self._logline(f"HUD window failed: {e}")
            self._ok = False
            self._ready.set()

    def _build(self, tk, root) -> None:
        """Construct the borderless, transparent, always-on-top panel and its four rows."""
        root.title("COVAS++ HUD")
        root.overrideredirect(True)      # borderless (no title bar / controls)
        root.attributes("-topmost", True)  # always above other windows
        try:
            # Windows color-key: the keyed color renders transparent AND click-through.
            root.attributes("-transparentcolor", _TRANSPARENT_KEY)
            bg = _TRANSPARENT_KEY
        except tk.TclError:
            bg = "#0a0a0a"               # platform without color-key: opaque dark panel
        root.configure(bg=bg)

        panel = tk.Frame(root, bg="#0a0a0a", padx=12, pady=8,
                         highlightbackground="#00b3b3", highlightthickness=1)
        panel.pack(padx=8, pady=8)

        def add_row(key: str, color: str, size: int, bold: bool) -> None:
            lbl = tk.Label(panel, text="", bg="#0a0a0a", fg=color, justify="left", anchor="w",
                           font=("Segoe UI", size, "bold" if bold else "normal"))
            lbl.pack(fill="x", anchor="w")
            self._rows[key] = lbl

        add_row("voice_state", "#00e5e5", 13, True)   # the loop state — headline
        add_row("checklist", "#e0e0e0", 11, False)
        add_row("route", "#c8c8c8", 11, False)
        add_row("callout", "#9aa0a6", 10, False)

        # Park it top-right with a small margin, once geometry is realized.
        root.update_idletasks()
        w = root.winfo_width()
        sw = root.winfo_screenwidth()
        root.geometry(f"+{max(0, sw - w - 24)}+24")

        # Belt-and-suspenders click-through for the whole window (the opaque panel wouldn't be
        # click-through from the color key alone). Windows-only, best-effort — the overlay is a
        # view, never a control surface, so it must never eat a click meant for the game.
        self._make_click_through(root)

    def _make_click_through(self, root) -> None:
        try:
            import ctypes  # Windows-only; harmless import elsewhere (guarded by the call below)
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            hwnd = root.winfo_id()
            user32 = ctypes.windll.user32  # type: ignore[attr-defined] — absent off Windows
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception as e:  # noqa: BLE001 — non-Windows / no ctypes: color key still helps
            self._logline(f"click-through not applied: {e}")

    def _poll(self) -> None:
        """Apply visibility + repaint from the latest snapshot, then reschedule. Runs only on
        the Tk thread. On a close request it tears the window down and stops rescheduling."""
        root = self._root
        if root is None:
            return
        with self._lock:
            visible, closing = self._visible, self._closing
        if closing:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            if visible:
                root.deiconify()
                self._render(self._provider())
            else:
                root.withdraw()
        except Exception as e:  # noqa: BLE001 — a repaint glitch must not kill the overlay
            self._logline(f"HUD repaint error: {e}")
        root.after(self.POLL_MS, self._poll)

    def _render(self, snap: HudSnapshot) -> None:
        rows = self._rows
        rows["voice_state"].configure(text=f"● {snap.voice_state}")
        self._set_row("checklist", "▸ " + snap.checklist if snap.checklist else "")
        self._set_row("route", "⤳ " + snap.route if snap.route else "")
        self._set_row("callout", "“" + snap.callout + "”" if snap.callout else "")

    def _set_row(self, key: str, text: str) -> None:
        """Set a row's text and collapse it when empty, so the panel shrinks to what's live."""
        lbl = self._rows[key]
        lbl.configure(text=text)
        if text:
            lbl.pack(fill="x", anchor="w")
        else:
            lbl.pack_forget()

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def make_view(snapshot_provider: Callable[[], HudSnapshot],
              *, log: Optional[Callable[[str], None]] = None) -> Optional[HudView]:
    """Build and start a `HudView`, or return None when tkinter / a display is unavailable
    (headless CI, no toolkit). This is the single guarded entry point for creating a window —
    the capability calls it only when the HUD is enabled, so the default test run never does."""
    view = HudView(snapshot_provider, log=log)
    if view.start():
        return view
    return None


class HudCapability:
    """Wires the model + view(s) to the app: always registered, drives the overlay off the bus.

    It advertises no LLM tools (the HUD is a non-interactive view; its toggles are the ordinary
    `[hud].enabled` / `[hud].vr_enabled` settings, reached from the Settings page or by voice
    through the settings capability). It only reacts to bus events — feeding the model and, on a
    `settings` change, reconciling each surface's visibility against its live `enabled` flag.

    ONE model, up to TWO sinks (issue #48): the 2D tkinter window and the SteamVR overlay are
    both VIEWS over the same `HudModel` snapshot — only the rendering surface differs. Each is
    created lazily on first enable, guarded so a missing toolkit/display or a missing VR runtime
    just means "that surface is off", never a crash.

    Injected seams keep it headless-testable:
      * `is_enabled() -> bool` — read `[hud].enabled` live (app: reads `cfg`; tests: a flag).
      * `view_factory(provider) -> HudView | None` — build the 2D window (app: `make_view`;
        tests: a fake, or one returning None to simulate a headless box). Called lazily on first
        enable, so a disabled HUD never touches tkinter.
      * `vr_is_enabled() -> bool` — read `[hud].vr_enabled` live (default: always off).
      * `vr_view_factory(provider) -> VrHudView | None` — build the SteamVR overlay (app:
        `make_vr_view` with the configured placement; tests: a fake / None). Called lazily on
        first enable, so a disabled VR HUD never imports `openvr` or touches a runtime.
    """

    def __init__(
        self,
        model: HudModel,
        *,
        is_enabled: Callable[[], bool],
        view_factory: Callable[[Callable[[], HudSnapshot]], Optional["HudView"]] = make_view,
        vr_is_enabled: Optional[Callable[[], bool]] = None,
        vr_view_factory: Optional[Callable[[Callable[[], HudSnapshot]], Optional[object]]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.model = model
        self._is_enabled = is_enabled
        self._view_factory = view_factory
        self._vr_is_enabled = vr_is_enabled or (lambda: False)
        self._vr_view_factory = vr_view_factory
        self._log = log
        self._view: Optional[HudView] = None
        self._view_tried = False  # don't re-attempt a failed/headless window every settings change
        self._vr_view: Optional[object] = None
        self._vr_view_tried = False
        self._reconcile()          # show at startup if already enabled

    # -- capability interface ----------------------------------------------------------
    def tools(self) -> list[dict]:
        return []  # non-interactive view — no LLM tools

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="the HUD",
            group="settings",
            one_liner=("I can show a small always-on-top overlay with my current state, your "
                       "active checklist step, and your route progress — on your desktop or, in "
                       "VR, floating in the cockpit. Off by default."),
            example="turn the HUD on",
            help_when_active=("Say 'turn the HUD on' or 'off' — it's a glanceable panel showing "
                              "whether I'm listening or speaking, your current checklist item, "
                              "and jumps remaining on your route. In VR, turn the VR HUD on for "
                              "the same panel as a SteamVR overlay in the headset."),
        )

    def on_event(self, event: dict) -> None:
        """Bus hook (event-pump thread). Feed the live state (status / checklist / route /
        callout) into the model so a shown window repaints from it. Visibility is NOT driven
        here — the app calls `reconcile()` directly on a settings change, so the toggle works
        even before the pump is running. Never raises — it shares the pump with every other
        capability."""
        try:
            if isinstance(event, dict):
                self.model.on_event(event)
        except Exception:  # noqa: BLE001 — one bad event must not take the pump down
            pass

    def reconcile(self) -> None:
        """Match the window to the live `[hud].enabled` flag. Called at construction (startup)
        and directly by the app after any settings change, so the toggle (Settings page or
        voice) does not depend on the event pump running."""
        self._reconcile()

    def shutdown(self) -> None:
        """Tear both overlays down on app exit (idempotent)."""
        for attr in ("_view", "_vr_view"):
            view = getattr(self, attr)
            if view is not None:
                try:
                    view.close()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)

    # -- visibility reconciliation -----------------------------------------------------
    def _reconcile(self) -> None:
        """Match each surface to its live enable flag. Independent and fail-soft — the 2D
        window and the VR overlay are reconciled separately, so one being unavailable (no
        display, or no VR runtime) never affects the other."""
        self._reconcile_surface(
            "_view", "_view_tried", self._is_enabled, self._view_factory,
            label="HUD", unavailable="no 2D overlay is available (no display / toolkit)")
        if self._vr_view_factory is not None:
            self._reconcile_surface(
                "_vr_view", "_vr_view_tried", self._vr_is_enabled, self._vr_view_factory,
                label="VR HUD", unavailable="no VR runtime is available (openvr / SteamVR absent)")

    def _reconcile_surface(self, view_attr: str, tried_attr: str,
                           is_enabled: Callable[[], bool], factory, *,
                           label: str, unavailable: str) -> None:
        """Match one surface's view to its enable flag: create+show when on, hide when off. The
        view is created lazily on first enable and reused thereafter (a toggle just hides/shows),
        so the underlying toolkit/runtime is only touched once the Commander opts in."""
        try:
            want = bool(is_enabled())
        except Exception:  # noqa: BLE001 — a config read glitch => treat as off
            want = False
        view = getattr(self, view_attr)
        if want:
            if view is None and not getattr(self, tried_attr):
                setattr(self, tried_attr, True)
                try:
                    view = factory(self.model.snapshot)
                except Exception as e:  # noqa: BLE001 — build failure => that surface stays off
                    self._logline(f"{label} unavailable: {e}")
                    view = None
                setattr(self, view_attr, view)
                if view is None:
                    self._logline(f"{label} enabled but {unavailable}; continuing without it.")
            if view is not None:
                view.show()
                self._logline(f"{label} shown.")
        else:
            if view is not None:
                view.hide()
                self._logline(f"{label} hidden.")

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
