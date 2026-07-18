# Engineer dashboard

Elite Dangerous has **20+ ship engineers**, each unlocked by its own invitation task and workshop
gift. Keeping track of who you've unlocked, who you're part-way with, and what each of the rest
still needs is exactly the kind of bookkeeping a companion should carry for you.

COVAS++ already answers this **by voice** — ask *"which engineers have I unlocked"* or *"how do I
unlock The Dweller"* and it reads your journal and tells you (see below). The **Engineer
dashboard** adds the other half: an at-a-glance **grid** of the whole fleet of engineers, so
"show me everything left across all of them" is one glance instead of a spoken list.

## Opening it

Open the control panel and click **🔧 engineers** (also linked from the Checklist, Memory, and
Crew pages). It's a **read-only** view — nothing to save, nothing to configure.

## What you see

Every ship engineer is shown as a card, colour-tagged by your live status:

| Status | Meaning |
|--------|---------|
| **Unlocked** | Their workshop is open. The badge shows the highest **grade** you've reached (e.g. *Unlocked · G5*). |
| **In progress** | You've either been **Invited** (you can visit — just deliver the unlock gift) or **Discovered** them (you still owe the invitation task). |
| **Locked** | You haven't started, or you're **Barred**. |

Each card lists the engineer's **base and system**, the **modules and weapons** they engineer, any
**permit** note, and — for anyone not yet unlocked — the **outstanding requirement**: exactly what
still stands between you and their workshop.

- Tap a **status chip** (All / Unlocked / In progress / Locked) to filter.
- Use the **search box** to filter by engineer name, system, or module (e.g. `FSD`, `shields`,
  `Colonia`).

!!! note "Before the game is running"
    The live status comes from Elite's **`EngineerProgress`** journal event, written at login. With
    the game closed (or journal monitoring off), the page shows a **"no journal data yet"** note and
    lists every engineer as **locked** with its requirements — still a useful reference, just without
    your personal progress.

## Where the data comes from

The dashboard is a pure **view** over two sources COVAS++ already had — it adds **no new data** and
never writes anything:

- the bundled, offline **reference table** of every engineer's location, specialties, and unlock
  requirements (`covas/ed/engineers.py`), and
- your live **`EngineerProgress`** map, read from the journal and kept on the game-state context.

That's the same join the voice tools use, so the grid and the spoken answers always agree.

## The voice path

The dashboard complements — it doesn't replace — the engineer voice tools:

- *"which engineers have I unlocked"* / *"what engineers do I still need"* — a spoken rundown.
- *"where is Felicity Farseer"* / *"how do I unlock The Dweller"* — one engineer's location, status,
  and what's left, with their system copied to your clipboard for plotting.
- *"which engineer upgrades my FSD"* — the engineers for a given module, each tagged with whether
  you've unlocked them.

Voice is best for a quick question mid-flight; the dashboard is best for planning "what's left".
