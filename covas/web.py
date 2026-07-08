"""Localhost control panel: settings, live log, status — served with Flask."""
from __future__ import annotations
import json

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from . import elevenlabs as el

THINKING_TIERS = ["Off", "Low", "Medium", "High", "Extra", "Max"]
WHISPER_SIZES = ["tiny", "base", "small", "medium", "large-v3"]


def create_app(core) -> Flask:
    flask_app = Flask(__name__, template_folder="templates", static_folder="static")
    sock = Sock(flask_app)

    @flask_app.route("/")
    def index():
        return render_template("index.html")

    @flask_app.route("/api/state")
    def state():
        return jsonify({
            "status": core.state,
            "settings": core.public_settings(),
            "options": {
                "models": core.cfg["anthropic"]["available_models"],
                "thinking": THINKING_TIERS,
                "whisper": WHISPER_SIZES,
            },
            "keys": core.cfg["keys"],
        })

    @flask_app.route("/api/elevenlabs")
    def elevenlabs_opts():
        try:
            return jsonify({
                "voices": el.list_voices(core.cfg),
                "models": el.list_models(core.cfg),
            })
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 502

    @flask_app.route("/api/settings", methods=["POST"])
    def settings():
        b = request.get_json(force=True) or {}
        patch: dict = {}

        def sec(name: str) -> dict:
            return patch.setdefault(name, {})

        if "model" in b:
            sec("anthropic")["model"] = b["model"]
        if "thinking" in b:
            sec("anthropic").setdefault("thinking", {})["default"] = b["thinking"]
        if "web_search" in b:
            sec("web_search")["enabled"] = bool(b["web_search"])
        if "personality" in b:
            sec("personality")["enabled"] = bool(b["personality"])
        if "el_model" in b:
            sec("elevenlabs")["model"] = b["el_model"]
        if "el_voice" in b:
            sec("elevenlabs")["voice_id"] = b["el_voice"]
            if b.get("el_voice_name"):
                sec("elevenlabs")["voice_name"] = b["el_voice_name"]
        if "whisper" in b:
            sec("whisper")["model"] = b["whisper"]

        if patch:
            core.update_settings(patch)
        return jsonify({"ok": True, "settings": core.public_settings()})

    @flask_app.route("/api/cancel", methods=["POST"])
    def cancel():
        core.trigger_cancel()
        return jsonify({"ok": True})

    @sock.route("/ws")
    def ws(ws):  # noqa: ANN001
        q = core.bus.subscribe()
        try:
            # prime the client with the current status
            ws.send(json.dumps({"type": "status", "state": core.state, "extra": ""}))
            while True:
                event = q.get()
                ws.send(json.dumps(event))
        except Exception:  # noqa: BLE001 — client disconnected
            pass
        finally:
            core.bus.unsubscribe(q)

    return flask_app
