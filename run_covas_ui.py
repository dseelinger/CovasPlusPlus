"""Entry point: COVAS++ voice loop + local web control panel (Phase 3)."""
import os
import threading
import webbrowser

from covas import firstrun
from covas.app import App
from covas.config import load_config
from covas.setup_web import run_first_run
from covas.single_instance import ensure_single_instance
from covas.web import create_app


def main() -> None:
    # Refuse a second instance before loading anything — two voice loops would share the mic
    # and speakers and talk over each other. Held for the process lifetime.
    instance_lock = ensure_single_instance()  # noqa: F841 — keep the lock alive

    # First-run gate (I3): on a fresh install there are no keys and no STT weights, so building
    # App() would crash or silently block on a download. Serve the setup wizard first; it writes
    # keys/overrides/weights under data_dir and returns once configured. A source-run dev with a
    # key + model is already configured, so this is a no-op for them.
    cfg = load_config()
    if not firstrun.is_configured(cfg):
        run_first_run(cfg)

    core = App()
    try:
        core.start()                      # install PTT / cancel key hooks
    except ValueError as e:
        print(f"Bad key name in config [keys]: {e}")
        return

    # Ctrl+Alt+Q (registered in core.start()) sets the quit event, but here the Flask dev
    # server blocks the main thread and never watches it. Bridge the two: a daemon waits on
    # the quit signal, cleans up the watchers/log, and force-exits — werkzeug's dev server
    # has no clean cross-thread shutdown, so os._exit is the pragmatic stop for a desktop app.
    def _await_quit() -> None:
        core.wait_for_quit()
        print("\nCOVAS++ shutting down. o7")
        core.shutdown()
        os._exit(0)
    threading.Thread(target=_await_quit, name="quit-watch", daemon=True).start()

    flask_app = create_app(core)
    host = core.cfg["ui"]["host"]
    port = int(core.cfg["ui"]["port"])
    url = f"http://{host}:{port}"

    print("\n================ COVAS++ (Phase 3) ================")
    print(f"  Control panel : {url}")
    ptt = core.cfg['keys']['push_to_talk']
    print(f"  Talk          : hold [{ptt}]")
    print(f"  Cancel        : tap  [{ptt}] briefly")
    print("  Quit          : Ctrl+Alt+Q or close this window")
    print("==================================================\n")

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    flask_app.run(host=host, port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
