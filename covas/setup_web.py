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
from . import settings_schema as schema

# The finish-step copy differs by HOW the wizard is being shown, because the safe next action
# differs. In the NATIVE single window (run_covas_app.py) the handoff swaps this SAME window over
# to the control panel automatically — telling the user to "close this tab" there would make them
# QUIT the app (closing the only window ends the process). In a BROWSER tab (run_covas_ui.py) the
# panel comes up separately, so closing the leftover setup tab is exactly right.
_FINISH_MSG_NATIVE = "Setup complete — starting COVAS++. Switching to the control panel…"
_FINISH_MSG_BROWSER = "Setup complete — COVAS++ is starting. You can close this tab."


def create_setup_app(cfg: dict, done: threading.Event, *, native: bool = False) -> Flask:
    """Build the wizard app. `cfg` is mutated in place as steps write overrides (so status
    reads stay current); `done` is set when setup finishes so the caller can stop serving.
    `native` tailors the finish copy to the single-window native flow vs a browser tab (see
    the message constants above)."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    finish_message = _FINISH_MSG_NATIVE if native else _FINISH_MSG_BROWSER

    # Coarse STT-download state shared with the status poll. Real byte-progress from
    # huggingface_hub is awkward to capture reliably, so we report a state, not a percent —
    # the template shows a "downloading ~250 MB, one time" spinner. state ∈ idle|downloading|
    # ready|error.
    dl = {"state": "idle", "error": None}
    dl_lock = threading.Lock()

    @app.route("/")
    @app.route("/setup")
    def setup_page():
        return render_template("setup.html", finish_message=finish_message)

    @app.route("/api/setup/status")
    def status():
        st = firstrun.configured_status(cfg)
        with dl_lock:
            st["download"] = dict(dl)
        return jsonify(st)

    # Sections whose key the wizard may save, plus the optional non-key provider fields it persists
    # to overrides (endpoint/model/host). Any TTS voice fields are handled by /api/setup/voice.
    _KEY_SECTIONS = ("anthropic", "openai", "gemini", "elevenlabs", "azure", "cartesia")

    @app.route("/api/setup/keys", methods=["POST"])
    def save_keys():
        """Persist the wizard's provider choices and whichever keys/fields were supplied (issue #87).
        The user picks ANY supported LLM + TTS combo — not Anthropic-only — so this writes:
          * `llm_provider` / `tts_provider` selection to overrides,
          * each supplied per-section key (blank strings ignored, so a blank box never clobbers), and
          * the optional non-key provider fields the chosen LLM needs (OpenAI base_url + model,
            Gemini model, Ollama host + model).
        Whether that's ENOUGH to finish is decided provider-aware by `is_configured`, not here."""
        b = request.get_json(force=True) or {}

        # 1) provider selection (validated against the schema vocabularies).
        llm = str(b.get("llm_provider") or "").strip().lower()
        tts = str(b.get("tts_provider") or "").strip().lower()
        try:
            if llm and llm in schema.LLM_PROVIDERS:
                firstrun.apply_override(cfg, {"llm": {"provider": llm}})
            if tts and tts in schema.TTS_PROVIDERS:
                firstrun.apply_override(cfg, {"tts": {"provider": tts}})

            # 2) keys — only non-blank values, keyed by config section.
            keys = b.get("keys") or {}
            for section in _KEY_SECTIONS:
                val = str(keys.get(section) or "").strip()
                if val:
                    firstrun.save_key(cfg, section, val)

            # 3) optional non-key provider fields (persist only what's supplied).
            for field, path in (
                ("openai_base_url", ("openai", "base_url")),
                ("openai_model", ("openai", "model")),
                ("gemini_model", ("gemini", "model")),
                ("ollama_host", ("ollama", "host")),
                ("ollama_model", ("ollama", "model")),
            ):
                v = str(b.get(field) or "").strip()
                if v:
                    firstrun.apply_override(cfg, {path[0]: {path[1]: v}})
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
        """Set the default voice for the CHOSEN TTS provider (issue #87 — no longer ElevenLabs-only):
          * edge / piper  — FREE, no key, no fetch: persist the supplied voice/model field (or keep
            the config default) and report the active voice — a keyless install still gets a voice.
          * elevenlabs    — the original flow: needs the key, resolves "George" (else the first
            voice); with no key it's skipped (text-only), reported so.
          * azure / openai / cartesia — persist the supplied voice field (+ region/model); the key
            these need to actually speak is collected on the keys step.
        Every branch is fail-soft and reports what it did so the wizard can badge it."""
        b = request.get_json(force=True, silent=True) or {}   # empty body is fine (voice is optional)
        provider = str(cfg.get("tts", {}).get("provider", "edge")).lower()

        if provider in ("edge", "piper"):
            field = "voice" if provider == "edge" else "model"
            supplied = str(b.get(field) or "").strip()
            if supplied:
                firstrun.apply_override(cfg, {provider: {field: supplied}})
            current = str((cfg.get(provider, {}) or {}).get(field) or "")
            return jsonify({"ok": True, "provider": provider,
                            "voice": {"name": current or "(default)"}})

        if provider == "elevenlabs":
            if not firstrun.elevenlabs_key_available(cfg):
                return jsonify({"ok": True, "skipped": True,
                                "reason": "no ElevenLabs key (text-only)"})
            try:
                voices = el.list_voices(cfg)
            except Exception as e:  # noqa: BLE001 — bad key / offline: let the wizard show it
                return jsonify({"ok": False, "error": str(e)}), 502
            chosen = firstrun.resolve_default_voice(voices)
            if chosen is None:
                return jsonify({"ok": False, "error": "your ElevenLabs account has no voices"}), 502
            firstrun.apply_override(cfg, {"elevenlabs": {
                "voice_id": chosen["voice_id"], "voice_name": chosen["name"]}})
            return jsonify({"ok": True, "provider": provider, "voice": chosen})

        # azure / openai (TTS) / cartesia: persist the voice field the user typed/picked. The config
        # section for OpenAI TTS is [openai_tts]; the others match the provider name.
        section = "openai_tts" if provider == "openai" else provider
        supplied = str(b.get("voice") or "").strip()
        if supplied:
            firstrun.apply_override(cfg, {section: {"voice": supplied}})
        if provider == "azure":
            region = str(b.get("region") or "").strip()
            if region:
                firstrun.apply_override(cfg, {"azure": {"region": region}})
        current = str((cfg.get(section, {}) or {}).get("voice") or "")
        return jsonify({"ok": True, "provider": provider,
                        "voice": {"name": current or "(default)"}})

    @app.route("/api/setup/finish", methods=["POST"])
    def finish():
        """Complete setup — but only if the required pieces are actually in place, so the
        wizard can't hand a half-configured app to the panel. Sets the done event the server
        loop waits on."""
        if not firstrun.is_configured(cfg):
            st = firstrun.configured_status(cfg)
            need = []
            if not st["llm"]:
                need.append(f"the {st['llm_provider']} LLM "
                            + ("model (pull it in Ollama)" if st["llm_provider"] == "ollama"
                               else "key"))
            if not st["stt"]:
                need.append("the speech-to-text model")
            return jsonify({"ok": False, "status": st,
                            "error": "not finished: need " + " and ".join(need or ["setup"])}), 400
        done.set()
        return jsonify({"ok": True})

    return app


def start_setup_server(cfg: dict, *, native: bool = False):
    """Start the wizard on a stoppable werkzeug server WITHOUT opening a browser or blocking.
    Returns `(srv, thread, done)`: the caller owns the lifecycle — it decides how the wizard is
    shown (a browser tab or a native PyWebView window pointed at the URL) and, once `done` is
    set, stops the server with `srv.shutdown(); thread.join()`.

    This is the seam the native-window entry (run_covas_app.py, I9) uses to render the wizard in
    the SAME window that later becomes the control panel — no browser step. `run_first_run` is the
    blocking, browser-owning convenience wrapper built on top of it (the run_covas_ui.py path)."""
    from werkzeug.serving import make_server

    done = threading.Event()
    app = create_setup_app(cfg, done, native=native)
    host = cfg["ui"]["host"]
    port = int(cfg["ui"]["port"])
    srv = make_server(host, port, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, name="setup-server", daemon=True)
    t.start()
    return srv, t, done


def run_first_run(cfg: dict) -> None:
    """Serve the wizard and block until it's finished, then return so the caller can start the
    real app. Opens the wizard in the default browser (the run_covas_ui.py path); the native
    entry uses `start_setup_server` directly and drives its own window instead."""
    import webbrowser

    srv, t, done = start_setup_server(cfg)
    host = cfg["ui"]["host"]
    port = int(cfg["ui"]["port"])
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
