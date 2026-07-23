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

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass

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
    checklist: str | None = None
    route: str | None = None
    callout: str | None = None


# Inline Markdown the checklist/callout source text may legitimately contain (it comes from the
# user's checklist file or LLM-authored prose) but that none of the three HUD surfaces render —
# web `hud.html` stays on the safe `textContent` path (never innerHTML), tkinter paints a plain
# label, and the SteamVR overlay rasterizes through a bitmap font. So literal `**`/`` ` ``/`_`
# glyphs would otherwise show verbatim in every surface (issue #122). Stripped once here, in the
# shared adapter, rather than per-surface.
_MD_LEADING_RE = re.compile(r"^\s*(?:[-*+]\s+|#{1,6}\s+)")          # "- ", "# ", "## foo" ...
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")                # **bold** / __bold__
# Emphasis stripping. The `*` form allows intra-word emphasis (CommonMark does), but the `_`
# form must NOT eat underscores inside a word — snake_case identifiers and filenames like
# `check_setup_now` are common in checklist/callout text and would otherwise collapse to
# `checksetupnow` (issue #158). So the `_em_` branch requires word boundaries: the opening `_`
# is not preceded by a word char and the closing `_` is not followed by one, which leaves
# intra-word underscores untouched while still stripping real ` _emphasis_ `.
_MD_ITALIC_RE = re.compile(
    r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"          # *em*
    r"|(?<![\w])_(?!_)(.+?)(?<!_)_(?![\w])")        # _em_ — word-bounded, spares snake_case
_MD_CODE_RE = re.compile(r"`([^`]*)`")                              # `code`


def _plain(text: str | None) -> str | None:
    """Strip inline Markdown to plain prose for the HUD (issue #122): bold/italic emphasis,
    inline code backticks, and a leading list/heading marker collapse to readable text so
    'Elvira Martuuk - **Location:** ...' shows as 'Elvira Martuuk - Location: ...' instead of
    with literal asterisks. Pure, and must NEVER raise — a regex glitch falls back to the raw
    string, matching the HUD's fail-soft rule (a display glitch degrades, it never crashes the
    loop). `None`/empty pass through unchanged."""
    if not text:
        return text
    try:
        result = _MD_LEADING_RE.sub("", text)
        result = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", result)
        result = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", result)
        result = _MD_CODE_RE.sub(lambda m: m.group(1), result)
        return result
    except Exception:  # noqa: BLE001 — a regex glitch must not blank or crash the HUD row
        return text


def checklist_line(checklist) -> str | None:
    """The current checklist step for the HUD: the first PENDING item plus a done/total count
    ('Scan the nav beacon  (3/10 done)'), or None when there's nothing to show. Pure over a
    `Checklist`-like object (anything with `next_pending`); reads the file fresh so a hand-edit
    or a voice update shows up live. Never raises — a checklist glitch just blanks the row.
    Markdown in the item text (bold/italic/code/list markers) is stripped for the HUD (#122)."""
    try:
        pending, done, total = checklist.next_pending(1)
    except Exception:  # noqa: BLE001 — the HUD must survive a checklist read error
        return None
    if not total:
        return None
    if not pending:
        return f"All {total} items done"
    _num, text = pending[0]
    return f"{_plain(text)}  ({done}/{total} done)"


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
        checklist_provider: Callable[[], str | None] | None = None,
        load_navroute: Callable[[], dict | None] | None = None,
        state: str = "Idle",
    ) -> None:
        self._lock = threading.Lock()
        self._state = str(state or "Idle")
        self._checklist_provider = checklist_provider or (lambda: None)
        self._load_navroute = load_navroute or (lambda: None)
        self._tracker = RouteTracker()
        # None = unknown (no target locked yet); True/False = the last locked target's star.
        self._next_scoopable: bool | None = None
        self._callout: str | None = None

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
                        self._callout = _plain(line)  # strip Markdown for the HUD row (#122)
                return

    def _on_ed_event(self, event: dict) -> None:
        name = event.get("event")
        if name == "NavRoute":
            # Read NavRoute.json OUTSIDE the lock — file I/O must not block a concurrent
            # snapshot() repaint on the view's Tk thread (issue #158). Load first, then apply
            # the parsed result under the lock (an in-memory tracker update, not I/O).
            data = self._load_navroute()
            with self._lock:
                if data:
                    self._tracker.load(data)
                    self._next_scoopable = None
            return
        with self._lock:
            if name == "NavRouteClear":
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

    def _safe_checklist(self) -> str | None:
        try:
            return self._checklist_provider()
        except Exception:  # noqa: BLE001
            return None

    def _route_line(self) -> str | None:
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

# The fixed top-to-bottom order of the four HUD rows. A row that blinks out (empty) and back in
# must return to its own slot, not the bottom of the stack (issue #158) — `_row_pack_before`
# resolves where to re-insert it so the render order stays stable.
_ROW_ORDER: tuple[str, ...] = ("voice_state", "checklist", "route", "callout")


def _row_pack_before(order: tuple[str, ...], visible: set[str], key: str) -> str | None:
    """Which currently-visible row should `key` be packed BEFORE to keep the fixed order?

    Pure ordering logic (unit-testable without a display): returns the first row after `key`
    in `order` that is currently visible — so a returning row re-inserts above everything that
    sits below it, landing back in its original slot. Returns None when nothing below it is
    visible, meaning "pack at the end" is already correct."""
    try:
        idx = order.index(key)
    except ValueError:
        return None
    for other in order[idx + 1:]:
        if other in visible:
            return other
    return None


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
                 *, log: Callable[[str], None] | None = None) -> None:
        self._provider = snapshot_provider
        self._log = log
        self._lock = threading.Lock()
        self._visible = False
        self._closing = False
        self._ready = threading.Event()
        self._ok = False
        self._root = None
        self._rows: dict[str, object] = {}
        self._visible_rows: set[str] = set()   # which rows are currently packed (for stable order)
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------
    def start(self, timeout: float = 5.0) -> bool:
        """Spawn the Tk thread and wait until it's built the window (or failed). Returns True
        only if the overlay is live — the caller treats False as "no 2D surface here"."""
        self._thread = threading.Thread(target=self._run, name="hud-view", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            # Build didn't finish in time. Don't orphan a half-built, hidden Tk root behind a
            # live daemon thread (issue #158): flag a close so the moment the Tk thread reaches
            # its poll it tears the root down instead of lingering. Report "no surface".
            self._logline("HUD window build timed out; tearing it down.")
            self.close()
            return False
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
            # If `_build` raised after tk.Tk() succeeded, the root would otherwise leak — an
            # undestroyed, hidden Tk root pinning this thread (issue #158). Destroy it here so a
            # failed build leaves nothing behind, then report "no surface" to the waiting caller.
            root = self._root
            if root is not None:
                try:
                    root.destroy()
                except Exception:  # noqa: BLE001 — teardown must not mask the original failure
                    pass
                self._root = None
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
            self._visible_rows.add(key)   # packed at build; _render collapses the empty ones

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
        """Set a row's text and collapse it when empty, so the panel shrinks to what's live.

        A row that returns after being empty must re-appear in its FIXED slot, not at the bottom
        of the pack stack (issue #158): `_row_pack_before` finds the visible row below it and packs
        it `before` that, so the top-to-bottom order never scrambles when rows blink in and out."""
        lbl = self._rows[key]
        lbl.configure(text=text)
        if text:
            if key not in self._visible_rows:
                before_key = _row_pack_before(_ROW_ORDER, self._visible_rows, key)
                if before_key is not None:
                    lbl.pack(fill="x", anchor="w", before=self._rows[before_key])
                else:
                    lbl.pack(fill="x", anchor="w")
                self._visible_rows.add(key)
        else:
            if key in self._visible_rows:
                lbl.pack_forget()
                self._visible_rows.discard(key)

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def make_view(snapshot_provider: Callable[[], HudSnapshot],
              *, log: Callable[[str], None] | None = None) -> HudView | None:
    """Build and start a `HudView`, or return None when tkinter / a display is unavailable
    (headless CI, no toolkit). This is the single guarded entry point for creating a window —
    the capability calls it only when the HUD is enabled, so the default test run never does."""
    view = HudView(snapshot_provider, log=log)
    if view.start():
        return view
    return None


class WebHudView:
    """The web HUD 'surface' for the surface-agnostic reconcile contract (issue #103).

    Deliberately trivial, and that's the design: the transparent `/hud` page is served by Flask
    whether or not anything watches it, and its visibility is decided entirely by the
    `[hud].web_enabled` flag that `/api/hud` reads (off => the page renders empty). So this view
    holds NO resources — it exists only to satisfy `_reconcile_surface`'s show/hide/close contract
    and to log, once per enable, the URL to paste into OpenKneeboard's Web Dashboard tab.

    Built by a factory that returns None when the control panel isn't running — the web HUD needs
    `run_covas_ui.py` (headless `run_covas.py` starts no Flask server), so a None keeps the surface
    off with the standard "unavailable" log rather than failing silently."""

    def __init__(self, url: str, *, log: Callable[[str], None] | None = None) -> None:
        self._url = url
        self._log = log
        self._announced = False   # log the paste-in URL once per enable, not every reconcile

    def show(self) -> None:
        if not self._announced:
            self._announced = True
            if self._log is not None:
                self._log(f"Web HUD live — add this as an OpenKneeboard Web Dashboard tab: "
                          f"{self._url}")

    def hide(self) -> None:
        self._announced = False   # re-announce the URL if it's turned back on

    def close(self) -> None:
        self._announced = False


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
        view_factory: Callable[[Callable[[], HudSnapshot]], HudView | None] = make_view,
        vr_is_enabled: Callable[[], bool] | None = None,
        vr_view_factory: Callable[[Callable[[], HudSnapshot]], object | None] | None = None,
        vr_permanent: Callable[[], bool] | None = None,
        web_is_enabled: Callable[[], bool] | None = None,
        web_view_factory: Callable[[Callable[[], HudSnapshot]], object | None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.model = model
        self._is_enabled = is_enabled
        self._view_factory = view_factory
        self._vr_is_enabled = vr_is_enabled or (lambda: False)
        self._vr_view_factory = vr_view_factory
        # () -> True only when the VR surface's failure is PERMANENT (openvr not importable), so a
        # failed build LATCHES; a TRANSIENT failure (SteamVR not up yet) does NOT, and a later
        # enable/reconcile/pin re-attempts (issue #140, DESIGN §3.8.1). Absent -> old always-latch.
        self._vr_permanent = vr_permanent
        self._web_is_enabled = web_is_enabled or (lambda: False)
        self._web_view_factory = web_view_factory
        self._log = log
        self._view: HudView | None = None
        self._view_tried = False  # don't re-attempt a failed/headless window every settings change
        self._vr_view: object | None = None
        self._vr_view_tried = False
        self._web_view: object | None = None
        self._web_view_tried = False
        # Serializes the check-then-act in `_reconcile_surface`: reconcile() can be called from
        # more than one thread (startup, a settings change, `on_web_ui_ready`), and an unlocked
        # "view is None and not tried -> build" is a race that could double-build a surface
        # (two Tk roots / two overlays) — issue #158. One lock makes the whole reconcile atomic.
        self._reconcile_lock = threading.Lock()
        self._reconcile()          # show at startup if already enabled

    # -- capability interface ----------------------------------------------------------
    def tools(self) -> list[dict]:
        return []  # non-interactive view — no LLM tools

    def help_meta(self) -> HelpMeta:
        return HelpMeta(
            category="the HUD",
            group="settings",
            one_liner=("I can show a small always-on-top overlay with my current state, your "
                       "active checklist step, and your route progress — on your desktop, in VR "
                       "floating in the cockpit, or as a transparent web page for OpenKneeboard. "
                       "Off by default."),
            example="turn the HUD on",
            help_when_active=("Say 'turn the HUD on' or 'off' — it's a glanceable panel showing "
                              "whether I'm listening or speaking, your current checklist item, "
                              "and jumps remaining on your route. In VR, turn the VR HUD on for "
                              "the same panel as a SteamVR overlay in the headset. On a non-SteamVR "
                              "rig (OpenComposite / VDXR / Virtual Desktop), turn the web HUD on and "
                              "point OpenKneeboard's Web Dashboard at /hud for the same panel "
                              "in-headset (needs the control panel running)."),
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

    def set_vr_placement(self, placement) -> None:
        """Relay a new placement to the live VR overlay (position / distance / pitch / curvature
        / width), so a Settings or voice change repositions it without a re-toggle. No-op when the
        VR surface isn't up or doesn't support live placement. Fail-soft."""
        view = self._vr_view
        setter = getattr(view, "set_placement", None)
        if setter is not None:
            try:
                setter(placement)
            except Exception as e:  # noqa: BLE001 — a reposition glitch must not disturb the loop
                self._logline(f"VR HUD reposition failed: {e}")

    def pin_vr_here(self):
        """Look-to-place: ask the live VR overlay to swing to the direction you're facing.
        Returns the new placement (for the caller to persist) or None when the VR surface isn't
        up or doesn't support pinning. Fail-soft."""
        view = self._vr_view
        pin = getattr(view, "pin_here", None)
        if pin is None:
            return None
        try:
            return pin()
        except Exception as e:  # noqa: BLE001 — a pin glitch must not disturb the loop
            self._logline(f"VR HUD pin failed: {e}")
            return None

    def recenter_vr_here(self):
        """Horizontal recentre (issue #144): ask the live overlay to snap its heading (yaw) to
        the CURRENT HMD heading, keeping distance/height/tilt/size/curvature. Returns the new
        placement (for the caller to persist) or None when the VR surface isn't up or doesn't
        support it. Fail-soft."""
        view = self._vr_view
        recenter = getattr(view, "recenter_here", None)
        if recenter is None:
            return None
        try:
            return recenter()
        except Exception as e:  # noqa: BLE001 — a recenter glitch must not disturb the loop
            self._logline(f"VR HUD recenter failed: {e}")
            return None

    def vr_attach_reason(self):
        """The typed reason the VR overlay isn't up *right now* (for the spoken failure line when
        a pin/recenter can't attach), or None when the overlay is live. When a view exists it's up;
        otherwise a cheap fresh probe distinguishes openvr-missing (permanent) from
        SteamVR-not-running (transient). Fail-soft — never raises into the voice loop. (#140)"""
        if self._vr_view is not None:
            return None
        try:
            from .vr_hud import probe_vr_reason  # lazy: avoid an import cycle at module load
            return probe_vr_reason()
        except Exception:  # noqa: BLE001 — no reason available => generic
            return None

    def on_web_ui_ready(self) -> None:
        """The control panel (Flask) has come up after startup. Clear the web surface's one-shot
        'tried' latch and reconcile, so a web HUD enabled BEFORE the server existed (e.g. on at
        startup) gets one more chance to attach now that /hud is actually served (#103)."""
        self._web_view_tried = False
        self._reconcile()

    def shutdown(self) -> None:
        """Tear every overlay down on app exit (idempotent)."""
        for attr in ("_view", "_vr_view", "_web_view"):
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
        display, or no VR runtime) never affects the other.

        Held under `_reconcile_lock` so the per-surface check-then-act (build-once-on-first-enable)
        can't race two concurrent callers into building the same surface twice (issue #158)."""
        with self._reconcile_lock:
            self._reconcile_surface(
                "_view", "_view_tried", self._is_enabled, self._view_factory,
                label="HUD", unavailable="no 2D overlay is available (no display / toolkit)")
            if self._vr_view_factory is not None:
                self._reconcile_surface(
                    "_vr_view", "_vr_view_tried", self._vr_is_enabled, self._vr_view_factory,
                    label="VR HUD",
                    unavailable="no VR runtime is available (openvr / SteamVR absent)",
                    latch_on_fail=self._vr_permanent)
            if self._web_view_factory is not None:
                self._reconcile_surface(
                    "_web_view", "_web_view_tried", self._web_is_enabled, self._web_view_factory,
                    label="Web HUD",
                    unavailable="the control panel isn't running (start run_covas_ui.py, not "
                                "run_covas.py, so /hud can be served)")

    def _reconcile_surface(self, view_attr: str, tried_attr: str,
                           is_enabled: Callable[[], bool], factory, *,
                           label: str, unavailable: str,
                           latch_on_fail: Callable[[], bool] | None = None) -> None:
        """Match one surface's view to its enable flag: create+show when on, hide when off. The
        view is created lazily on first enable and reused thereafter (a toggle just hides/shows),
        so the underlying toolkit/runtime is only touched once the Commander opts in.

        ``latch_on_fail`` (VR only, issue #140): when a build returns None it is consulted — if it
        returns False the failure is TRANSIENT and the 'tried' latch is RESET so a later
        enable/reconcile/pin re-attempts (e.g. SteamVR started after COVAS++). Absent/True keeps
        the old behaviour: latch once and don't retry (right for a headless 2D box / a missing
        runtime that won't reappear)."""
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
                    # A transient failure must not lock the surface out for the session — allow a
                    # fresh attempt next reconcile (issue #140).
                    if latch_on_fail is not None:
                        try:
                            if not latch_on_fail():
                                setattr(self, tried_attr, False)
                        except Exception:  # noqa: BLE001 — fail soft: keep the latch on a probe glitch
                            pass
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
