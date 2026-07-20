"""Native-window entry point (I4/I9): the WHOLE fresh-install experience — first-run wizard AND
the ongoing control panel — in a real OS window via PyWebView, no browser tab, no URL bar.
Closing the window QUITS the app (INSTALLER_DESIGN decision #4: no tray, no background loop after
close). This is the packaged app's entry; run_covas_ui.py (the browser path) stays as the
unchanged source-dev / fallback entry.

Single-window handoff (I9): PyWebView's `webview.start()` owns the main thread and starts cleanly
only ONCE, so we use exactly one window and one start() for the whole session. On a fresh install
that window first loads the wizard (served by a stoppable werkzeug server on 127.0.0.1:8765); when
the user finishes, a background thread stops the wizard server, builds `App()` (STT weights + keys
are now on disk), starts the real panel Flask server on the SAME port, and navigates the SAME
window to it via `window.load_url` — never a second start(). An already-configured install skips
the wizard and brings the panel up before the window loads. No browser opens at any point.

Threading model: the GUI loop owns the main thread; Flask + the voice loop run on background
daemon threads; `webview.start()` blocks the main thread until the window closes. That's the
mirror image of run_covas_ui.py (where Flask blocks the main thread and a daemon watches for quit).

pywebview is a BUILD dependency (requirements-build.txt), not base runtime — so this module
imports it lazily inside main(), and a source run that never calls this entry needs it not.
"""
import logging
import os
import socket
import sys
import threading
import time

from covas import firstrun
from covas import setup_web
from covas.__version__ import __version__
from covas.app import App
from covas.config import load_config
from covas.single_instance import ensure_single_instance
from covas.web import create_app


def _selftest() -> int:
    """Headless build check (`COVAS++.exe --selftest`): import every native/heavy dependency and
    the whole covas app graph, then exit — proving a FROZEN build bundled them all WITHOUT needing
    a display, mic, or keys (the Phase-0 spike's self-test, made a first-class entry). A missing
    bundled lib raises ImportError and exits non-zero, which build.ps1 surfaces as a failure."""
    import importlib
    mods = [
        # native / heavy risks. pywhispercpp (whisper.cpp STT, issue #206) + its top-level
        # _pywhispercpp extension — importing the extension load-links the ggml/whisper DLLs, so this
        # proves the native STT backend bundled and LOADS (replaces the old faster-whisper/av stack).
        "pywhispercpp", "_pywhispercpp",
        "sounddevice", "soundfile",
        "numpy", "requests", "anthropic", "flask", "flask_sock", "webview",
        # the app graph (pulls in the rest of covas transitively)
        "covas.config", "covas.app", "covas.web", "covas.firstrun", "covas.setup_web",
        # Swappable provider modules — imported LAZILY from the factory, so importing covas.app
        # doesn't prove they're bundled. Import each so a missing one fails the FROZEN build.
        "covas.providers.whispercpp_stt",
        "covas.providers.edge_tts", "covas.providers.azure_tts", "covas.providers.openai_tts",
        "covas.providers.cartesia_tts", "covas.providers.piper_tts", "covas.providers.elevenlabs_tts",
        "covas.providers.openai_llm", "covas.providers.gemini_llm",
    ]
    for m in mods:
        importlib.import_module(m)
    # edge-tts (+ its aiohttp stack) is the DEFAULT voice and is imported lazily inside EdgeTTS —
    # import the THIRD-PARTY package here so the frozen build proves it bundled it (else the shipped
    # app's default voice silently degrades to text). See covas.spec.
    importlib.import_module("edge_tts")
    print(f"SELFTEST OK: imported {len(mods) + 1} modules incl. pywhispercpp/edge_tts.",
          flush=True)
    return 0


def _wizard_panel_urls(host: str, port: int) -> tuple[str, str]:
    """The (wizard, panel) URLs for the single native window. They MUST differ by path: the I9
    handoff navigates the SAME window from the wizard to the panel, and WebView2 treats a
    `load_url` to the CURRENT url as a no-op — so identical URLs would leave the window stuck on
    the finished wizard page even though the panel is already serving. The wizard has a dedicated
    `/setup` route, so it lives there and the panel stays at `/`."""
    base = f"http://{host}:{port}"
    return f"{base}/setup", f"{base}/"


def _wait_until_serving(host: str, port: int, timeout: float = 10.0) -> bool:
    """Block until the Flask thread is accepting connections (or timeout). Without this the
    window can load before the server binds and show a connection error on first paint."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _install_quit_watch(core: App, window) -> None:
    """Ctrl+Alt+Q still quits: the hotkey (registered in core.start()) sets the quit event; this
    watcher destroys the window, which unblocks webview.start() in main(). A user closing the
    window reaches the same exit path (start() returns) without the destroy. Only meaningful once
    `core` exists — during the wizard there is no core, so closing the window is the only way out
    and that path already quits cleanly."""
    def _await_quit() -> None:
        core.wait_for_quit()
        try:
            window.destroy()
        except Exception:  # noqa: BLE001 — window already gone (user closed it first)
            pass
    threading.Thread(target=_await_quit, name="quit-watch", daemon=True).start()


def _start_panel(cfg: dict, host: str, port: int) -> App | None:
    """Build the real App and start the control-panel Flask server on `host:port`, blocking until
    it is accepting connections. Returns the started `App`, or None if config is bad (a bad PTT
    key name) so the caller can quit instead of showing a broken window. Building App loads the
    (now-downloaded) STT weights and installs the PTT/cancel hooks."""
    core = App()
    try:
        core.start()                      # install PTT / cancel key hooks
    except ValueError as e:
        print(f"Bad key name in config [keys]: {e}")
        return None

    # Flask on a background daemon thread — webview owns the main thread. threaded=True so the
    # panel's websocket + REST endpoints serve concurrently.
    flask_app = create_app(core)
    threading.Thread(
        target=lambda: flask_app.run(host=host, port=port, threaded=True, use_reloader=False),
        name="flask-server", daemon=True).start()
    _wait_until_serving(host, port)

    # flush=True so this lands immediately (the process then blocks in webview.start()) — it's
    # the "you launched the NATIVE entry" signal, so it must not sit buffered or get buried.
    ptt = core.cfg['keys']['push_to_talk']
    print("\n================ COVAS++ (native window) ================", flush=True)
    print(f"  Talk : hold [{ptt}]   ·   Cancel : tap [{ptt}] briefly", flush=True)
    print("  Quit : close the window or press Ctrl+Alt+Q", flush=True)
    print("========================================================\n", flush=True)
    return core


def main() -> None:
    # pywebview is a packaging-only dep; import here so the base runtime never requires it.
    import webview

    # Refuse a second instance before loading anything — two voice loops would fight over the
    # mic and speakers. Held for the process lifetime.
    instance_lock = ensure_single_instance()  # noqa: F841 — keep the lock alive

    # Quiet werkzeug's per-request logging: once the window opens it fires a burst of GET /…
    # requests that otherwise floods the console. For a native app the console is secondary
    # (the window is the UI), so keep it to warnings/errors only — for the wizard server too.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    cfg = load_config()
    host = cfg["ui"]["host"]
    port = int(cfg["ui"]["port"])
    # Distinct wizard/panel URLs so the handoff is a real navigation (see _wizard_panel_urls).
    wizard_url, panel_url = _wizard_panel_urls(host, port)

    # Shared state across the GUI thread and the background boot thread. `core` is filled once the
    # panel App is built; `closing` guards the wizard→panel handoff against a mid-wizard window
    # close so we don't build an App nobody will see.
    state: dict = {"core": None, "closing": False}

    # I9 single-window handoff: whatever serves the FIRST page must be up BEFORE the window loads
    # it. Configured install → bring the panel up now. Fresh install → start only the lightweight
    # wizard server now (fast, no App/STT load); the panel is built later, after setup finishes.
    configured = firstrun.is_configured(cfg)
    setup_handle = None
    if configured:
        core = _start_panel(cfg, host, port)
        if core is None:
            return
        state["core"] = core
    else:
        setup_handle = setup_web.start_setup_server(cfg, native=True)  # (srv, thread, done)
        print("\n================ COVAS++ first-run setup ================", flush=True)
        print("  Complete setup in the window: API keys, speech model, mic.", flush=True)
        print("========================================================\n", flush=True)

    # The one and only window and start() for the session. A fresh install opens on the wizard
    # (`/setup`); the handoff below navigates the SAME window to the panel (`/`). A configured
    # install opens straight on the panel.
    # zoomable=True (issue #116): native trackpad/touch pinch-zoom in the packaged WebView2
    # window, alongside the in-page Ctrl+/-/0 and Ctrl+scroll zoom (_zoom.html).
    window = webview.create_window(f"COVAS++ v{__version__}", url=(panel_url if configured else wizard_url),
                                   width=1200, height=820, min_size=(900, 640), zoomable=True)

    if configured:
        _install_quit_watch(state["core"], window)

    def _on_closed() -> None:
        # If the user bails mid-wizard, unblock the boot thread's done.wait() and mark closing so
        # it won't go on to build a panel for a window that's gone.
        state["closing"] = True
        if setup_handle is not None:
            setup_handle[2].set()  # done event
    window.events.closed += _on_closed

    def _boot() -> None:
        """Runs after the GUI loop starts (webview.start(func=...)), so window.load_url is safe.
        Only used on a fresh install: wait for the wizard to finish, stop its server, then build
        the panel and swap the SAME window over to it."""
        srv, t, done = setup_handle
        done.wait()                       # wizard finished (or window closed → _on_closed set it)
        srv.shutdown()
        t.join(timeout=5)
        if state["closing"]:              # window closed mid-wizard — quit path owns cleanup
            return
        print("Setup complete — starting COVAS++.\n", flush=True)
        core = _start_panel(cfg, host, port)
        if core is None:                  # bad config after setup — quit cleanly
            try:
                window.destroy()
            except Exception:  # noqa: BLE001 — window already gone
                pass
            return
        state["core"] = core
        _install_quit_watch(core, window)
        # Navigate the SAME window from /setup to / (the panel). A DIFFERENT path than the wizard,
        # so WebView2 performs a real navigation instead of no-opping a same-URL load. No second
        # start().
        window.load_url(panel_url)

    # Blocks on the main thread until the window is closed (user close OR quit-watch destroy).
    # On a fresh install, `_boot` drives the wizard→panel handoff on a pywebview-managed thread.
    webview.start(_boot) if setup_handle is not None else webview.start()

    # Window closed -> quit. request_quit() covers the user-close case (unblocks the watcher);
    # shutdown() stops watchers/mixer and closes the log. os._exit is the pragmatic stop for a
    # desktop app — it guarantees the keyboard hooks + daemon Flask thread go down immediately.
    print("\nCOVAS++ shutting down. o7")
    core = state["core"]
    if core is not None:                  # None if the user bailed during the wizard
        core.request_quit()
        core.shutdown()
    os._exit(0)


def _null_sink():
    """A process-lifetime discard stream that never raises on odd Unicode. A windowed
    (console=False) frozen build has no console, so PyInstaller leaves sys.stdout/stderr as
    None and the app writes model output — which can contain emoji or non-Latin glyphs — here.
    Open it utf-8 with errors='replace' so an unencodable glyph is dropped, not raised: a
    cp1252-default sink would UnicodeEncodeError and crash the turn mid-reply (e.g. a model
    that emits Arabic or an emoji)."""
    return open(os.devnull, "w", encoding="utf-8", errors="replace")  # noqa: SIM115 — process-lifetime sink


def _ensure_writable_std_streams() -> None:
    """Give a windowed frozen build (no console) safe stdout/stderr so no print()/write can
    crash the app; real diagnostics go to the log file under %APPDATA%\\COVAS++\\logs. A source
    run keeps its normal console (already utf-8-hardened in covas.app)."""
    if sys.stdout is None:
        sys.stdout = _null_sink()
    if sys.stderr is None:
        sys.stderr = _null_sink()


if __name__ == "__main__":
    _ensure_writable_std_streams()
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    main()
