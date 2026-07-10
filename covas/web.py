"""Localhost control panel: live log/status (index.html) + a schema-driven
settings page (settings.html). Served with Flask.

Every settings write — from the quick-config card, the full settings page, or a
future client — is validated against the ONE schema in `settings_schema.py`
before it can reach overrides.json. Unknown keys and out-of-range/invalid values
are rejected loudly (HTTP 400) so unvalidated data never lands on disk (N1).
"""
from __future__ import annotations
import json

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from . import elevenlabs as el
from . import settings_schema as schema

THINKING_TIERS = schema.THINKING_TIERS
WHISPER_SIZES = schema.WHISPER_SIZES

# Friendly keys the legacy quick-config card (index.html) posts, mapped to their
# canonical schema keys so those writes are validated through the same path.
_LEGACY_MAP = {
    "model": "anthropic.model",
    "thinking": "anthropic.thinking.default",
    "web_search": "web_search.enabled",
    "personality": "personality.enabled",
    "el_model": "elevenlabs.model",
    "el_voice": "elevenlabs.voice_id",
    "el_voice_name": "elevenlabs.voice_name",
    "whisper": "whisper.model",
}

_EL_SOURCES = (schema.OPT_EL_MODELS, schema.OPT_EL_VOICES)


def create_app(core) -> Flask:
    flask_app = Flask(__name__, template_folder="templates", static_folder="static")
    sock = Sock(flask_app)

    def _dynamic_options(keys) -> dict:
        """Resolve enum options that aren't statically known. Models come from
        config (free); ElevenLabs lists are fetched only when an EL setting is
        actually in play, and best-effort — offline, they're simply omitted and
        validation falls back to a type check rather than rejecting."""
        dyn = {schema.OPT_MODELS: core.cfg["anthropic"]["available_models"]}
        need_el = any(
            (schema.by_key.get(k).options_source in _EL_SOURCES)
            for k in keys if schema.by_key.get(k) is not None
        )
        if need_el:
            try:
                dyn[schema.OPT_EL_MODELS] = [m["model_id"] for m in el.list_models(core.cfg)]
                dyn[schema.OPT_EL_VOICES] = [v["voice_id"] for v in el.list_voices(core.cfg)]
            except Exception:  # noqa: BLE001 — offline/API failure: leave EL unresolved
                pass
        return dyn

    def _validate(updates: dict) -> tuple[dict, dict]:
        """Validate a {key: value} map against the schema. Returns (patch,
        errors); the patch is built in full and applied by the caller ONLY when
        errors is empty, so a single bad field aborts the whole write."""
        dyn = _dynamic_options(list(updates.keys()))
        patch: dict = {}
        errors: dict = {}
        for key, value in updates.items():
            s = schema.by_key.get(key)
            if s is None:
                errors[key] = "unknown setting"
                continue
            coerced, err = schema.validate_value(
                s, value, options=schema.resolve_options(s, dyn))
            if err:
                errors[key] = err
            else:
                schema.set_value(patch, s, coerced)
        return patch, errors

    @flask_app.route("/")
    def index():
        return render_template("index.html")

    @flask_app.route("/settings")
    def settings_page():
        return render_template("settings.html")

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

    @flask_app.route("/api/schema")
    def api_schema():
        # Models are resolved server-side (cheap, from config). ElevenLabs voice/
        # model lists are left unresolved here and filled client-side via
        # /api/elevenlabs, so this endpoint stays fast and works offline.
        dyn = {schema.OPT_MODELS: core.cfg["anthropic"]["available_models"]}
        return jsonify({
            "groups": schema.public_schema(core.cfg, core.overrides, dyn),
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
        """Legacy quick-config endpoint (index.html). Same schema validation."""
        b = request.get_json(force=True) or {}
        updates = {}
        for friendly, key in _LEGACY_MAP.items():
            if friendly in b:
                updates[key] = b[friendly]
        patch, errors = _validate(updates)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        if patch:
            core.update_settings(patch)
        return jsonify({"ok": True, "settings": core.public_settings()})

    @flask_app.route("/api/settings/update", methods=["POST"])
    def settings_update():
        """Schema-driven write from the settings page. Accepts either a batch
        {"updates": {key: value, ...}} or a single {"key":..., "value":...}."""
        b = request.get_json(force=True) or {}
        updates = b.get("updates")
        if updates is None and "key" in b:
            updates = {b["key"]: b.get("value")}
        updates = updates or {}
        patch, errors = _validate(updates)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        if patch:
            core.update_settings(patch)
        return jsonify({"ok": True, "groups": schema.public_schema(
            core.cfg, core.overrides,
            {schema.OPT_MODELS: core.cfg["anthropic"]["available_models"]})})

    @flask_app.route("/api/settings/reset", methods=["POST"])
    def settings_reset():
        """Reset one setting to its config.toml default (drops it from overrides)."""
        b = request.get_json(force=True) or {}
        key = b.get("key")
        s = schema.by_key.get(key)
        if s is None:
            return jsonify({"ok": False, "errors": {key: "unknown setting"}}), 400
        core.reset_setting(s.path)
        return jsonify({"ok": True, "groups": schema.public_schema(
            core.cfg, core.overrides,
            {schema.OPT_MODELS: core.cfg["anthropic"]["available_models"]})})

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
