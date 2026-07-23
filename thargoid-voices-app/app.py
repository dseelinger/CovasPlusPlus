"""Thargoid Voices — standalone local generator/curator web app.

Launch with ``python app.py`` and open http://127.0.0.1:5005/ . Generate Thargoid
"voice" SFX server-side, audition them in the browser, and save the keepers into a
target folder of your choosing. Binds to localhost only; no auth, no external
exposure. This app is fully self-contained and depends on nothing outside this
directory.
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    request,
    send_from_directory,
)

import synth

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
HOST = "127.0.0.1"
PORT = 5005
CACHE_LIMIT = 256  # most-recent rendered variants kept in memory for audition/save

app = Flask(__name__, static_folder="static", static_url_path="/static")

# In-memory render cache: id -> {type, wav(bytes), seed, meta}. Bounded LRU-ish.
_cache: "OrderedDict[str, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_counter = 0
_counter_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Config persistence                                                          #
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"output_dir": ""}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def validate_dir(path_str: str) -> tuple[bool, str]:
    """Return (ok, message). A dir is OK if it exists and is writable."""
    if not path_str or not path_str.strip():
        return False, "No output folder set."
    p = Path(path_str.strip()).expanduser()
    if not p.exists():
        return False, f"Folder does not exist: {p}"
    if not p.is_dir():
        return False, f"Not a folder: {p}"
    probe = p / f".write_test_{int(time.time()*1000)}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"Folder is not writable: {exc}"
    return True, f"Folder OK: {p}"


# --------------------------------------------------------------------------- #
# Cache helpers                                                               #
# --------------------------------------------------------------------------- #
def _next_id() -> str:
    global _counter
    with _counter_lock:
        _counter += 1
        return f"v{_counter:08d}"


def _cache_put(entry: dict) -> str:
    vid = _next_id()
    with _cache_lock:
        _cache[vid] = entry
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)  # evict oldest
    return vid


def _cache_get(vid: str) -> dict | None:
    with _cache_lock:
        return _cache.get(vid)


def _unique_path(directory: Path, base: str, ext: str = ".wav") -> Path:
    """A path under ``directory`` that does not yet exist (never overwrite)."""
    candidate = directory / f"{base}{ext}"
    i = 1
    while candidate.exists():
        candidate = directory / f"{base}_{i:03d}{ext}"
        i += 1
    return candidate


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> Response:
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def api_state():
    cfg = load_config()
    ok, msg = validate_dir(cfg.get("output_dir", ""))
    types = [
        {
            "key": s.key,
            "label": s.label,
            "description": s.description,
            "defaults": {"pitch": s.pitch, "harshness": s.harshness, "reverb": s.reverb},
        }
        for s in synth.SPECS.values()
    ]
    return jsonify({
        "output_dir": cfg.get("output_dir", ""),
        "output_dir_ok": ok,
        "output_dir_msg": msg,
        "types": types,
        "sample_rate": synth.SAMPLE_RATE,
    })


@app.post("/api/config")
def api_config():
    data = request.get_json(force=True, silent=True) or {}
    output_dir = str(data.get("output_dir", "")).strip()
    ok, msg = validate_dir(output_dir)
    # Persist whatever the user typed (even if invalid) so it survives a restart,
    # but report validity so the UI can warn before any save is attempted.
    cfg = load_config()
    cfg["output_dir"] = output_dir
    save_config(cfg)
    return jsonify({"output_dir": output_dir, "ok": ok, "msg": msg})


@app.post("/api/generate")
def api_generate():
    data = request.get_json(force=True, silent=True) or {}
    req_type = str(data.get("type", "")).strip()
    types = list(synth.SPECS) if req_type == "all" else [req_type]
    for t in types:
        if t not in synth.SPECS:
            abort(400, f"unknown type: {t!r}")

    try:
        count = int(data.get("count", 4))
    except (TypeError, ValueError):
        abort(400, "count must be an integer")
    count = max(1, min(12, count))

    pitch = float(data.get("pitch", 50))
    harshness = float(data.get("harshness", 50))
    reverb = float(data.get("reverb", 50))

    seed_field = data.get("seed", None)
    base_seed = None
    if seed_field not in (None, "", "null"):
        try:
            base_seed = int(seed_field)
        except (TypeError, ValueError):
            abort(400, "seed must be an integer or blank")

    variants = []
    for t in types:
        for i in range(count):
            if base_seed is None:
                # Fresh randomness per variant; still reported so it's reproducible.
                seed = int.from_bytes(__import__("os").urandom(4), "little")
            else:
                seed = base_seed + i
            samples = synth.render_variant(
                t, pitch=pitch, harshness=harshness, reverb=reverb, seed=seed,
            )
            wav = synth.to_wav_bytes(samples)
            meta = synth.measure(samples)
            vid = _cache_put({"type": t, "wav": wav, "seed": seed, "meta": meta})
            clipping = meta["peak"] >= 0.999
            variants.append({
                "id": vid,
                "type": t,
                "label": synth.SPECS[t].label,
                "seed": seed,
                "url": f"/api/audio/{vid}.wav",
                "clipping": clipping,
                **meta,
            })
    return jsonify({"variants": variants})


@app.get("/api/audio/<vid>.wav")
def api_audio(vid: str):
    entry = _cache_get(vid)
    if entry is None:
        abort(404, "variant expired or not found")
    return Response(entry["wav"], mimetype="audio/wav")


@app.post("/api/save")
def api_save():
    data = request.get_json(force=True, silent=True) or {}
    vid = str(data.get("id", ""))
    entry = _cache_get(vid)
    if entry is None:
        return jsonify({"ok": False, "msg": "Variant expired — regenerate it."}), 404

    cfg = load_config()
    ok, msg = validate_dir(cfg.get("output_dir", ""))
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 400

    out_root = Path(cfg["output_dir"].strip()).expanduser()
    sub = out_root / entry["type"]
    try:
        sub.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return jsonify({"ok": False, "msg": f"Could not create subfolder: {exc}"}), 400

    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{entry['type']}_{entry['seed']}_{stamp}"
    dest = _unique_path(sub, base)
    try:
        dest.write_bytes(entry["wav"])  # never overwrites: _unique_path guarantees new
    except OSError as exc:
        return jsonify({"ok": False, "msg": f"Write failed: {exc}"}), 400

    return jsonify({"ok": True, "path": str(dest), "msg": f"Saved to {dest}"})


def main() -> None:
    url = f"http://{HOST}:{PORT}/"
    print("\n  Thargoid Voices — generator & curator")
    print(f"  Serving at {url}  (localhost only)\n")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:  # pragma: no cover - convenience only
        pass
    # threaded=True so audio serving doesn't block generation; debug off (public repo).
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
