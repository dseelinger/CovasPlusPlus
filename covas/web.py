"""Localhost control panel: live log/status (index.html), a schema-driven
settings page (settings.html), a WYSIWYG checklist editor (checklist.html,
N10), and a memory browser (memory.html, issue #62). Served with Flask.

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

from . import catalog
from . import crew as crew_mod
from . import elevenlabs as el
from . import firstrun
from . import personality as persona
from . import settings_schema as schema
from . import updates
from .__version__ import __version__
from .checklist import ITEM_RE, checklist_event
from .memory.store import MemoryRecord, MemoryStore, store_from_config
from .macros.store import store_from_config as macros_store_from_config

# Config sections whose `api_key_file` the masked "API keys" Settings card manages (issue #23),
# in display order. The card is write-only: keys are stored ENCRYPTED per section and never read
# back to the client — only a set/not-set boolean per section is exposed.
_KEY_SECTIONS = ("anthropic", "elevenlabs", "openai", "gemini", "azure", "cartesia", "cg")
_KEY_SECTION_SET = frozenset(_KEY_SECTIONS)


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
    # Voice speed is the ONE normalized, provider-agnostic control now (issue #99), not per-provider.
    "speed": "tts.speed",
}

_EL_SOURCES = (schema.OPT_EL_MODELS, schema.OPT_EL_VOICES)

# Fetched-catalog sources the /api/catalog endpoint may resolve (issue #92 / #88). Only these are
# accepted, so the endpoint can't be pointed at an arbitrary string.
_CATALOG_SOURCES = frozenset({
    schema.OPT_OPENAI_MODELS, schema.OPT_GEMINI_MODELS, schema.OPT_OLLAMA_MODELS,
    schema.OPT_ANTHROPIC_MODELS_LIVE, schema.OPT_OPENAI_BASE_URLS,
    schema.OPT_EDGE_VOICES, schema.OPT_AZURE_VOICES, schema.OPT_CARTESIA_VOICES,
    schema.OPT_INPUT_DEVICES,  # mic picker (#89) — resolved from the local device list
})
# Short throttle so repeated dropdown opens don't hammer a provider (mirrors the ElevenLabs pattern).
_CATALOG_TTL_S = 60.0


def create_app(core) -> Flask:
    flask_app = Flask(__name__, template_folder="templates", static_folder="static")
    sock = Sock(flask_app)
    _catalog_cache: dict = {}  # (source, base_url) -> (expires_at, options, error)

    # Signal the core that the control panel (this Flask server) exists, so a web HUD (#103)
    # enabled before the server came up can attach now that /hud is actually served. Guarded so a
    # stub core in tests that doesn't implement it is fine.
    _note_ui = getattr(core, "note_web_ui_started", None)
    if callable(_note_ui):
        _note_ui()

    @flask_app.context_processor
    def _inject_theme() -> dict:
        """Stamp the active control-panel theme (ui.theme) into EVERY rendered template so the
        <html data-theme="…"> is correct on first paint — no flash of the wrong palette (issue
        #104). Defaults to "dark" when unset. Read live from cfg so a theme switch (applied via
        the settings path) shows on the next navigation/restart with no flash."""
        return {"theme": core.cfg.get("ui", {}).get("theme", "dark")}

    def _catalog_cached(source: str, base_url):
        """Resolve a catalog source through `catalog.resolve`, throttled by _CATALOG_TTL_S so
        reopening a dropdown doesn't re-hit the provider. Fail-soft: returns (options, error)."""
        import time
        ck = (source, base_url or "")
        hit = _catalog_cache.get(ck)
        if hit and hit[0] > time.monotonic():
            return hit[1], hit[2]
        opts, err = catalog.resolve(source, core.cfg, base_url=base_url)
        _catalog_cache[ck] = (time.monotonic() + _CATALOG_TTL_S, opts, err)
        return opts, err

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
        # The LLM/Speech quick controls now come provider-shaped inside `settings` (issue #86) and
        # carry their own resolved options; only whisper stays a flat top-level option list here.
        return jsonify({
            "status": core.state,
            "version": __version__,
            "settings": core.public_settings(),
            "options": {
                "whisper": WHISPER_SIZES,
            },
            "keys": core.cfg["keys"],
        })

    @flask_app.route("/hud")
    def hud_page():
        """The transparent web HUD (issue #103). Served for OpenKneeboard's Web Dashboard tab so
        the companion HUD composites in-headset on ANY OpenXR runtime (OpenComposite / VDXR /
        Virtual Desktop), where a separate-process SteamVR overlay structurally can't. The page is
        transparent by construction and polls /api/hud; it renders empty when the HUD is off."""
        return render_template("hud.html")

    @flask_app.route("/api/hud")
    def hud_state():
        """Live HUD snapshot as JSON for the /hud page. FAIL SOFT — never 500: no HUD capability
        (wiring failed at startup), the web surface being off, or the model raising all collapse to
        an "off" payload, so the page renders empty and nothing floats in the cockpit."""
        try:
            hud = getattr(core, "hud", None)
            enabled = bool(core.cfg.get("hud", {}).get("web_enabled", False))
            if hud is None or not enabled:
                return jsonify({"enabled": False})
            snap = hud.model.snapshot()
            return jsonify({
                "enabled": True,
                "voice_state": snap.voice_state,
                "checklist": snap.checklist,
                "route": snap.route,
                "callout": snap.callout,
            })
        except Exception:  # noqa: BLE001 — a snapshot/config glitch blanks the page, never 500s
            return jsonify({"enabled": False})

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

    @flask_app.route("/api/cues/reload", methods=["POST"])
    def cues_reload():
        """Re-scan the cue folders and hot-swap the preloaded set — no restart (issue #109).
        The open→drop-files→reload flow this mirrors: `cues_open` above surfaces the folder;
        this re-reads it. Fail-soft: `CuePlayer.reload()` never raises (a bad/missing file is
        skipped), and if the audio layer never came up (`core.cues` absent) we still return 200
        with all-zero counts rather than 500."""
        counts = core.cues.reload() if getattr(core, "cues", None) is not None else {}
        return jsonify({"ok": True, "counts": counts})

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

    @flask_app.route("/api/catalog")
    def catalog_opts():
        """Resolve ONE fetched-catalog options_source (issue #92 / #88) for the editable-combobox
        dropdowns. `?source=@openai_models[&base_url=…]`. ALWAYS 200 + `{options, error}`: on failure
        (offline / no key / unreachable) `options` is `[]` and `error` names why, so the page degrades
        to free-text with the current value kept — never an empty blocking control. Results are cached
        briefly (throttle) since these are network calls, some key-gated."""
        source = (request.args.get("source") or "").strip()
        base_url = (request.args.get("base_url") or "").strip() or None
        if source not in _CATALOG_SOURCES:
            return jsonify({"options": [], "error": f"unknown source {source!r}"}), 400
        opts, err = _catalog_cached(source, base_url)
        return jsonify({"options": opts or [], "error": err})

    def _key_flags() -> dict:
        """Set/not-set boolean per managed provider section — the ONLY key info the client sees."""
        return {s: firstrun.key_available(core.cfg, s) for s in _KEY_SECTIONS}

    @flask_app.route("/api/keys")
    def keys_state():
        """Per-provider set/not-set flags for the masked "API keys" card (issue #23). BOOLEANS
        ONLY — never the key material — so the page renders badges without exposing secrets."""
        return jsonify({"keys": _key_flags()})

    @flask_app.route("/api/keys", methods=["POST"])
    def keys_save():
        """Rotate or clear ONE provider's key. `{section, value}` writes the encrypted key; a blank
        value is a NO-OP (never clobbers a stored key), and `{section, clear: true}` explicitly
        removes it. Returns the refreshed flags. Write-only: no stored key is ever sent back."""
        b = request.get_json(force=True) or {}
        section = str(b.get("section") or "")
        if section not in _KEY_SECTION_SET:
            return jsonify({"ok": False, "error": f"unknown key section {section!r}"}), 400
        try:
            if b.get("clear"):
                firstrun.clear_key(core.cfg, section)
            else:
                value = str(b.get("value") or "").strip()
                if value:
                    firstrun.save_key(core.cfg, section, value)
        except Exception as e:  # noqa: BLE001 — surface a write failure, don't crash the panel
            return jsonify({"ok": False, "error": str(e)}), 500
        return jsonify({"ok": True, "keys": _key_flags()})

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
        # Same `checklist` event the voice/tool path fires, so OTHER open Checklist tabs
        # re-render in place too (this tab ignores its own version echo) — one writer path (#82).
        core.bus.publish(checklist_event(core.checklist))
        return jsonify({"ok": True, "version": _file_version(path),
                        "items": len(items), "done": done})

    # ---- Memory browser (issue #62) --------------------------------------------
    # The transparent, user-editable ship's log competitors don't offer: read / search / edit /
    # delete / add memories from the panel. It shares the SAME physical JSONL file the voice path
    # writes to — the content-hash stale-write guard (mirroring the checklist editor) is what makes
    # that concurrency safe: a web save is a whole-file `store.save`, refused (409) when a voice
    # append/prune landed since the client loaded. When memory is ON we reach the app's live store
    # instance (so mutating it keeps the in-memory list authoritative — a later voice prune can't
    # clobber a web edit); otherwise we build one from config pointing at the same file.
    def _memory_store() -> MemoryStore | None:
        mem = getattr(core, "memory", None)
        if mem is not None:
            return mem.store
        try:
            return store_from_config(core.cfg)
        except Exception:  # noqa: BLE001 — no resolvable memory dir: browser simply unavailable
            return None

    def _memory_matches(record: MemoryRecord, q: str) -> bool:
        """Case-insensitive substring match across a record's text, type, and tags — the same
        search the browser runs client-side, offered server-side for API clients (and tests)."""
        hay = " ".join((record.text, record.type, " ".join(record.tags))).lower()
        return q in hay

    def _memory_snapshot(store: MemoryStore, q: str = "") -> dict:
        """Re-read the file into the (possibly shared) store, then render the list + version.
        Reloading syncs the shared in-memory list to disk so the content-hash `version` and the
        returned records always describe the same on-disk state (picks up voice appends and any
        hand-edits to the file). `version` and `total` always describe the WHOLE file, so a
        filtered read still carries the correct stale-write token to write back with."""
        records = store.load()
        shown = [r for r in records if not q or _memory_matches(r, q)]
        return {"memories": [r.to_dict() for r in shown], "total": len(records),
                "version": _file_version(store.path), "name": store.path.name}

    @flask_app.route("/memory")
    def memory_page():
        return render_template("memory.html")

    @flask_app.route("/api/memory")
    def memory_state():
        store = _memory_store()
        if store is None:
            return jsonify({"ok": False, "error": "no memory store configured"}), 400
        q = str(request.args.get("q") or "").strip().lower()
        return jsonify({"ok": True, **_memory_snapshot(store, q)})

    def _memory_guard(store: MemoryStore, b: dict):
        """Shared stale-write guard for every mutation. Returns a Flask 409 response to bail with
        when the file changed since the client loaded it (unless `force`), else None to proceed."""
        current = _file_version(store.path)
        if not b.get("force") and b.get("base_version") != current:
            return jsonify({"ok": False, "error": "stale", **_memory_snapshot(store)}), 409
        return None

    def _memory_saved(store: MemoryStore, records: list[MemoryRecord]):
        """Persist the mutated list (one whole-file atomic rewrite, shared with the voice path),
        log the sync, and hand back the refreshed snapshot with the new stale-write token."""
        store.save(records)
        core.bus.publish({"type": "log", "who": "system",
                          "text": f"Memory updated from the web browser "
                                  f"({len(records)} on file)."})
        return jsonify({"ok": True, **_memory_snapshot(store)})

    @flask_app.route("/api/memory/add", methods=["POST"])
    def memory_add():
        """Add one memory by hand. Guarded like a save so a concurrent voice append isn't lost."""
        store = _memory_store()
        if store is None:
            return jsonify({"ok": False, "error": "no memory store configured"}), 400
        b = request.get_json(force=True) or {}
        stale = _memory_guard(store, b)
        if stale is not None:
            return stale
        text = str(b.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "a memory needs some text"}), 400
        records = store.load()                     # fresh, file-synced list to append to
        records.append(MemoryRecord(text=text, type=str(b.get("type") or "note") or "note",
                                    tags=b.get("tags") or ()))
        return _memory_saved(store, records)

    @flask_app.route("/api/memory/edit", methods=["POST"])
    def memory_edit():
        """Edit one memory's text / type / tags BY id. `id`, `when`, and creation order are kept;
        only the edited fields change, round-tripping losslessly through the store."""
        store = _memory_store()
        if store is None:
            return jsonify({"ok": False, "error": "no memory store configured"}), 400
        b = request.get_json(force=True) or {}
        stale = _memory_guard(store, b)
        if stale is not None:
            return stale
        rec_id = str(b.get("id") or "")
        text = str(b.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "a memory needs some text"}), 400
        records = store.load()
        for i, r in enumerate(records):
            if r.id == rec_id:
                # Rebuild (not mutate) so tags re-normalize; preserve id + original timestamp.
                records[i] = MemoryRecord(text=text,
                                          type=str(b.get("type") or r.type) or "note",
                                          tags=b.get("tags") or (), when=r.when, id=r.id)
                return _memory_saved(store, records)
        return jsonify({"ok": False, "error": f"no memory with id {rec_id!r}"}), 404

    @flask_app.route("/api/memory/delete", methods=["POST"])
    def memory_delete():
        """Delete one memory BY id (whole-file rewrite)."""
        store = _memory_store()
        if store is None:
            return jsonify({"ok": False, "error": "no memory store configured"}), 400
        b = request.get_json(force=True) or {}
        stale = _memory_guard(store, b)
        if stale is not None:
            return stale
        rec_id = str(b.get("id") or "")
        records = store.load()
        kept = [r for r in records if r.id != rec_id]
        if len(kept) == len(records):
            return jsonify({"ok": False, "error": f"no memory with id {rec_id!r}"}), 404
        return _memory_saved(store, kept)

    # ---- Crew editor (issue #70) -----------------------------------------------
    # Define the crew CAST: name, personality, and (optional) explicit voice per character. It
    # edits the SAME JSON roster file ([crew].file) the voice/prompt paths read live — personas
    # fold into the static system instruction, a non-blank voice_ref overrides the deterministic
    # auto-assign. A whole-roster save (the cast is small) guarded by the same content-hash
    # stale-write token as the checklist/memory editors, so a hand-edit to the file isn't clobbered.
    def _crew_path() -> Path | None:
        return crew_mod.roster_file(core.cfg)

    def _crew_voices() -> list[dict]:
        """Voice options for the per-character dropdown — the configured cast POOL plus the persona
        voice, each `{ref, label}`. A blank ref (offered as "Auto" in the UI) keeps the deterministic
        assignment. Offline: reads [audio.voices] from config, never hits the network."""
        try:
            from .mixer.voices import build_cast
            cast = build_cast(core.cfg)
        except Exception:  # noqa: BLE001 — no resolvable cast -> only the Auto option in the UI
            return []
        persona = cast.persona()
        out: list[dict] = []
        seen: set[str] = set()
        for v in [persona, *cast.pool]:
            ref = str(v.ref or "").strip()
            if not ref or ref in seen:
                continue
            seen.add(ref)
            role = "persona" if ref == persona.ref else v.gender
            out.append({"ref": ref, "label": f"{v.provider}:{ref} ({role})"})
        return out

    def _crew_snapshot() -> dict:
        """The roster + voice options + version, re-read from the shared file so the content-hash
        `version` and the returned members always describe the same on-disk state."""
        path = _crew_path()
        members = crew_mod.load_members(core.cfg)
        return {"members": [m.to_dict() for m in members], "voices": _crew_voices(),
                "enabled": crew_mod.is_enabled(core.cfg),
                "version": _file_version(path) if path else "",
                "name": path.name if path else ""}

    @flask_app.route("/crew")
    def crew_page():
        return render_template("crew.html")

    @flask_app.route("/api/crew")
    def crew_state():
        if _crew_path() is None:
            return jsonify({"ok": False, "error": "no crew file configured"}), 400
        return jsonify({"ok": True, **_crew_snapshot()})

    @flask_app.route("/api/crew", methods=["POST"])
    def crew_save():
        """Save the whole roster back to [crew].file. Refuses (409) when the file changed since the
        client loaded it (a hand-edit / voice write landed) unless `force` is set — the response
        carries the current roster so the client can reload-vs-overwrite instead of clobbering."""
        path = _crew_path()
        if path is None:
            return jsonify({"ok": False, "error": "no crew file configured"}), 400
        b = request.get_json(force=True) or {}
        current = _file_version(path)
        if not b.get("force") and b.get("base_version") != current:
            return jsonify({"ok": False, "error": "stale", **_crew_snapshot()}), 409
        raw = b.get("members")
        if not isinstance(raw, list):
            return jsonify({"ok": False, "error": "members must be a list"}), 400
        members = [m for m in (crew_mod.CrewMember.from_obj(o) for o in raw) if m is not None]
        crew_mod.save_members(path, members)
        core.bus.publish({"type": "log", "who": "system",
                          "text": f"Crew roster updated from the web editor "
                                  f"({len(members)} character(s))."})
        return jsonify({"ok": True, **_crew_snapshot()})

    # ---- Custom macros browser (issue #50) -------------------------------------
    # View / author / delete the Commander's own named macros, mirroring the checklist/memory
    # editors. Authoring here runs the SAME registry validator (`compile_macro`) as voice
    # authoring, so a web-created macro is anti-hallucination-checked identically: an action not
    # in [keybinds].allowlist, or an unknown status/trigger, is a templated 400 and nothing is
    # saved. When macros are ON we reach the app's live store; otherwise we build one from config
    # pointing at the same file, so the panel works before the feature is enabled.
    def _macro_store():
        mac = getattr(core, "macros", None)
        if mac is not None:
            return mac._store
        try:
            return macros_store_from_config(core.cfg)
        except Exception:  # noqa: BLE001 — no resolvable file: browser simply unavailable
            return None

    def _macro_actions_and_allow():
        """The action registry + current [keybinds].allowlist the validator (and the form's
        option lists) use — read live from config so a changed allowlist is reflected."""
        from .capabilities.keybind_capability import KeybindConfig
        from .keybinds import actions as _kb_actions  # noqa: F401 — populates the registry
        from .keybinds.registry import registered_macros
        allow = frozenset(KeybindConfig.from_cfg(core.cfg).allowlist)
        return registered_macros(), allow

    def _macro_vocab():
        """The closed vocabularies the authoring form offers (allowlisted actions, status keys,
        triggers) — same sets the validator enforces, so the form can't suggest an invalid one."""
        from .macros.registry import STATUS_CONDITIONS, TRIGGERS
        actions, allow = _macro_actions_and_allow()
        return {
            "actions": sorted(allow & set(actions)),
            "statuses": [{"key": k, "label": c.label} for k, c in STATUS_CONDITIONS.items()],
            "triggers": [{"id": t.id, "when": t.when} for t in TRIGGERS.values()],
        }

    @flask_app.route("/macros")
    def macros_page():
        return render_template("macros.html")

    @flask_app.route("/api/macros")
    def macros_state():
        store = _macro_store()
        if store is None:
            return jsonify({"ok": False, "error": "no macros file configured"}), 400
        specs = store.load()
        return jsonify({"ok": True, "macros": [s.to_dict() for s in specs],
                        "vocab": _macro_vocab(), "name": store.path.name})

    @flask_app.route("/api/macros/create", methods=["POST"])
    def macros_create():
        """Validate a macro against the registry and persist it. Reuses the exact voice-authoring
        path (`_spec_from_input` -> `compile_macro`) so web and voice share one validator."""
        store = _macro_store()
        if store is None:
            return jsonify({"ok": False, "error": "no macros file configured"}), 400
        from .capabilities.macro_capability import _spec_from_input
        from .macros.compile import MacroValidationError, compile_macro
        b = request.get_json(force=True) or {}
        try:
            spec = _spec_from_input(b)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        actions, allow = _macro_actions_and_allow()
        try:
            compile_macro(spec, actions=actions, allowlist=allow)   # anti-hallucination gate
        except MacroValidationError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        store.add(spec)
        core.bus.publish({"type": "log", "who": "system",
                          "text": f"Custom macro '{spec.name}' saved from the web editor."})
        return jsonify({"ok": True, "macros": [s.to_dict() for s in store.all()]})

    @flask_app.route("/api/macros/delete", methods=["POST"])
    def macros_delete():
        store = _macro_store()
        if store is None:
            return jsonify({"ok": False, "error": "no macros file configured"}), 400
        b = request.get_json(force=True) or {}
        name = str(b.get("name") or "")
        if not store.delete(name):
            return jsonify({"ok": False, "error": f"no macro called {name!r}"}), 404
        core.bus.publish({"type": "log", "who": "system",
                          "text": f"Custom macro '{name}' deleted from the web editor."})
        return jsonify({"ok": True, "macros": [s.to_dict() for s in store.all()]})

    @flask_app.route("/api/prompt", methods=["POST"])
    def prompt():
        """Type a prompt to the AI from the control panel (issue #76). Runs a FULL normal turn
        (router tiering, ED/memory context, tools, spoken reply) — just skips STT. Empty/whitespace
        is rejected, matching the transcription guard."""
        b = request.get_json(force=True) or {}
        text = str(b.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "empty prompt"}), 400
        core.dispatch_text(text)
        return jsonify({"ok": True})

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
