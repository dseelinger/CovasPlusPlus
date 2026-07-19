"""Drop-in content pipeline (C11) — auto-discover audio + line content from convention folders.

Adding content is dropping a file in the right place (or editing a text file) — NO code or config
edits. At startup the loader scans a fixed folder skeleton and overlays what it finds onto the
registered cues, music contexts, and line pools. A missing/empty folder or file leaves that cue
simply SILENT (no error), per the fail-closed-silent rule.

Conventions (relative to the project root; the skeleton + a README in each is created on first run):
  * SFX samples — audio/sfx/<cue>/*.{wav,ogg,flac}   (<cue> in SFX_CUES; any filenames)
  * Music tracks — audio/music/<context>/*.{wav,ogg,flac,mp3}   (<context> in MUSIC_CONTEXTS)
  * Chatter pools — content/chatter/<category>.txt   (one spoken line per non-blank line; '#' = comment)
  * Threat pool — content/interdiction_threat.txt

Chatter/threat FILES override that category's built-in pool when present; an absent file keeps the
shipped default (so chatter still works out of the box). SFX/music have no defaults — an empty
folder is silent. Decoding is soundfile's job (wav/flac/ogg; mp3 depends on the libsndfile build).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

AUDIO_EXTS = (".wav", ".ogg", ".flac")
MUSIC_EXTS = (".wav", ".ogg", ".flac", ".mp3")

SFX_CUES = ("thargoid_voices", "space_radiation", "hyperspace_weirdness", "interdiction_sting")
CHATTER_CATEGORIES = ("station_traffic", "system_patrol", "market_buzz", "populated_musing")
# SINGLE SOURCE OF TRUTH: reuse music.py's authoritative context tuple so the folders we scan and
# offer in the skeleton can never drift from the contexts music_context() actually returns. A prior
# private copy here listed `unpopulated`/`scooping_fuel` — state tokens that music_context() folds
# into `deep_space`/`near_star`, never returns — so tracks dropped there were silently unreachable
# (issue #160). `nebula` stays: it's a documented, registered library tag reserved for future
# auto-selection, not a fold-away state token.
from .music import MUSIC_CONTEXTS

_SFX_DIR = ("audio", "sfx")
_MUSIC_DIR = ("audio", "music")
_CHATTER_DIR = ("content", "chatter")
_THREAT_FILE = ("content", "interdiction_threat.txt")


def parse_line_file(path) -> list[str]:  # noqa: ANN001 — a path-like
    """Non-blank, non-'#'-comment lines, stripped. A missing file yields [] (silent, no error)."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[str] = []
    try:
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                out.append(line)
    except OSError:
        return []
    return out


def _scan_dir(folder: Path, exts: tuple[str, ...]) -> list[str]:
    """Absolute paths of audio files in `folder`, deterministically ordered by filename. Missing
    folder -> []."""
    if not folder.is_dir():
        return []
    files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]
    return [str(f) for f in sorted(files, key=lambda f: f.name.lower())]


@dataclass(frozen=True)
class ContentBundle:
    """Everything discovered under the convention folders. Empty dicts/lists = nothing dropped in."""

    sfx: dict[str, list[str]]       # cue name -> sample paths
    music: dict[str, list[str]]     # context -> track paths
    chatter: dict[str, list[str]]   # category -> spoken lines
    threat: list[str]               # interdiction threat-assessment lines


def empty_bundle() -> ContentBundle:
    return ContentBundle({}, {}, {}, [])


def load_content(base) -> ContentBundle:  # noqa: ANN001 — a path-like (project root)
    """Scan the convention folders under `base` and return what's present. Pure I/O read; never
    raises for a missing tree (unknown/extra folders are ignored)."""
    base = Path(base)
    sfx = {c: _scan_dir(base.joinpath(*_SFX_DIR, c), AUDIO_EXTS) for c in SFX_CUES}
    music = {c: _scan_dir(base.joinpath(*_MUSIC_DIR, c), MUSIC_EXTS) for c in MUSIC_CONTEXTS}
    chatter = {c: parse_line_file(base.joinpath(*_CHATTER_DIR, f"{c}.txt"))
               for c in CHATTER_CATEGORIES}
    threat = parse_line_file(base.joinpath(*_THREAT_FILE))
    return ContentBundle(sfx, music, chatter, threat)


# ---- overlay onto the registered cues / music / interdiction --------------------------------
def overlay_cues(cues, bundle: ContentBundle):
    """Return copies of `cues` with dropped-in content applied: SFX samples and chatter phrasings
    are replaced by the folder/file content when present, else the cue keeps its shipped default."""
    out = []
    for cue in cues:
        samples = bundle.sfx.get(cue.name)
        if samples:
            cue = replace(cue, samples=tuple(samples))
        lines = bundle.chatter.get(cue.name)
        if lines:
            cue = replace(cue, phrasings=tuple(lines))
        out.append(cue)
    return out


def merged_music_library(cfg: dict, bundle: ContentBundle):
    """A MusicLibrary combining [music.tracks] config with dropped-in audio/music/<context> files
    (config first, then any new folder tracks appended)."""
    from .music import MusicLibrary

    base = ((cfg.get("music", {}) or {}).get("tracks", {}) or {})
    tracks: dict[str, list[str]] = {k: list(v) for k, v in base.items() if isinstance(v, (list, tuple))}
    for ctx, paths in bundle.music.items():
        if not paths:
            continue
        bucket = tracks.setdefault(ctx, [])
        for p in paths:
            if p not in bucket:
                bucket.append(p)
    return MusicLibrary(tracks)


# ---- content-status report ------------------------------------------------------------------
def content_status(bundle: ContentBundle) -> list[dict]:
    """One row per cue/context/pool: {kind, name, count, has_default, state}. `state` is 'silent'
    (no content, no default), 'default' (no content but a built-in pool), or 'custom' (dropped-in
    content present) — so it's obvious what still needs content."""
    rows: list[dict] = []

    def _row(kind: str, name: str, count: int, has_default: bool) -> dict:
        state = "custom" if count else ("default" if has_default else "silent")
        return {"kind": kind, "name": name, "count": count,
                "has_default": has_default, "state": state}

    for name in SFX_CUES:
        rows.append(_row("sfx", name, len(bundle.sfx.get(name, [])), has_default=False))
    for name in MUSIC_CONTEXTS:
        rows.append(_row("music", name, len(bundle.music.get(name, [])), has_default=False))
    for name in CHATTER_CATEGORIES:
        rows.append(_row("chatter", name, len(bundle.chatter.get(name, [])), has_default=True))
    rows.append(_row("threat", "interdiction_threat", len(bundle.threat), has_default=True))
    return rows


def status_summary(bundle: ContentBundle) -> str:
    """One-line summary for the startup log / web readout."""
    rows = content_status(bundle)
    custom = [r["name"] for r in rows if r["state"] == "custom"]
    silent = [r["name"] for r in rows if r["state"] == "silent"]
    total = sum(r["count"] for r in rows)
    return (f"drop-in content: {len(custom)} populated ({total} files/lines); "
            f"silent (need files): {', '.join(silent) or 'none'}")


# ---- folder skeleton ------------------------------------------------------------------------
_SFX_README = ("Drop SFX for '{name}' here — {exts}. EVERY file in this folder joins the cue's "
               "rotation (deterministic by filename). An empty folder leaves this cue silent.\n")
_MUSIC_README = ("Drop music tracks for the '{name}' context here — {exts} (mp3 depends on your "
                 "libsndfile build). An empty folder means no music for this context.\n")
_CHATTER_README = ("One spoken line per non-blank line; lines starting with '#' are comments.\n"
                   "One file per category: {files}.\n"
                   "A file OVERRIDES that category's built-in pool; delete it to fall back.\n")


def _write_if_absent(path: Path, text: str) -> None:
    if not path.exists():
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass


def ensure_skeleton(base) -> None:  # noqa: ANN001 — a path-like (project root)
    """Create the convention folders + a README in each (idempotent; only writes what's missing),
    so it's obvious where content goes. Fail-soft — a filesystem error never blocks startup."""
    base = Path(base)
    try:
        for name in SFX_CUES:
            d = base.joinpath(*_SFX_DIR, name)
            d.mkdir(parents=True, exist_ok=True)
            _write_if_absent(d / "README.md", _SFX_README.format(name=name, exts=", ".join(AUDIO_EXTS)))
        for name in MUSIC_CONTEXTS:
            d = base.joinpath(*_MUSIC_DIR, name)
            d.mkdir(parents=True, exist_ok=True)
            _write_if_absent(d / "README.md", _MUSIC_README.format(name=name, exts=", ".join(MUSIC_EXTS)))
        cdir = base.joinpath(*_CHATTER_DIR)
        cdir.mkdir(parents=True, exist_ok=True)
        _write_if_absent(cdir / "README.md",
                         _CHATTER_README.format(files=", ".join(f"{c}.txt" for c in CHATTER_CATEGORIES)))
        for c in CHATTER_CATEGORIES:
            _write_if_absent(cdir / f"{c}.txt",
                             f"# {c}: one spoken line per line; '#' lines are comments. "
                             "Delete this file to use the built-in pool.\n")
        _write_if_absent(base.joinpath(*_THREAT_FILE),
                         "# Interdiction threat-assessment lines (the assistant, on the COVAS bus).\n"
                         "# One line each; '#' comments ignored. Empty -> built-in pool.\n")
    except OSError:
        pass


def threat_lines(bundle: ContentBundle, default: Optional[tuple[str, ...]] = None) -> tuple[str, ...]:
    """The interdiction threat pool: dropped-in lines if present, else the provided default."""
    return tuple(bundle.threat) if bundle.threat else tuple(default or ())
