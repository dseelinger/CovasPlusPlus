<!--
Thanks for contributing to COVAS++! Please read CONTRIBUTING.md first.
Small, single-purpose, tested PRs that match an existing issue are merged fastest.
-->

## What & why

<!-- What does this change, and why? Link the issue it addresses. -->

Closes #

## How I tested

<!-- The commands you ran and what you saw. Bare `pytest` must stay offline and free. -->

- [ ] `python -m compileall covas` — clean
- [ ] `pytest` — unit suite green (offline, free)
- [ ] `pytest -m "integration and local"` — if my change touches Piper/Whisper/audio *(optional)*
- [ ] On-hardware / manual check *(if this touches audio, the live game, or key injection — describe below)*

<!-- Manual/on-hardware notes, if any: -->

## Definition of done (from CLAUDE.md)

<!-- A feature isn't done until these stay in sync. Tick what applies; strike through what doesn't. -->

- [ ] Docs site (`docs/`) updated
- [ ] `MANUAL_TESTS.md` check added/updated (for on-hardware behavior)
- [ ] In-app help metadata updated (for a new/changed capability)
- [ ] `DESIGN_AND_ROADMAP.md` updated (if the architecture changed)

## Checklist

- [ ] One focused change; diff is small and reviewable
- [ ] No secrets, API keys, absolute `C:\Users\...` paths, or personal data committed
- [ ] New runtime dependencies (if any) are justified in the description and added to `requirements*.txt`
- [ ] I've read the [Code of Conduct](../CODE_OF_CONDUCT.md)
