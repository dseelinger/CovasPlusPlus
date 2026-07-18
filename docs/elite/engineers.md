# Engineers finder

> *"I find Elite's engineers — who upgrades a given module, where they are — and read your
> journal to tell you what you've unlocked and what's still needed."*

COVAS++ knows every ship engineer: where they are, what modules they improve, and how you unlock
them. Crucially, it reads your game journal's `EngineerProgress` — so it answers about **your**
progress (unlocked, invited, or not yet started) rather than reciting a generic wiki.

**Example:** *"which engineer unlocks my FSD"*

## What you can ask

| You say… | It tells you… |
|----------|---------------|
| *"Where is Felicity Farseer?"* | Her system and base, what she engineers, your unlock status, and it copies the system to your clipboard to plot a route |
| *"How do I unlock The Dweller?"* | Your journal status with them and exactly what's still needed — the invitation requirement and the unlock gift/task |
| *"Which engineer upgrades my shields?"* | Every engineer who engineers that module, each tagged with whether **you've** unlocked them |
| *"Which engineers have I unlocked?"* / *"What engineers do I still need?"* | A rundown: how many are unlocked, which are part-way, and which are still locked |

Name an engineer in plain speech (*"Farseer"*, *"Tod McQuinn"*, *"The Dweller"*) or name a module
(*"FSD"*, *"thrusters"*, *"multi-cannons"*, *"power plant"*). When you ask about a specific
engineer, COVAS++ copies their star system to your clipboard so you can paste it straight into the
galaxy map — unless you're already there.

## Journal-grounded status

The unlock status comes from the journal's `EngineerProgress` event, which the game writes at
login and whenever your progress changes. Statuses map to what the game reports:

- **Not yet started** — you have no progress with them; COVAS++ tells you how to earn the
  invitation and then unlock them.
- **Discovered (Known)** — you know of them but haven't earned the invitation yet.
- **Invited** — you can visit them; COVAS++ tells you the task to unlock their workshop.
- **Unlocked** — done, with the grade you've reached.

If COVAS++ hasn't seen an `EngineerProgress` event yet this session, it says so rather than
guessing.

## The reference table

Locations, specialties, and requirements come from a **bundled offline table** — no network is
used at runtime. It covers the bubble engineers and the Colonia region engineers.

Frontier occasionally relocates engineers or tweaks requirements between game updates. The table
is a point-in-time snapshot (see `covas/ed/engineers.py`, which documents its community sources and
how to refresh it). Your **status** is always live from the journal; only the generic requirement
prose is bundled.

## See the whole fleet at a glance

Voice is best for a quick "how do I unlock X" mid-flight. For "show me **everything** left across all
20+ engineers", open the **[Engineer dashboard](../using/engineers.md)** in the control panel — a
scannable grid of every engineer's live unlock status and outstanding requirement, from the same
journal data.

## Settings

This reads **only local journal data** and a bundled table — no game-account login or private API.
It needs [game-state monitoring](monitoring.md) (`[elite].enabled = true`) so the journal is being
watched.
