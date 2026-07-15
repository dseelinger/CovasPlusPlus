# Interactive crew

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
# Optional hint list so the model reaches for consistent characters. Free-form names still work.
roster = ["Nyx", "Vela"]
```

When crew is **off**, replies are spoken exactly as before — nothing about the normal voice loop
changes.

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

### Deterministic voices

Each crew name maps to a voice **deterministically**: the same name gets the **same** voice every
time, and different names get **different** voices (drawn from your configured cast pool). You
don't assign voices by hand — *"Nyx"* simply sounds like Nyx across the whole session, and across
sessions. A free-form name the model invents on the spot still gets a stable voice the same way.
If you haven't configured a cast pool, crew lines fall back to the persona voice.

### The roster (optional)

`[crew].roster` is only a **hint** — a list of names woven into the instruction so the model
tends to reuse the same characters instead of inventing new ones each turn. It never restricts the
model: any name it uses is voiced. Leave it empty to let the companion pick names that fit the
moment.

## Attribution

Crew closes an attribution gap: something the ship *notices on your behalf* can now be voiced by a
named crew member rather than an anonymous radio voice. The rule stays simple — **the persona is
the default speaker, a crew member speaks only in character**, and the crew never borrows the
persona's clean voice (they're on the radio-treated comms channel, like other cast voices).

!!! note "Fail-soft"
    If a crew voice can't be produced (no cast pool, a provider hiccup), that single line is
    spoken in the **persona voice** instead — you always hear the line, just not always in a
    separate voice. A crew problem never interrupts the reply.
