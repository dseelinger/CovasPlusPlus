"""The 'ultimate checklist' — a markdown to-do list read/updated by voice.

Task lines use standard markdown syntax:
    - [ ] an unfinished objective
    - [x] a completed objective
Headings, notes and blank lines are ignored. Task lines are numbered 1..N in file
order; that number is a stable handle for marking a specific item. The file is read
fresh on every call, so hand-edits are picked up live.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ITEM_RE = re.compile(r"^(\s*[-*]\s+)\[( |x|X)\]\s*(.*)$")


class Checklist:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.current = 0  # 1-based "current line" cursor (0 = none)

    # ---- low-level --------------------------------------------------------
    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def _task_lines(lines: list[str]):
        """[(line_index, done_bool, text)] for each markdown task line."""
        out = []
        for i, line in enumerate(lines):
            m = ITEM_RE.match(line)
            if m:
                out.append((i, m.group(2).lower() == "x", m.group(3).strip()))
        return out

    # ---- public API (numbers are 1-based over task lines) -----------------
    def items(self):
        """[(number, done, text)] over all task lines."""
        return [(n, d, t) for n, (_, d, t) in enumerate(self._task_lines(self._lines()), 1)]

    def progress(self) -> tuple[int, int]:
        items = self.items()
        return sum(1 for _, d, _ in items if d), len(items)

    def next_pending(self, count: int = 1):
        """(list[(number, text)] of first `count` pending, done, total)."""
        items = self.items()
        pend = [(n, t) for n, d, t in items if not d]
        done = sum(1 for _, d, _ in items if d)
        return pend[:max(1, count)], done, len(items)

    def find(self, query: str, limit: int = 10):
        """Search objectives. Returns [(number, done, text)] — exact match wins,
        else substring matches, else best word-overlap matches."""
        q = (query or "").strip().lower()
        if not q:
            return []
        items = self.items()
        exact = [(n, d, t) for n, d, t in items if t.lower() == q]
        if exact:
            return exact[:limit]
        sub = [(n, d, t) for n, d, t in items if q in t.lower()]
        if sub:
            return sub[:limit]
        qtok = set(q.split())
        scored = []
        for n, d, t in items:
            s = len(qtok & set(t.lower().split()))
            if s > 0:
                scored.append((s, n, d, t))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [(n, d, t) for _s, n, d, t in scored[:limit]]

    def set_number(self, number: int, completed: bool) -> str | None:
        """Set the done-state of task line `number` (1-based). Returns its text."""
        lines = self._lines()
        tasks = self._task_lines(lines)
        if not (1 <= number <= len(tasks)):
            return None
        line_idx = tasks[number - 1][0]
        m = ITEM_RE.match(lines[line_idx])
        text = m.group(3).strip()
        lines[line_idx] = f"{m.group(1)}[{'x' if completed else ' '}] {text}"
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.current = number
        return text

    # ---- cursor + CRUD ----------------------------------------------------
    def current_item(self):
        """(number, done, text) of the current line, or None."""
        items = self.items()
        if 1 <= self.current <= len(items):
            n, d, t = items[self.current - 1]
            return n, d, t
        return None

    def _resolve(self, number: int | None) -> int:
        """A target task number: explicit `number`, else the cursor."""
        return int(number) if number else self.current

    def _write(self, lines: list[str]) -> None:
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def add(self, text: str, position: str = "after", anchor: int | None = None):
        """Insert a new pending task relative to an anchor (default = current).
        Matches the anchor's indentation/bullet. Returns (new_number, text)."""
        text = text.strip()
        lines = self._lines()
        tasks = self._task_lines(lines)
        if not tasks:  # empty list -> append at end of file
            lines.append(f"- [ ] {text}")
            self._write(lines)
            self.current = 1
            return 1, text
        num = self._resolve(anchor) or len(tasks)
        num = max(1, min(num, len(tasks)))
        anchor_idx = tasks[num - 1][0]
        prefix = ITEM_RE.match(lines[anchor_idx]).group(1)
        insert_at = anchor_idx + (1 if position != "before" else 0)
        lines.insert(insert_at, f"{prefix}[ ] {text}")
        self._write(lines)
        new_num = next((k for k, (li, _d, _t) in enumerate(self._task_lines(lines), 1)
                        if li == insert_at), num)
        self.current = new_num
        return new_num, text

    def modify(self, new_text: str, number: int | None = None):
        """Replace the text of a task (default = current), keeping its check state
        and indentation. Returns (number, new_text) or None."""
        new_text = new_text.strip()
        lines = self._lines()
        tasks = self._task_lines(lines)
        num = self._resolve(number)
        if not (1 <= num <= len(tasks)):
            return None
        li = tasks[num - 1][0]
        m = ITEM_RE.match(lines[li])
        lines[li] = f"{m.group(1)}[{m.group(2)}] {new_text}"
        self._write(lines)
        self.current = num
        return num, new_text

    def delete(self, number: int | None = None):
        """Remove a task (default = current). Returns its text, or None. The cursor
        moves to whatever now occupies that slot (or the last item)."""
        lines = self._lines()
        tasks = self._task_lines(lines)
        num = self._resolve(number)
        if not (1 <= num <= len(tasks)):
            return None
        li = tasks[num - 1][0]
        text = tasks[num - 1][2]
        del lines[li]
        self._write(lines)
        remaining = len(self._task_lines(lines))
        self.current = min(num, remaining)  # 0 if list now empty
        return text


def checklist_event(checklist: Checklist) -> dict:
    """Build the payload for a `checklist` bus event (#82).

    Carries the full item list + progress (for any listener) AND the raw file markdown
    with its content-hash `version`, so a live Checklist-page client can re-render IN
    PLACE and keep its stale-write token in sync with GET /api/checklist. It hashes the
    SAME raw bytes web._file_version does and decodes utf-8-sig (BOM-safe), so the
    version and markdown here are byte-identical to a fresh page load. Read-only /
    fail-soft: an unreadable file yields an empty, still-publishable snapshot."""
    try:
        data = checklist.path.read_bytes()
    except OSError:
        data = b""
    version = hashlib.sha256(data).hexdigest()[:16]
    try:
        markdown = data.decode("utf-8-sig") if data else ""
    except UnicodeDecodeError:  # a mangled file must not break the sync path
        markdown = ""
    items = checklist.items()
    done = sum(1 for _n, d, _t in items if d)
    return {
        "type": "checklist",
        "items": [{"n": n, "done": d, "text": t} for n, d, t in items],
        "done": done, "total": len(items),
        "markdown": markdown, "version": version,
    }
