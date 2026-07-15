"""Keybind automation (DESIGN §6) — the twitchy part, isolated hard behind a safety layer.

The one-action prototype: prove a single reliable keystroke end-to-end before generalizing.
Three pieces mirror the design's split of concerns:

    binds.py     -> resolve + parse the ACTIVE ED bindings; map an *action*
                    (LandingGearToggle) to the physical KEYBOARD key the Commander bound.
    scancodes.py -> pure ED-key-token -> Windows hardware-scancode map (unit-tested).
    executor.py  -> inject that key into Elite via scancode-level SendInput (press / hold /
                    release). ED often ignores plain virtual-key events; scancodes are what
                    DirectInput-style games actually read.

The capability that ties them together (`KeybindCapability`) lives in
covas/capabilities/keybind_capability.py — it advertises exactly ONE named macro to the
LLM and runs deterministic keystrokes behind confirmation + allowlist + a combat guard.
The model only ever *selects* a macro; it never synthesizes raw key sequences.
"""
from .binds import (KeyBinding, BindsError, PREFER_PRIMARY, PREFER_SECONDARY,
                    binding_preference, load_binds, parse_binds, resolve_binds_file)
from .registry import Macro, register, registered_macros

__all__ = [
    "KeyBinding",
    "BindsError",
    "Macro",
    "PREFER_PRIMARY",
    "PREFER_SECONDARY",
    "binding_preference",
    "load_binds",
    "parse_binds",
    "register",
    "registered_macros",
    "resolve_binds_file",
]
