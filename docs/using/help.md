# Help & recovery

> *"I explain what I can do and help when I couldn't act on something."*

COVAS++ has a built-in help system that always tells the truth about what it can do — because it's
generated directly from the features that are actually loaded, not written by hand and not made up
on the spot. If a feature isn't enabled, help won't claim it. If it is, help can describe it.

**Example:** *"what can you do"*

## Three ways it helps

### 1. "What can you do?" — the overview

Ask *"what can you do?"* (or *"what are my options?"*) and COVAS++ names the **categories** of
things it can help with — navigation and search, your ship, your checklist, community goals,
settings, and so on — and invites you to drill in. It deliberately doesn't try to recite every
single capability at once; there are too many, so it groups them.

### 2. Drilling in

- *"Tell me about navigation and search."* → lists the capabilities in that group, each with an
  example command (at most three, then "there are others — ask about…").
- *"How do I find a module?"* → describes that one capability in detail, with an example and the
  refinements it accepts (like size, mount, or landing pad).

You can ask *"how do I…"*, *"can you…"*, or *"tell me about…"* — they all route to help.

### 3. Failure recovery — the important one

The most useful mode fires when you say something COVAS++ **couldn't** resolve. Instead of a dead
end, it echoes what it *did* catch and suggests the nearest real option:

> *"Find the closest power distributer."* *(misspelled)*
> → *"I didn't recognize 'power distributer' as a module — did you mean Power Distributor?"*

The suggestion is always a **real** value it actually knows — it never invents a correction. And
when you ask for something that genuinely isn't built (say, *"plot me a route"*), it tells you it
can't and offers to list what it *can* do — without pretending the missing feature is real.

## Why this matters

This is what keeps COVAS++ honest. Everything it says it can do is projected from the live list of
loaded features, so the help you hear always matches the app you're actually running — no stale
docs, no phantom features.

There's nothing to configure — help is always on.

## Asking its version

You can also ask COVAS++ which build it's running:

> *"What version are you?"* → *"I'm running COVAS++ version 0.1.0."*

That reads the app's single source-of-truth version string. **Checking for updates is a
different thing and stays in the control panel** — the [update banner](../getting-started/updating.md)
downloads and installs a new version, which is a click, never a voice command. Ask the version by
voice; update from the panel.

