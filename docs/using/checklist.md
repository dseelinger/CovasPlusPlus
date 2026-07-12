# The checklist

> *"I track your objective checklist — I can read what's next, mark items done or reopened, and
> add, change, or delete lines."*

COVAS++ keeps your **"ultimate checklist"** — a plain markdown to-do list — and reads and updates
it by voice. It's the same file whether you edit it by voice or in the
[control panel's checklist editor](../control-panel.md), so the two always stay in sync.

## What you can say

| You say… | It does… |
|----------|----------|
| *"What should I knock out next?"* | Reads your next pending objective, with overall progress ("66 of 807") |
| *"Give me my next three objectives."* | Reads the next few upcoming items |
| *"Mark that one done."* | Marks the item complete |
| *"Actually, reopen it."* | Flips a completed item back to pending |
| *"Add 'buy a fuel scoop' after that."* | Inserts a new item, matching the surrounding nesting |
| *"Change that to 'buy a 5A fuel scoop'."* | Replaces an item's text, keeping its checkbox state |
| *"Delete that line."* | Removes an item |

**Example to get started:** *"what should I knock out next"*

If a request matches several lines ("mark the fuel scoop one done" when there are three), COVAS++
**asks which one** rather than guessing.

## How it stays in sync

The checklist is a markdown file (`ultimate_checklist.md` by default, git-ignored because it's
personal). Voice edits and web edits both read and write that one file:

- Voice reads are always **fresh** — hand-edit the file, save, and the next "what's next?" reflects
  your change.
- The [web checklist editor](../control-panel.md) renders it as proper markdown with real
  checkboxes. If a voice edit lands while you have the editor open, it warns you before
  overwriting rather than clobbering your change.

Task lines use the standard `- [ ]` (to-do) and `- [x]` (done) markdown, and nesting is preserved,
so the file stays readable and portable.

## Settings

The checklist file location is `[checklist].file` in [`config.toml`](../configuration.md). There's
nothing to enable — the checklist tools are always available.
