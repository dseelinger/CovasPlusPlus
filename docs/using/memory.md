# Persistent memory

COVAS++ can keep a small set of **facts about you** — how you like to be addressed, your main
ship, standing preferences — so the companion isn't starting from zero every session. Unlike the
memory in other Elite assistants, this one is **transparent**: it's a plain text file you own, can
read, edit, or delete, and it never leaves your machine.

!!! note "What's live"
    COVAS++ ships the memory **store and recall engine** (issue #59), **automatic capture**
    (issue #60), **recall in conversation** (issue #61), and a **memory browser** in the control
    panel (issue #62): it populates memory on its own — from journal milestones and from durable
    facts you mention — brings the right facts back into a turn when you reach for them ("do you
    remember my main ship?"), and lets you read, search, edit, delete, and add memories yourself,
    all offline and free.

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

## Browse and edit it in the control panel

Prefer not to open a text editor? The **Memory** tab of the [control panel](../control-panel.md)
(the 🧠 link in the header, or `http://127.0.0.1:8765/memory`) is a full read/write view of the
same file:

- **See everything on file** — each memory with its type, tags, and when it was recorded.
- **Search** — filter live by any word in the text, a tag, or the type.
- **Edit** — change a memory's text, type, or tags in place. The `id` and original timestamp are
  preserved, so it round-trips losslessly.
- **Delete** — remove a memory you don't want kept.
- **Add** — jot a fact by hand (text, an optional type, and comma-separated tags).

The browser edits the **exact same `memory.jsonl`** the voice loop reads and writes, so a memory
you add here is recalled in conversation immediately, and anything the companion learns shows up
here on reload. Because both sides share one file, saves carry a **stale-write guard**: if the
voice loop wrote to memory while you had the tab open, the panel notices the file changed and asks
you to reload rather than silently overwriting that change — the same protection the checklist
editor uses.

## Recall in conversation

COVAS++ brings relevant memories into a turn **two** ways — and both are careful never to bloat the
prompt or bust the model's cache.

**Automatic, when your question reaches into the past.** A tiny rules pass (the same trick as the
"where am I" game-context detector) watches for turns that reference memory — *"do you remember…",
"what's my favourite…", "have I been here before", "remind me what…"*. When one matches, the most
relevant stored facts are found and prepended as a short reference block **to that one turn's
message only** — never to the cached system prompt — so the companion answers from what it actually
knows instead of guessing. If nothing relevant is on file, nothing is added. A turn that doesn't
reference the past is left completely untouched (no memory, no extra tokens). You can force a lookup
for a turn with the **`recall` wake word** ("recall, what's my main ship?"), which is scrubbed from
what the model sees.

**On demand, when the companion decides to check.** COVAS++ also has a `recall_memory` tool it can
call mid-reply to look something up explicitly. Both paths are a **free, offline read** of your
local file.

Under the hood, recall finds the facts most relevant to a question with **keyword and tag matching**
— fast, fully **offline, and free**, with no API and no extra software. Tags are weighted above body
words, so tagging a fact with `name` makes "what's my name?" find it reliably.

The trigger phrases are tunable in [`config.toml`](../configuration.md) via `[memory].recall_phrases`
(the natural asks) and `[memory].recall_wake` (the manual override word) — the same shape as the
`[elite]` context phrases.

There's an **optional** semantic-recall mode backed by text embeddings that can match by *meaning*
rather than shared words. It is **off by default** on purpose: embeddings cost money and send text
to a provider, which runs against COVAS++'s privacy-first, cost-first stance. No embedding backend
ships yet; leaving `[memory.embedding].enabled = false` (the default) keeps recall on the free,
offline keyword path.

!!! tip "Cache-safe by design"
    The recall block rides the **current turn's user message**, which the model never caches, and
    your stored conversation history keeps the *clean* question. So injecting memories never grows
    the cached system prefix — recall costs nothing against the prompt cache.

## Settings

In [`config.toml`](../configuration.md):

| Setting | Default | What it does |
|---------|---------|--------------|
| `[memory].enabled` | `true` | Master switch for loading/saving memory, automatic capture, **and** recall. |
| `[memory].dir` | `"memory"` | Folder (under your data dir) holding `memory.jsonl`. Git-ignored. |
| `[memory].cap` | `500` | Upper bound on stored records; oldest journal milestones are pruned first. |
| `[memory].recall_phrases` | *(list)* | Phrases that trigger automatic recall into a turn ("do you remember"…). |
| `[memory].recall_wake` | `["recall"]` | Manual-override word forcing a lookup for a turn; scrubbed from the model's input. |
| `[memory.embedding].enabled` | `false` | Opt in to semantic recall (costs money; off keeps keyword recall). |
| `[memory.embedding].provider` | `""` | Name of an embedding backend (none available yet). |

## How this compares

Other Elite voice assistants keep "memory" in an opaque store you can't see into or hand-correct.
COVAS++ memory is a **human-readable file you control** — inspect it, fix a fact, delete anything,
and know it stays on your machine — and it's **free by default**, with paid semantic recall strictly
opt-in.
