# Roadmap

COVAS++ is well past its early MVP. The whole planned feature set — the core voice loop, the
provider seam, cost tiering, Elite Dangerous monitoring, proactive and route callouts, the guarded
keybind prototype, auto-honk, the full voice-search surface, community goals, ship loadout, the
web control panel, and the ambient audio layer — is **built and in use.**

Everything documented on this site is a real, shipped feature. There is no separate "coming soon"
list of half-built things; new work lands as a complete feature and gets its own page here.

## What's deliberately limited

A couple of things are kept intentionally small — that's a design choice, not an unfinished corner:

- **Ship controls stay minimal and guarded.** COVAS++ presses exactly one control on request
  (toggle landing gear) plus the optional auto-honk. This is by design: reliable, *safe* keystroke
  automation into Elite Dangerous is the twitchy part, so it's proven one action at a time behind a
  hard safety layer (allowlist, separate-turn confirmation, combat guard, hard abort) rather than
  opening the floodgates. More actions would only be added after on-hardware validation, one at a
  time, behind the same safeguards. **COVAS++ will not fly your ship.**

- **The ambient audio layer is opt-in and content-light.** The whole [audio subsystem](audio/ambient-audio.md)
  ships off by default, and its sound effects and music are **drop-in** — you supply your own
  audio and line content (rights are yours to manage), and COVAS++ overlays whatever you drop in.
  Out of the box it's silent until you add content or turn parts on.

## Where future work would go

The architecture is built to stay additive: new features arrive as self-contained "capabilities"
that register themselves, rather than changes to the core loop. Natural directions — should they be
built — include more guarded ship-control macros (each proven individually), and richer content for
the ambient layer. Any new capability automatically shows up in the built-in
[help system](using/help.md), so the app always tells you honestly what it can do.

For the full design rationale and architecture, see `DESIGN_AND_ROADMAP.md` in the project
repository.
