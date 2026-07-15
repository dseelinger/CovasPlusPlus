# Persistent memory

COVAS++ can keep a small set of **facts about you** — how you like to be addressed, your main
ship, standing preferences — so the companion isn't starting from zero every session. Unlike the
memory in other Elite assistants, this one is **transparent**: it's a plain text file you own, can
read, edit, or delete, and it never leaves your machine.

!!! note "What's live"
    COVAS++ ships the memory **store and recall engine** (issue #59) and **automatic capture**
    (issue #60): it now populates memory on its own — from journal milestones and from durable
    facts you mention — so you don't have to seed it by hand. *Recalling* memories aloud mid-
    conversation ("what do you remember about…") is the remaining follow-up (issue #61).

## What gets remembered automatically

When `[memory].enabled` is on, COVAS++ writes to memory **without being asked**, from two cheap
sources — and it never costs money or touches the network to do so.

**Journal milestones.** As you play, a curated set of genuinely notable journal events becomes a
durable one-line memory. Deliberately a *small, high-signal* set — quality over volume:

| Event | Remembered as |
|-------|---------------|
| First to discover a body (detailed scan) | `First to discover <body>` |
| Fully mapping a body | `Fully mapped <body>` |
| Death | `Died` (with the killer, when known) |
| Rank promotion | `Promoted: reached <category> rank <n>` |
| Buying a fleet carrier | `Bought a fleet carrier (<callsign>)` |
| Adding a new ship to the fleet | `Added a <ship> to the fleet` |
| A **lucrative** mission, exploration payout, or voucher redemption | the payday, in credits |

Money events are only kept when they clear a **notable-credits floor** (10 million by default),
so routine income never clutters the log. These describers are **deterministic table lookups —
no LLM runs per event**, mirroring the "check my logs" recent-events feed.

**Things you say.** When you state a standing preference or instruction — "remember that I prefer
the Krait", "I like to be called Commander", "my main ship is the Anaconda" — COVAS++ notes it.
This rides the reply it's **already** writing (a tool call inside the same turn), so it adds **no
extra model call and no extra cost**.

Every capture is **deduplicated** against what's already stored (a repeated milestone or a
reworded-but-identical fact is ignored), and the store is **capped** (`[memory].cap`, default 500)
so an always-on capture can't grow the file without limit. When the cap is reached, the oldest
auto-captured **journal milestones** are pruned first — they're reproducible from the journal —
and facts you explicitly asked to remember are pruned only if they alone exceed the cap.

!!! info "Only new events, and only while you play"
    Capture listens to *live* game events. On startup COVAS++ warms its game-state context from
    your current journal **without** re-capturing it, so relaunching never re-adds old milestones.

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
| `[memory].enabled` | `true` | Master switch for loading/saving memory **and** automatic capture. |
| `[memory].dir` | `"memory"` | Folder (under your data dir) holding `memory.jsonl`. Git-ignored. |
| `[memory].cap` | `500` | Upper bound on stored records; oldest journal milestones are pruned first. |
| `[memory.embedding].enabled` | `false` | Opt in to semantic recall (costs money; off keeps keyword recall). |
| `[memory.embedding].provider` | `""` | Name of an embedding backend (none available yet). |

## How this compares

Other Elite voice assistants keep "memory" in an opaque store you can't see into or hand-correct.
COVAS++ memory is a **human-readable file you control** — inspect it, fix a fact, delete anything,
and know it stays on your machine — and it's **free by default**, with paid semantic recall strictly
opt-in.
