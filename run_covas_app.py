"""Native-window entry point (I4): the control panel in a real OS window via PyWebView —
no browser tab, no URL bar. Closing the window QUITS the app (INSTALLER_DESIGN decision #4:
no tray, no background loop after close). This is the packaged app's entry; run_covas_ui.py
(the browser path) stays as the unchanged source-dev / fallback entry.

Threading model: PyWebView's GUI loop must own the MAIN thread, so Flask + the voice loop run
on a background daemon thread and webview.start() blocks the main thread until the window
closes. That's the mirror image of run_covas_ui.py (where Flask blocks the main thread and a
daemon watches for quit).

pywebview is a BUILD dependency (requirements-build.txt), not base runtime — so this module
imports it lazily inside main(), and a source run that never calls this entry needs it not.
"""
import os
import socket
import sys
import threading
import time

from covas import firstrun
from covas.app import App
from covas.config import load_config
from covas.setup_web import run_first_run
from covas.single_instance import ensure_single_instance
from covas.web import create_app


def _selftest() -> int:
    """Headless build check (`COVAS++.exe --selftest`): import every native/heavy dependency and
    the whole covas app graph, then exit — proving a FROZEN build bundled them all WITHOUT needing
    a display, mic, or keys (the Phase-0 spike's self-test, made a first-class entry). A missing
    bundled lib raises ImportError and exits non-zero, which build.ps1 surfaces as a failure."""
    import importlib
    mods = [
        # native / heavy risks
        "ctranslate2", "sounddevice", "soundfile", "faster_whisper", "onnxruntime", "av",
        "numpy", "requests", "anthropic", "flask", "flask_sock", "webview",
        # the app graph (pulls in the rest of covas transitively)
        "covas.config", "covas.app", "covas.web", "covas.firstrun", "covas.setup_web",
    ]
    for m in mods:
        importlib.import_module(m)
    # onnxruntime is what backs the (lazily-imported) Silero VAD we run with vad_filter=True — make
    # the check meaningful by touching the VAD module too, not just importing faster_whisper.
    importlib.import_module("faster_whisper.vad")
    print(f"SELFTEST OK: imported {len(mods) + 1} modules incl. onnxruntime/av/ctranslate2.",
          flush=True)
    return 0


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


def main() -> None:
    # pywebview is a packaging-only dep; import here so the base runtime never requires it.
    import webview

    # Refuse a second instance before loading anything — two voice loops would fight over the
    # mic and speakers. Held for the process lifetime.
    instance_lock = ensure_single_instance()  # noqa: F841 — keep the lock alive

    # First-run gate (I3): the wizard still runs in the browser (a one-time, fresh-install step);
    # the ONGOING control panel is what gets the native window. Keeping the wizard as a browser
    # flow avoids PyWebView's single-start constraint (start() owns the main thread once) — a
    # native wizard window would need a second start() after App is built, which is fragile.
    cfg = load_config()
    if not firstrun.is_configured(cfg):
        run_first_run(cfg)

    core = App()
    try:
        core.start()                      # install PTT / cancel key hooks
    except ValueError as e:
        print(f"Bad key name in config [keys]: {e}")
        return

    host = core.cfg["ui"]["host"]
    port = int(core.cfg["ui"]["port"])
    url = f"http://{host}:{port}"

    # Quiet werkzeug's per-request logging: once the window opens it fires a burst of GET /…
    # requests that otherwise floods the console and buries the banner below. For a native app
    # the console is secondary (the window is the UI), so keep it to warnings/errors only.
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

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

    window = webview.create_window("COVAS++", url=url, width=1200, height=820,
                                   min_size=(900, 640))

    # Ctrl+Alt+Q still quits: the hotkey sets the quit event; this watcher destroys the window,
    # which unblocks webview.start() below. A user closing the window reaches the same exit path
    # (start() returns) without the destroy.
    def _await_quit() -> None:
        core.wait_for_quit()
        try:
            window.destroy()
        except Exception:  # noqa: BLE001 — window already gone (user closed it first)
            pass
    threading.Thread(target=_await_quit, name="quit-watch", daemon=True).start()

    # Blocks on the main thread until the window is closed (user close OR quit-watch destroy).
    webview.start()

    # Window closed -> quit. request_quit() covers the user-close case (unblocks the watcher);
    # shutdown() stops watchers/mixer and closes the log. os._exit is the pragmatic stop for a
    # desktop app — it guarantees the keyboard hooks + daemon Flask thread go down immediately.
    print("\nCOVAS++ shutting down. o7")
    core.request_quit()
    core.shutdown()
    os._exit(0)


if __name__ == "__main__":
    # A windowed (console=False) frozen build has no console, so PyInstaller leaves sys.stdout/
    # sys.stderr as None — any print() in the app (banner, App startup lines) would then crash.
    # Redirect them to a null sink so every write is safe; real diagnostics go to the log file
    # under %APPDATA%\COVAS++\logs. A source run keeps its normal console.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 — process-lifetime sink, never closed
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    main()
