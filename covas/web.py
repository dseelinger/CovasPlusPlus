"""Localhost control panel: live log/status (index.html), a schema-driven
settings page (settings.html), and a WYSIWYG checklist editor (checklist.html,
N10). Served with Flask.

Every settings write — from the quick-config card, the full settings page, or a
future client — is validated against the ONE schema in `settings_schema.py`
before it can reach overrides.json. Unknown keys and out-of-range/invalid values
are rejected loudly (HTTP 400) so unvalidated data never lands on disk (N1).

The checklist editor edits the SAME file the voice loop uses ([checklist].file).
Voice and web stay in sync for free on the read side — `Checklist` re-reads the
file on every call — so "reload on save" only needs the cursor clamped and a log
event published. The write side carries a stale-write guard: every load hands the
client a content-hash `version`, and a save whose `base_version` no longer
matches the file on disk (a voice edit landed meanwhile) is refused with HTTP 409
so the panel can warn instead of clobbering.
"""
from __future__ import annotations
import hashlib
import json
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from . import elevenlabs as el
from . import personality as persona
from . import settings_schema as schema
from . import updates
from .__version__ import __version__
from .checklist import ITEM_RE


def _file_version(path: Path) -> str:
    """Content-hash fingerprint of the checklist file (missing file == empty). Hash of
    CONTENT, not mtime, so a no-op rewrite doesn't false-positive the stale guard."""
    try:
        data = path.read_bytes()
    except OSError:
        data = b""
    return hashlib.sha256(data).hexdigest()[:16]


def _normalize_tasks(markdown: str) -> str:
    """Keep the on-disk task syntax canonical after a WYSIWYG round-trip: editors serialize
    task bullets as `* [ ]`, the file's convention (and the roster the voice model relies on)
    is `- [ ]`. Only lines the checklist parser recognizes as tasks are touched — indentation
    (nesting) and the checkbox state pass through byte-for-byte; every other line (headings,
    notes) is left exactly as the editor wrote it. Ends with one trailing newline, matching
    `Checklist._write`."""
    out = []
    for line in markdown.splitlines():
        m = ITEM_RE.match(line)
        if m and "*" in m.group(1):
            line = f"{m.group(1).replace('*', '-', 1)}[{m.group(2)}] {m.group(3)}"
        out.append(line)
    text = "\n".join(out)
    return text + "\n" if text else ""

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
    "speed": "elevenlabs.speed",
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
            "version": __version__,
            "settings": core.public_settings(),
            "options": {
                "models": core.cfg["anthropic"]["available_models"],
                "thinking": THINKING_TIERS,
                "whisper": WHISPER_SIZES,
            },
            "keys": core.cfg["keys"],
        })

    @flask_app.route("/api/update")
    def update_check():
        """Check GitHub for a newer release. Fail-soft: always 200 with a result dict, so a
        client polling this on load never has to handle an error path. The banner in
        index.html reads `available`/`latest`/`asset_url` from here."""
        return jsonify(updates.check_for_update())

    @flask_app.route("/api/update/apply", methods=["POST"])
    def update_apply():
        """Tier-2 apply (UI-only action, per decision #5): download the new installer and
        launch it, then quit so it can replace files we hold open. The client supplies the
        `asset_url` the check returned. On success we schedule the app's normal quit
        (request_quit → run_covas_ui's quit-watch does shutdown + exit) just after this
        response flushes; the installer takes over from there and preserves %APPDATA% state."""
        b = request.get_json(force=True) or {}
        asset = b.get("asset_url")
        if not asset:
            return jsonify({"ok": False, "error": "no installer asset in the latest release"}), 400
        try:
            updates.download_and_launch_installer(asset)
        except Exception as e:  # noqa: BLE001 — user-initiated: surface the failure, don't quit
            return jsonify({"ok": False, "error": f"update failed: {e}"}), 502
        # Hand off to the installer. Delay the quit so this 200 reaches the browser first.
        threading.Timer(0.5, core.request_quit).start()
        return jsonify({"ok": True})

    @flask_app.route("/api/cues/open", methods=["POST"])
    def cues_open():
        """Open the user's cue override folder (<data_dir>/sounds) in the OS file manager so
        dropping in custom cues is discoverable (I8). Ensures the per-type skeleton first.
        Fail-soft: returns the path either way, and `opened=false` if the OS couldn't open it
        (non-Windows, headless) — the client can then just show the path."""
        import os

        from .audio import cue_roots, ensure_cue_skeleton
        user_base, _ = cue_roots(core.cfg)
        ensure_cue_skeleton(user_base)
        opened = False
        try:
            os.startfile(str(user_base))  # noqa: S606 — Windows-only; opens Explorer on our dir
            opened = True
        except Exception:  # noqa: BLE001 — no file manager / not Windows: fall back to the path
            pass
        return jsonify({"ok": True, "path": str(user_base), "opened": opened})

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

    # ---- Personality tab (N7) -------------------------------------------------
    @flask_app.route("/api/personality")
    def personality_state():
        return jsonify({
            "personas": persona.list_personas(core.cfg),
            "selected": core.cfg.get("personality", {}).get("persona", "Classic"),
            "campaign": persona.read_campaign(core.cfg),
            "enabled": bool(core.cfg.get("personality", {}).get("enabled")),
        })

    @flask_app.route("/api/personality/select", methods=["POST"])
    def personality_select():
        b = request.get_json(force=True) or {}
        name = str(b.get("persona") or "").strip()
        known = {p["name"].strip().lower() for p in persona.list_personas(core.cfg)}
        if not name or name.lower() not in known:
            return jsonify({"ok": False, "error": f"unknown persona {name!r}"}), 400
        core.update_settings({"personality": {"persona": name}})
        return jsonify({"ok": True, "selected": name})

    @flask_app.route("/api/personality/campaign", methods=["POST"])
    def personality_campaign():
        b = request.get_json(force=True) or {}
        persona.save_campaign(core.cfg, str(b.get("campaign") or ""))
        return jsonify({"ok": True})

    @flask_app.route("/api/personality/custom", methods=["POST"])
    def personality_custom():
        b = request.get_json(force=True) or {}
        name = str(b.get("name") or "").strip()
        body = str(b.get("body") or "").strip()
        if not name or not body:
            return jsonify({"ok": False, "error": "name and body are required"}), 400
        saved = persona.save_custom_persona(core.cfg, name, body)
        core.update_settings({"personality": {"persona": saved}})   # select it immediately
        return jsonify({"ok": True, "selected": saved,
                        "personas": persona.list_personas(core.cfg)})

    # ---- Checklist editor (N10) -------------------------------------------------
    def _checklist_path() -> Path | None:
        raw = (core.cfg.get("checklist", {}) or {}).get("file")
        return Path(raw) if raw else None

    @flask_app.route("/checklist")
    def checklist_page():
        return render_template("checklist.html")

    @flask_app.route("/api/checklist")
    def checklist_state():
        path = _checklist_path()
        if path is None:
            return jsonify({"ok": False, "error": "no checklist file configured"}), 400
        try:
            # utf-8-sig: a BOM (a Notepad hand-edit) must not reach the editor — it would
            # render the first heading as literal text and get escaped on the next save.
            markdown = path.read_text(encoding="utf-8-sig")
        except OSError:
            markdown = ""                      # missing file: an empty, saveable checklist
        return jsonify({"ok": True, "markdown": markdown,
                        "version": _file_version(path), "name": path.name})

    @flask_app.route("/api/checklist", methods=["POST"])
    def checklist_save():
        """Save the editor's markdown back to [checklist].file. Refuses (409) when the file
        changed since the client loaded it — a voice edit landed — unless `force` is set;
        the response carries the CURRENT content so the client can offer reload-vs-overwrite
        instead of silently clobbering either side."""
        path = _checklist_path()
        if path is None:
            return jsonify({"ok": False, "error": "no checklist file configured"}), 400
        b = request.get_json(force=True) or {}
        current = _file_version(path)
        if not b.get("force") and b.get("base_version") != current:
            try:
                on_disk = path.read_text(encoding="utf-8-sig")   # BOM-safe, as in the GET
            except OSError:
                on_disk = ""
            return jsonify({"ok": False, "error": "stale",
                            "version": current, "markdown": on_disk}), 409
        text = _normalize_tasks(str(b.get("markdown") or ""))
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            return jsonify({"ok": False, "error": f"write failed: {e}"}), 500
        # "Reload" the voice model: reads are already per-call fresh, so only the in-memory
        # cursor needs care — clamp it so it can't point past the (possibly shorter) new list.
        items = core.checklist.items()
        core.checklist.current = min(core.checklist.current, len(items))
        done = sum(1 for _, d, _ in items if d)
        core.bus.publish({"type": "log", "who": "system",
                          "text": f"Checklist updated from the web editor "
                                  f"({done}/{len(items)} complete)."})
        return jsonify({"ok": True, "version": _file_version(path),
                        "items": len(items), "done": done})

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
