"""Entry point: COVAS++ voice loop + local web control panel (Phase 3)."""
import threading
import webbrowser

from covas.app import App
from covas.web import create_app


def main() -> None:
    core = App()
    try:
        core.start()                      # install PTT / cancel key hooks
    except ValueError as e:
        print(f"Bad key name in config [keys]: {e}")
        return

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
