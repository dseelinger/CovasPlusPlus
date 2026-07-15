"""The transparent memory store: human-readable facts on disk, fail-soft I/O.

Format — one JSON object per line (JSON Lines / `.jsonl`), because it is the sweet spot for
this job: trivially machine-parseable, append-friendly (a new fact is one appended line, no
rewrite), and still readable/editable by a human in any text editor. Each line is one fact:

    {"id": "...", "text": "Commander prefers to be addressed as CMDR", "type": "preference",
     "tags": ["address", "name"], "when": "2026-07-15T12:00:00Z"}

WHY per-line JSON rather than one big JSON array: a single malformed line (a hand-edit typo,
a half-written line from a crash) must NOT take down the whole store. We parse line by line and
skip anything that doesn't decode — the rest of the memory survives. `text` is the only required
field; everything else has a sane default so a user can jot a bare `{"text": "..."}` by hand.

The file lives under the user's WRITABLE data dir (config `[memory].dir`, git-ignored) so memory
is private and never committed. Paths are injected (the store takes a concrete path), which keeps
unit tests hermetic — they point it at a tmp file, no config or data dir involved.
"""
from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    """UTC timestamp, second precision — enough to order facts, no locale surprises."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_tags(tags: object) -> tuple[str, ...]:
    """Coerce whatever a (possibly hand-edited) file holds into a clean tuple of lower tags."""
    if isinstance(tags, str):  # a lone string -> single tag (forgiving of hand edits)
        tags = [tags]
    if not isinstance(tags, (list, tuple)):
        return ()
    return tuple(str(t).strip().lower() for t in tags if str(t).strip())


@dataclass(slots=True)
class MemoryRecord:
    """One remembered fact plus light metadata. `text` is the fact; the rest is for recall/UX."""

    text: str
    type: str = "note"            # coarse kind: preference | fact | note | ... (free-form)
    tags: tuple[str, ...] = ()    # normalized lower-case keywords for cheap tag recall
    when: str = field(default_factory=_now_iso)  # ISO-8601 UTC; when the fact was recorded
    id: str = ""                  # stable id (uuid4 hex); filled on creation if blank

    def __post_init__(self) -> None:
        self.tags = _norm_tags(self.tags)
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "type": self.type,
                "tags": list(self.tags), "when": self.when}

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryRecord":
        """Build from a parsed line. Missing/odd fields fall back to defaults (fail soft);
        raises ValueError only if there is no usable `text` — an empty fact is meaningless."""
        text = str(d.get("text", "")).strip()
        if not text:
            raise ValueError("memory record has no text")
        return cls(
            text=text,
            type=str(d.get("type", "note")) or "note",
            tags=_norm_tags(d.get("tags", ())),
            when=str(d.get("when", "")) or _now_iso(),
            id=str(d.get("id", "")),
        )


class MemoryStore:
    """Load/save a JSONL fact file, fail-soft. Holds an in-memory list so recall never re-reads
    disk; `add` appends both to memory and to the file (one line, no full rewrite)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._records: list[MemoryRecord] = []
        self._loaded = False

    # --- read -------------------------------------------------------------
    def load(self) -> list[MemoryRecord]:
        """Parse the file line by line. A missing file is simply an empty memory; a corrupt
        line (bad JSON or no text) is skipped with a warning so one typo can't nuke the store."""
        self._records = []
        self._loaded = True
        if not self.path.exists():
            return self._records
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as e:  # unreadable file -> empty memory, never crash the caller
            self._warn(f"could not read memory file {self.path} ({e})")
            return self._records
        for lineno, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):  # allow blank lines + '#' comments in the file
                continue
            try:
                self._records.append(MemoryRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                self._warn(f"skipping corrupt memory line {lineno} in {self.path.name} ({e})")
        return self._records

    def all(self) -> list[MemoryRecord]:
        """Every record, loading on first use. Returns the live list — treat as read-only."""
        if not self._loaded:
            self.load()
        return self._records

    # --- write ------------------------------------------------------------
    def add(self, record: MemoryRecord) -> MemoryRecord:
        """Append one fact. Writes a single line (append mode) so a large memory stays cheap to
        grow. Fail-soft: a write error leaves the in-memory copy intact and warns, doesn't raise."""
        if not self._loaded:
            self.load()
        self._records.append(record)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except OSError as e:
            self._warn(f"could not append to memory file {self.path} ({e})")
        return record

    def remember(self, text: str, *, type: str = "note",  # noqa: A002 — mirrors the record field
                 tags: object = ()) -> MemoryRecord:
        """Convenience: build a record (id + timestamp auto-filled) and persist it."""
        return self.add(MemoryRecord(text=text, type=type, tags=_norm_tags(tags)))

    def save(self, records: list[MemoryRecord] | None = None) -> None:
        """Rewrite the whole file (for edits/pruning, vs. the append-only `add`). Writes to a
        temp file then replaces, so a crash mid-write can't corrupt the existing store."""
        if records is not None:
            self._records = list(records)
            self._loaded = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            body = "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in self._records)
            tmp.write_text(body + ("\n" if body else ""), encoding="utf-8")
            tmp.replace(self.path)  # atomic on the same filesystem
        except OSError as e:
            self._warn(f"could not save memory file {self.path} ({e})")

    @staticmethod
    def _warn(msg: str) -> None:
        # Match app.py's fail-soft diagnostics: a line to stderr, never an exception upward.
        print(f"!! [memory] {msg}", file=sys.stderr, flush=True)


def store_from_config(cfg: dict) -> MemoryStore:
    """Build a store from loaded config. `[memory].dir` is already resolved to an absolute path
    under the writable data dir by config._resolve_paths; the store lives at <dir>/memory.jsonl.
    Composition-root helper only — tests construct MemoryStore(path) directly with a tmp file."""
    mem = cfg.get("memory", {})
    base = Path(mem.get("dir") or (Path.cwd() / "memory"))
    return MemoryStore(base / "memory.jsonl")
