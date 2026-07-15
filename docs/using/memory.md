# Persistent memory

COVAS++ can keep a small set of **facts about you** — how you like to be addressed, your main
ship, standing preferences — so the companion isn't starting from zero every session. Unlike the
memory in other Elite assistants, this one is **transparent**: it's a plain text file you own, can
read, edit, or delete, and it never leaves your machine.

!!! note "Foundation release"
    This release ships the memory **store and recall engine** (issue #59). Teaching COVAS++ to
    *remember things you say* and *recall them mid-conversation* by voice is a follow-up
    (issue #61). For now, memory is something you can seed and inspect by hand, and that internal
    features build on.

## Where it lives

Your memory is a single file, `memory.jsonl`, under your writable data dir:

- **Source / dev run:** `memory/memory.jsonl` in the project root.
- **Installed app:** `%APPDATA%\COVAS++\memory\memory.jsonl`.

It is **git-ignored and private** — nothing about you is ever committed or uploaded. The folder is
set by `[memory].dir` in [`config.toml`](../configuration.md).

## The format (readable and editable)

Each line is one fact as a small JSON object. `text` is the fact; everything else is optional
metadata that helps recall:

```json
{"text": "prefers the Krait Mk II for combat", "type": "preference", "tags": ["ship"]}
{"text": "Commander's name is Jameson", "type": "fact", "tags": ["name"]}
```

| Field  | Meaning |
|--------|---------|
| `text` | The fact itself (the only required field). |
| `type` | Coarse kind — `preference`, `fact`, `note`, … (free-form). |
| `tags` | Keywords used for quick recall — a tag match counts for more than a body-word match. |
| `when` | When it was recorded (ISO-8601 UTC; filled in automatically). |
| `id`   | A stable identifier (filled in automatically). |

You can hand-edit this file in any text editor. A bare `{"text": "likes metric units"}` line is
perfectly valid — the rest fills in. If a line ever gets mangled (a stray comma, a half-written
line after a crash), COVAS++ **skips just that line** and keeps the rest of your memory — one typo
never wipes the file. Blank lines and lines starting with `#` are ignored, so you can annotate it.

## How recall works

Recall finds the facts most relevant to a question. By default it uses **keyword and tag matching**
— fast, fully **offline, and free**, with no API and no extra software. Tags are weighted above
body words, so tagging a fact with `name` makes "what's my name?" find it reliably.

There's an **optional** semantic-recall mode backed by text embeddings that can match by *meaning*
rather than shared words. It is **off by default** on purpose: embeddings cost money and send text
to a provider, which runs against COVAS++'s privacy-first, cost-first stance. No embedding backend
ships yet; leaving `[memory.embedding].enabled = false` (the default) keeps recall on the free,
offline keyword path.

## Settings

In [`config.toml`](../configuration.md):

| Setting | Default | What it does |
|---------|---------|--------------|
| `[memory].enabled` | `true` | Master switch for loading/saving memory. |
| `[memory].dir` | `"memory"` | Folder (under your data dir) holding `memory.jsonl`. Git-ignored. |
| `[memory.embedding].enabled` | `false` | Opt in to semantic recall (costs money; off keeps keyword recall). |
| `[memory.embedding].provider` | `""` | Name of an embedding backend (none available yet). |

## How this compares

Other Elite voice assistants keep "memory" in an opaque store you can't see into or hand-correct.
COVAS++ memory is a **human-readable file you control** — inspect it, fix a fact, delete anything,
and know it stays on your machine — and it's **free by default**, with paid semantic recall strictly
opt-in.
