"""Custom macros (issue #50) — voice/UI-authored, named, triggerable ship-action macros.

The Tier-1 killer feature: the Commander COMPOSES a new named macro conversationally ("call
it Dock ASAP; when docking is granted, throttle to zero and drop the gear"), instead of only
invoking a fixed catalog. The macro is validated against the action/trigger REGISTRY (so it
can only ever reference actions the app really has and triggers it really folds — structural
anti-hallucination), persisted across sessions, and later invoked by name ("run Dock ASAP")
or auto-run when its bound journal/Status event fires.

Layering (each module is small and single-purpose):
  * `registry.py` — the two closed vocabularies authoring validates against: TRIGGERS (folded
    journal/Status events a macro may bind to) and STATUS_CONDITIONS (Status flags a step may
    gate on). These ARE the anti-hallucination allowlists for triggers/conditions.
  * `spec.py`     — the persisted, high-level macro spec (name, ordered steps, optional trigger)
    and its JSON (de)serialization. Pure data; no game/registry knowledge.
  * `compile.py`  — turns a spec into a runnable `keybinds.registry.Macro` (a flat `Step` tuple)
    by RESOLVING every step/trigger against the registry + the keybind allowlist. Unknown =>
    a templated `MacroValidationError`; it NEVER invents an action. This is where the structural
    anti-hallucination lives.
  * `store.py`    — fail-soft JSONL persistence of the specs (one macro per line), mirroring the
    memory store so a single bad line can't nuke the collection.

Execution reuses the #33 pieces wholesale: a compiled macro is an ordinary `Macro` with `steps`,
run through the SAME `keybinds.sequence.run_sequence` runner behind the SAME combat guard, and
its keys come from the SAME shared executor — so the one hard abort releases them too.
"""
