# Accessibility & text mode

COVAS++ is voice-first, but voice shouldn't be the *only* way in. If you're non-verbal, deaf or
hard-of-hearing, or simply can't use a mic right now, you can run COVAS++ **entirely by text** — and
the control panel is built to be usable by keyboard and screen reader.

## Text mode — talk to COVAS by typing

Text in and text out is a **first-class, supported mode**, not a fallback for when speech breaks:

- **Type to COVAS.** The control panel's **live log** has a message box at the top —
  *"Type a message to COVAS…"*. Press **Enter** (or the ✈ button) and it runs a **full, normal
  turn**: same brain, tools, game-awareness, and reply as a spoken turn — it just skips the
  microphone. You never have to push-to-talk.
- **Read what COVAS says.** Every reply appears as text in the **live log** right below the box —
  so a spoken answer is always **also on screen** (on-screen captions of what COVAS says). This
  works whether the reply was triggered by voice or by typing.
- **No microphone required.** Because the typed path is always available, a Commander who can't or
  won't speak has the complete companion — Assist, Act, and Immerse — through the keyboard.

!!! tip "Prefer no voice at all?"
    Leave the speaking voice as the free **Edge** voice (or pick any you like) — replies are still
    captioned in the log regardless. If you'd rather COVAS not speak, you can run without a cloud
    TTS key; it stays fully usable by text.

## Screen readers

- The **live log** is an ARIA live region (`role="log"`, `aria-live="polite"`), so a screen reader
  **announces COVAS's replies and status changes as they arrive** — no need to hunt for new text.
- Every page has a **"Skip to…" link** as the first focusable element, page **landmarks**
  (`banner`, `navigation`, `main`, `status`), and **labels** on every input, button, and toggle.
- **Toggles are real switches** (`role="switch"`) — focusable and flipped with **Space** or
  **Enter**, with their on/off state exposed via `aria-checked`.

## Keyboard navigation

The whole control panel is operable without a mouse:

- **Tab / Shift-Tab** moves through links, inputs, buttons, and toggles in a sensible order; the
  focused element always shows a **visible focus ring**.
- **Space / Enter** activates the focused control (including the on/off switches and the
  **Test my setup** button).
- The first **Tab** on any page lands on **"Skip to…"**, which jumps you past the header nav
  straight to the content.

## Colour is never the only signal

Status is always conveyed by **text as well as colour**, so it's legible with any kind of colour
vision:

- The connection indicator pairs its coloured dot with a **word** (CONNECTING… / READY / etc.).
- **Test my setup** marks each line with a symbol **and** a word — `✓ [OK]`, `! [warn]`,
  `✗ [FAIL]` — not just green/amber/red.

## Reduced motion

If your operating system is set to **reduce motion** (Windows: *Settings → Accessibility → Visual
effects → Animation effects, off*), COVAS++ honours it — the panel disables its animations,
transitions, and auto-scrolling (`prefers-reduced-motion`), so nothing pulses, slides, or spins.
