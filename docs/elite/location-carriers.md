# Location & fleet carriers

> *"I copy your current system to the clipboard, and tell you where your fleet carrier is."*

Quick location commands, plus reliable tracking of your **personal (owned) fleet carrier**.

**Example:** *"where's my fleet carrier"*

## What you can say

| You say… | It does… |
|----------|----------|
| *"Copy my current system."* | Puts your current system name on the clipboard |
| *"Where's my fleet carrier?"* | Speaks your **owned** carrier's current system and copies it |
| *"Where's my squadron carrier?"* | Explains it can only be checked in-game (see below) |

## How your carrier is tracked

Your personal carrier's location is read **live from the journal** and pinned to your carrier's
own identity (its Carrier ID, name, and callsign). That pinning matters: if you happen to be
aboard *another* carrier — a squadron carrier, say — COVAS++ won't mistake its location for yours.
It always reports *your* carrier.

If you don't own a carrier, it says so rather than guessing.

## Squadron carriers

There's no reliable public database that resolves a squadron carrier by callsign (no galaxy
service — Spansh, EDSM, or Inara — exposes it). So *"where's my squadron carrier"* doesn't attempt
a lookup. Instead COVAS++ points you to the in-game **Squadron menu → Carrier Management** tab
(and may name your squadron, which it picks up from the journal). Nothing to configure.

## The "you're already there" rule

Every location, carrier, and search result follows one sensible rule: if the answer **is your
current system**, COVAS++ says so and **does not** copy it to the clipboard — you're already there,
so there's nothing to paste into the galaxy map.

(The one exception is the explicit [copy-to-clipboard](../using/clipboard.md) command, which copies
whatever you ask for on purpose.)

## Settings

Nothing to configure — the owned carrier is auto-tracked. Requires
[game-state monitoring](monitoring.md) (`[elite].enabled = true`).
