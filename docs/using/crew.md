# Interactive crew

!!! warning "Experimental — off by default"
    Interactive crew is an **experimental** feature: it ships **disabled** for everyone and is
    gated at its enablement seam, so a flag-off build voices no crew line and shows no crew
    surface. Enable it just for yourself by adding `experimental.crew.enabled = true` to your
    git-ignored `overrides.json` (see
    [Experimental feature flags](../configuration.md#experimental-feature-flags)) **and** setting
    `crew.enabled = true`.

By default COVAS++ speaks in **one** voice — the ship's companion (the persona). With **crew**
turned on, an ordinary reply can also voice a **named crew member**, each line attributed and
spoken in its **own distinct voice**. The persona still speaks every line it isn't told to hand
off, so it stays the default narrator; crew members chime in only when a line is explicitly
theirs.

This is a conversation feature, not a role-play mode: the model decides, turn by turn, when a
crew voice adds something (a sensor callout from your scanner officer, a quip from the engineer)
and prefixes just that line.

## Turning it on

Crew is **off by default**. Turn it on with:

- the **Settings** page — *Interactive crew* under **Personality**, or
- your voice — *"turn crew on"* / *"turn crew off"*, or
- `config.toml`:

```toml
[crew]
enabled = true
```

When crew is **off**, replies are spoken exactly as before — nothing about the normal voice loop
changes.

## Defining your crew (the Crew tab)

Open the control panel and click **🎙 crew**. Each character has four fields:

- **Name** — how the companion refers to them, and the `[Name]` prefix it uses. Case-sensitive.
- **Role** *(optional)* — a free-text **function** for the character: *Fighter pilot*,
  *Quartermaster*, *Ship's cook*, whatever fits. It folds into the system prompt so the model
  plays the character's *job*, not just their temperament (`"Nyx (Sensor officer) — sharp-eyed
  and dry."`). A role with no personality still tells the model what the character *does*.
- **Personality** *(optional)* — a short line of flavor ("Sharp-eyed sensor officer, terse and
  dry") that folds into the system prompt so the character stays consistent turn to turn.
- **Voice** — the same **searchable voice picker** (🔍 command palette + type-to-filter) the
  Settings page uses, so voices look and behave identically everywhere (issue #120). Leave it on
  **Auto (deterministic)** to let COVAS++ pick a stable voice, pin one from your
  [cast pool](../audio/ambient-audio.md), or type a Piper `.onnx` path / custom id.

Add characters, edit them, delete them, then **SAVE ROSTER**. The roster is stored in a small,
git-ignored `crew.json` file (`[crew].file`) that the voice loop and the system prompt read live —
a saved edit applies to the very next reply. If someone hand-edits the file underneath you, the
editor warns instead of clobbering it (the same stale-write guard as the checklist and memory
editors).

The personas fold into the **static** part of the prompt, so they ride the prompt cache and only
rewrite it the once, when you save a change — they don't add per-turn cost.

You can still use the legacy inline `[crew].roster = ["Nyx", "Vela"]` list in `config.toml`
(names only). It's used only when no `crew.json` exists; the Crew tab supersedes it.

## A crew for each ship

Rosters are **per-ship**. Your exploration Phantom can carry a quiet science team while your combat
Chieftain carries a gunner and a fighter jockey — and COVAS++ switches between them **automatically**
the moment you swap ships in game. No competitor ties the crew to the hull you're flying.

At the top of the Crew tab is an **Editing roster** selector:

- **Default** — today's single roster. Every ship inherits it *until* you give that ship its own
  crew, so nothing about existing setups changes: leave everything on Default and it behaves exactly
  as before.
- **each ship in your fleet** — the ship you're currently flying is marked *active*. Pick one to edit
  **that ship's** roster. An empty per-ship roster shows a hint that the ship is currently inheriting
  Default; add a character (or copy one in) to give it its own crew.

**Your fleet comes from your own journal** — the ship you're flying (from the live `Loadout`) plus
every ship you own (`StoredShips`). No Inara, no Coriolis, no network. A ship you've already built a
roster for stays selectable even when the fleet snapshot is stale or absent (the roster file
remembers it), so you're never locked out of editing a roster because you haven't docked somewhere
your ships are stored.

### Copy crew from another ship

Building a second ship's cast from scratch is tedious, so **Copy crew from…** seeds it: pick any
roster that has members (Default included) and click **COPY** to clone its characters into the ship
you're editing. It's a **plain deep copy** — the two rosters are independent afterwards, so editing
one never changes the other.

### Which roster speaks

The roster that speaks, chatters, and answers is **always the one for the ship you're actually
flying**. COVAS++ reads the active ship from the journal's `Loadout` (a `ShipyardSwap` is always
followed by a fresh `Loadout`, so swaps are picked up with no extra setup). A ship with no roster of
its own falls back to Default.

!!! note "Prompt cache on a swap"
    A character's personas fold into the *static*, cached part of the prompt. Swapping to a ship with
    a **different** roster rewrites that cached block **once** (the next reply after the swap) — rare
    and cheap. Within a ship, the instruction is exactly as static as before, so ordinary turn-to-turn
    conversation never re-pays for it.

### Limit a ship's crew to its seats

A Sidewinder shouldn't carry a three-person cast. The **Limit crew to ship seats** checkbox
(Settings → **Personality**, **off by default**) caps each **per-ship** roster at that hull's real
**multicrew seat count** — the same fixed per-hull stat the [ship spec](../elite/ship-specs.md) tool
reports (a Sidewinder seats 1, a Krait Phantom 2, an Anaconda 4). With it on:

- the ship selector shows **N of ⟨seats⟩**, and **+ Add character** stops at the seat limit,
- **Copy crew from…** truncates to the target ship's seats (with a note) when the source has more,
- a roster authored before you turned the setting on is trimmed to the seats when it next speaks.

It applies to **per-ship rosters only** — the **Default** roster isn't tied to any hull, so it keeps
the generic cap. An unknown or brand-new hull with no bundled seat data falls back to the generic cap
rather than blocking you. Off by default, so no roster is ever silently truncated.

```toml
[crew]
limit_to_seats = false   # opt-in: cap a ship's crew at its multicrew seat count
```

## Adopting your hired NPC fighter pilots

If you've **hired an NPC fighter pilot** in game (from a Crew Lounge), COVAS++ can turn that pilot
— the one who actually flies your ship-launched fighter — into a speaking crew member, grounded in
your own journal.

The **Name** box in the Crew tab is a suggestions list: it offers the pilots COVAS++ has seen in
your journal. Pick one to **adopt** them:

- their **name** is filled in,
- their **role** is prefilled to *Fighter pilot*, and
- a **nominal personality is generated** for them (one quick, cheap model call, only when you
  adopt — never during a conversation).

All three are just a starting point — edit or clear any of them before you **SAVE ROSTER**. Typing
a name that *isn't* one of your hired pilots works exactly as before; the suggestions never
constrain what you can type.

!!! note "Where the suggestions come from"
    Elite doesn't write a single "here's your current crew" line, so COVAS++ harvests pilot names
    from the sparse journal events it *does* write — `CrewHire`, `CrewAssign`, wage payments, rank
    ticks, and `CrewFire` — into a small, git-ignored `npc_crew.json` (`[crew].npc_registry_file`)
    that builds up over time and survives restarts. A pilot hired long ago resurfaces the next time
    they're paid a wage. This needs [Elite Dangerous monitoring](../elite/monitoring.md) turned on;
    with it off, the Name box simply shows no suggestions. Adoption is always explicit — a hired
    pilot **never** joins your speaking roster on their own.

## How it works

When crew is on, a short, **static** instruction is added to the system prompt telling the model
it *may* start a line with a name in square brackets to voice that crew member:

```
Bringing us out of jump now.
[Nyx] Three contacts, bearing two-seven-zero.
[Vela] Shields are holding.
Take us in.
```

COVAS++ splits that reply into ordered segments and speaks each in turn:

- **Unprefixed lines** ("Bringing us out of jump now.", "Take us in.") are the **ship persona**,
  in its usual voice.
- **`[Nyx]` / `[Vela]` lines** are spoken in each character's **own voice**, radio-filtered, from
  the shared [voice cast](../audio/ambient-audio.md).

### Voices: auto (best-fit), or assigned

Leave a character on **Auto** and COVAS++ tries to give it a voice that actually *fits*:

- **With a written personality**, the same best-fit voice-casting built for shipped personas
  (issue #96) runs over your crew roster in the background — an LLM matches each personality
  against your ElevenLabs catalog's metadata (gender, age, accent, description) and picks the
  single closest voice. "Sharp-eyed sensor officer, terse and dry" lands on a voice that sounds
  the part instead of an arbitrary one. This is **ONE cheap-tier, cached** call: it only re-runs
  when you **save** a roster edit that actually changed a persona (or added/removed a member) or
  your voice catalog changed — a save with no persona changes costs nothing.
- **Without a personality** (or whenever a best-fit pairing isn't available — LLM off, no key, the
  feature gated off), Auto falls back to the **deterministic** pick from before: the same name
  always gets the same voice, different names get different voices, drawn from your configured
  cast pool. Auto never gets *worse* than this — it's the guaranteed floor.

Once a best-fit voice is found for an Auto character, the Crew tab's **Voice** dropdown shows it
right on the blank option — *"Auto — currently: `<voice name>`"* — so you can hear what Auto chose
before deciding whether to keep it or pin something else.

In the **Crew** tab you can also **assign** a specific voice to a character instead of leaving it
on Auto. An assigned (pinned) voice **always** wins, over both the best-fit pairing and the
deterministic fallback — pin one to veto Auto's choice. Auto's own precedence is: an assigned voice
first, then a best-fit pairing for that name, then the deterministic fallback last.

Best-fit crew pairing is gated by the same `[personality].auto_voice_pairing` switch as the persona
pairing it reuses (Settings → **Personality**) — turn it off and Auto is always the deterministic
pick, with no background LLM call. It also needs the active TTS provider to be ElevenLabs with a
key (the richest catalog metadata) and runs only at a non-lean optimization level, same as #96.

The result is cached in its OWN small, git-ignored file (`crew_voice_pairings.json`,
`[crew].voice_pairings_file`) — kept separate from the persona cache
(`personalities/voice_pairings.json`) so editing your crew roster never re-triggers the persona
pairing, and vice versa.

### Personas keep characters consistent

A character's **personality** line (from the Crew tab) is woven into the system prompt, so *"Nyx"*
doesn't just *sound* the same each turn — she *acts* the same. The model reaches for the crew you've
defined instead of inventing new names, and voices each in character. Leave the roster empty to let
the companion pick names that fit the moment (each still gets a stable voice).

## Speaking to your crew

You can address a crew member directly and they'll answer for themselves. Say *"Nyx, how are we
looking?"* and the reply comes back prefixed `[Nyx]`, in Nyx's voice, in character for her role and
personality — the companion may add its own line before or after. Address the whole crew (*"all
hands, sound off"*) and each member gives a short line in turn.

This is delivered entirely at the prompt level: the same `[Name]` machinery above carries it, so
there's nothing new to configure — turning crew on is enough. Keep in mind the model *chooses* to
answer in-character; it's guidance, not a hard routing rule.

!!! note "Pronounceable names travel better"
    Addressing rides on speech-to-text, and STT can mangle exotic spellings — a name like *"Xy'thra"*
    may not survive the trip from microphone to text, so the model never sees it to answer as that
    member. Prefer **short, pronounceable** roster names (Nyx, Vela, Rho) for crew you plan to talk
    to. There's no fuzzy name-matching layer; what STT hears is what the model gets.

## Crew chatter (ambient)

With crew on, roster members also speak up on their own now and then — a brief, in-character
**ambient** line in their own voice on the comms channel, coloured by their **role** and whatever's
happening right now. The fighter pilot mutters through an interdiction; the quartermaster grumbles
as the hold fills. Nobody's reading a script: each line is **improvised** from the character's role +
personality + the live situation, so a sensor officer and a cook sound like different people doing
different jobs.

Crew chatter follows the same **honesty discipline** as the rest of the ambient layer — a line
asserts nothing checkable (no names, numbers, or places), it's pure personality. It's **generated or
nothing**: there's no canned pool, so if a line can't be produced (or doesn't pass the fact-safe
check) the crew member simply stays quiet that turn.

**What it needs:**

- `[crew].enabled = true` with at least one roster member (a member's **role** is what makes the
  line role-aware — give your crew roles),
- the ambient audio layer on (`[audio].enabled`, `[audio.cues].enabled`) **with flavor generation**
  (`[audio.cues].flavor = true`) — crew chatter is LLM-only, so with flavor off it's silent,
- you're **in your ship** (crew are aboard — this is *not* population-gated the way station chatter
  is; it plays out in empty space too).

**Pacing** is deliberately sparse — seasoning, not a podcast. The gap between crew lines is drawn
from a window you can set, on top of the global ambient rate cap:

```toml
[crew]
chatter_min_seconds = 180.0   # fastest gap between crew lines
chatter_max_seconds = 600.0   # slowest gap
```

Say *"mute the chatter"* to silence the whole ambient layer (crew lines included) at runtime.

## Attribution

Crew closes an attribution gap: something the ship *notices on your behalf* can now be voiced by a
named crew member rather than an anonymous radio voice. The rule stays simple — **the persona is
the default speaker, a crew member speaks only in character**, and the crew never borrows the
persona's clean voice (they're on the radio-treated comms channel, like other cast voices).

!!! note "Fail-soft"
    If a crew voice can't be produced (no cast pool, a provider hiccup), that single line is
    spoken in the **persona voice** instead — you always hear the line, just not always in a
    separate voice. A crew problem never interrupts the reply.
