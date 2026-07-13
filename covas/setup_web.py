"""First-run setup wizard — a tiny Flask app served BEFORE the real App exists.

The control panel (web.py) needs a fully-built App (STT weights loaded, keys present); on a
fresh install none of that exists yet, and building App would block on a silent model download
or crash with no key. So the entry point runs THIS server first: it collects keys, downloads
the STT model with visible status, resolves the default voice, and picks a mic — writing
everything under data_dir — then hands control back so the normal panel can start.

`run_first_run(cfg)` blocks until the user finishes (or the model+keys are otherwise satisfied),
then shuts the wizard server down and returns. It's a no-op to call when already configured;
the caller guards with firstrun.is_configured, but finish is also re-gated here so the wizard
can't complete half-set-up.
"""
from __future__ import annotations

import threading

from flask import Flask, jsonify, render_template, request

from . import elevenlabs as el
from . import firstrun


def create_setup_app(cfg: dict, done: threading.Event) -> Flask:
    """Build the wizard app. `cfg` is mutated in place as steps write overrides (so status
    reads stay current); `done` is set when setup finishes so the caller can stop serving."""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Coarse STT-download state shared with the status poll. Real byte-progress from
    # huggingface_hub is awkward to capture reliably, so we report a state, not a percent —
    # the template shows a "downloading ~250 MB, one time" spinner. state ∈ idle|downloading|
    # ready|error.
    dl = {"state": "idle", "error": None}
    dl_lock = threading.Lock()

    @app.route("/")
    @app.route("/setup")
    def setup_page():
        return render_template("setup.html")

    @app.route("/api/setup/status")
    def status():
        st = firstrun.configured_status(cfg)
        with dl_lock:
            st["download"] = dict(dl)
        return jsonify(st)

    @app.route("/api/setup/keys", methods=["POST"])
    def save_keys():
        """Save whichever keys were supplied. Anthropic is required to finish; ElevenLabs is
        optional (absent ⇒ text-only). Empty strings are ignored, not written, so a blank
        ElevenLabs box doesn't clobber an existing key."""
        b = request.get_json(force=True) or {}
        anth = str(b.get("anthropic") or "").strip()
        elk = str(b.get("elevenlabs") or "").strip()
        try:
            if anth:
                firstrun.save_anthropic_key(cfg, anth)
            if elk:
                firstrun.save_elevenlabs_key(cfg, elk)
        except Exception as e:  # noqa: BLE001 — surface a write failure to the wizard
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "status": firstrun.configured_status(cfg)})

    @app.route("/api/setup/mics")
    def mics():
        try:
            return jsonify({"ok": True, "devices": firstrun.list_input_devices()})
        except Exception as e:  # noqa: BLE001 — no PortAudio / no devices: let the wizard note it
            return jsonify({"ok": False, "error": str(e), "devices": []}), 502

    @app.route("/api/setup/mic", methods=["POST"])
    def mic():
        """Persist the chosen capture device by NAME (a substring the Recorder resolves at
        startup — more stable across reboots than a device index). Blank clears it to the
        system default."""
        b = request.get_json(force=True) or {}
        name = str(b.get("device") or "").strip()
        firstrun.apply_override(cfg, {"audio": {"input_device": name}})
        return jsonify({"ok": True})

    @app.route("/api/setup/model", methods=["POST"])
    def model_download():
        """Kick off the STT weight download on a background thread and return immediately; the
        wizard polls /api/setup/status for the state. Idempotent: a second call while a download
        is in flight is ignored."""
        with dl_lock:
            if dl["state"] == "downloading":
                return jsonify({"ok": True, "state": "downloading"})
            dl["state"] = "downloading"
            dl["error"] = None

        def worker():
            try:
                firstrun.download_stt_model(
                    firstrun.DEFAULT_STT_MODEL, firstrun.stt_download_root(cfg))
                # Record the installed model so the app (and the gate) use small.en, then flip
                # the state — order matters so a status poll that sees "ready" also sees it set.
                firstrun.apply_override(cfg, {"whisper": {"model": firstrun.DEFAULT_STT_MODEL}})
                with dl_lock:
                    dl["state"] = "ready"
            except Exception as e:  # noqa: BLE001 — network/disk failure: report, don't crash
                with dl_lock:
                    dl["state"] = "error"
                    dl["error"] = str(e)

        threading.Thread(target=worker, name="stt-download", daemon=True).start()
        return jsonify({"ok": True, "state": "downloading"})

    @app.route("/api/setup/voice", methods=["POST"])
    def voice():
        """Resolve and store the default TTS voice. Needs the ElevenLabs key already saved;
        with no key this step is simply skipped (text-only) and reports so. Picks "George" by
        name, else the first valid voice."""
        if not firstrun.elevenlabs_key_available(cfg):
            return jsonify({"ok": True, "skipped": True, "reason": "no ElevenLabs key (text-only)"})
        try:
            voices = el.list_voices(cfg)
        except Exception as e:  # noqa: BLE001 — bad key / offline: let the wizard show it
            return jsonify({"ok": False, "error": str(e)}), 502
        chosen = firstrun.resolve_default_voice(voices)
        if chosen is None:
            return jsonify({"ok": False, "error": "your ElevenLabs account has no voices"}), 502
        firstrun.apply_override(cfg, {"elevenlabs": {
            "voice_id": chosen["voice_id"], "voice_name": chosen["name"]}})
        return jsonify({"ok": True, "voice": chosen})

    @app.route("/api/setup/finish", methods=["POST"])
    def finish():
        """Complete setup — but only if the required pieces are actually in place, so the
        wizard can't hand a half-configured app to the panel. Sets the done event the server
        loop waits on."""
        if not firstrun.is_configured(cfg):
            return jsonify({"ok": False, "status": firstrun.configured_status(cfg),
                            "error": "not finished: need the Anthropic key and the STT model"}), 400
        done.set()
        return jsonify({"ok": True})

    return app


def run_first_run(cfg: dict) -> None:
    """Serve the wizard and block until it's finished, then return so the caller can start the
    real app. Uses a stoppable werkzeug server (not app.run) so we can shut it down cleanly and
    continue in the same process."""
    import webbrowser

    from werkzeug.serving import make_server

    done = threading.Event()
    app = create_setup_app(cfg, done)
    host = cfg["ui"]["host"]
    port = int(cfg["ui"]["port"])
    srv = make_server(host, port, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, name="setup-server", daemon=True)
    t.start()

    url = f"http://{host}:{port}"
    print("\n================ COVAS++ first-run setup ================")
    print(f"  Open {url} to finish setup (it should open automatically).")
    print("  Enter your API keys, download the speech model, pick a mic.")
    print("========================================================\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        done.wait()
    finally:
        srv.shutdown()
        t.join(timeout=5)
    print("Setup complete — starting COVAS++.\n")
