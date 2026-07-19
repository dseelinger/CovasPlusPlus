"""Fail-soft persistence for authored macro specs (issue #50).

Mirrors the memory store's design (`covas/memory/store.py`): one JSON object per line (JSONL),
so a macro is added by appending a single line, the file stays human-readable/editable, and a
single malformed line (a hand-edit typo, a half-written line from a crash) is skipped rather
than taking down the whole collection.

The file lives under the WRITABLE data dir (`[macros].file`, resolved to an absolute path by
`config._resolve_paths`). It's git-ignored: an authored macro is Commander content and could
reference their own play style, so it never gets committed. Paths are injected (the store takes
a concrete path), keeping unit tests hermetic — they point it at a tmp file, no config involved.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .spec import MacroSpec


class MacroStore:
    """Load/save a JSONL file of `MacroSpec`s, fail-soft. Holds an in-memory list so lookups
    never re-read disk; mutations rewrite the whole (small) file atomically."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._specs: list[MacroSpec] = []
        self._loaded = False

    # -- read ------------------------------------------------------------------
    def load(self) -> list[MacroSpec]:
        """Parse the file line by line. A missing file is simply an empty collection; a corrupt
        line (bad JSON, or a spec with no name/steps) is skipped with a warning so one typo can't
        nuke every macro. Returns the live list — treat as read-only."""
        self._specs = []
        self._loaded = True
        if not self.path.exists():
            return self._specs
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as e:
            self._warn(f"could not read macros file {self.path} ({e})")
            return self._specs
        for lineno, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):   # allow blank lines + '#' comments
                continue
            try:
                self._specs.append(MacroSpec.from_dict(json.loads(line)))
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self._warn(f"skipping corrupt macro on line {lineno} in {self.path.name} ({e})")
        return self._specs

    def all(self) -> list[MacroSpec]:
        """Every spec, loading on first use."""
        if not self._loaded:
            self.load()
        return self._specs

    def get(self, name: str) -> MacroSpec | None:
        """The spec whose name matches `name` case-insensitively, or None. Name is the Commander's
        handle ('run Dock ASAP'), so lookup is forgiving of spoken casing."""
        want = str(name or "").strip().lower()
        if not want:
            return None
        for s in self.all():
            if s.name.strip().lower() == want:
                return s
        return None

    # -- write -----------------------------------------------------------------
    def add(self, spec: MacroSpec) -> MacroSpec:
        """Add (or REPLACE by name) a macro and persist. Replacing on a duplicate name keeps
        'create a macro called X' idempotent — re-authoring X overwrites rather than duplicates.
        Fail-soft: a write error leaves the in-memory copy intact and warns, never raises."""
        if not self._loaded:
            self.load()
        self._specs = [s for s in self._specs if s.name.strip().lower()
                       != spec.name.strip().lower()]
        self._specs.append(spec)
        self._save()
        return spec

    def delete(self, name: str) -> bool:
        """Delete a macro by (case-insensitive) name. Returns True if one was removed."""
        if not self._loaded:
            self.load()
        want = str(name or "").strip().lower()
        kept = [s for s in self._specs if s.name.strip().lower() != want]
        if len(kept) == len(self._specs):
            return False
        self._specs = kept
        self._save()
        return True

    def save(self, specs: list[MacroSpec] | None = None) -> None:
        """Replace the whole collection (used by the web editor's whole-file writes)."""
        if specs is not None:
            self._specs = list(specs)
            self._loaded = True
        self._save()

    def _save(self) -> None:
        """Rewrite the file atomically (unique temp file then replace) so a crash mid-write can't
        corrupt the existing store. A UNIQUE per-write temp file (not a fixed `.tmp`) means two
        concurrent saves — the voice worker and the web thread both persisting — can't interleave
        into the same scratch file; each writes its own, and `os.replace` (atomic on the same
        filesystem) makes the last one win cleanly rather than leaving a torn file. Fail-soft: an
        OS error warns, never raises."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(json.dumps(s.to_dict(), ensure_ascii=False) for s in self._specs)
            # mkstemp gives a unique name in the target dir (same filesystem, so os.replace is
            # atomic). Own the fd, write, replace; on any error remove the orphan temp file.
            fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent),
                                            prefix=self.path.name + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body + ("\n" if body else ""))
                os.replace(tmp_name, self.path)
            except OSError:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as e:
            self._warn(f"could not save macros file {self.path} ({e})")

    @staticmethod
    def _warn(msg: str) -> None:
        print(f"!! [macros] {msg}", file=sys.stderr, flush=True)


def store_from_config(cfg: dict) -> MacroStore:
    """Build a store from loaded config. `[macros].file` is already resolved to an absolute path
    under the writable data dir by `config._resolve_paths`. Composition-root helper only — tests
    construct `MacroStore(path)` directly with a tmp file."""
    mc = cfg.get("macros", {}) or {}
    raw = mc.get("file") or (Path.cwd() / "custom_macros.jsonl")
    return MacroStore(Path(raw))
