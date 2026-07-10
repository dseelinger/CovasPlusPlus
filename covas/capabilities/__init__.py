"""Capabilities — self-contained feature modules that register tools and event
handlers with the core, rather than being wired into app.py (DESIGN §3.3).

A capability exposes tool schemas the LLM may call, a handler to run them, and
optional hooks to react to bus events / inject system-prompt context. The
CapabilityRegistry aggregates them so app.py talks to one object.
"""
from .base import (Capability, CapabilityRegistry, HelpMeta, Slot,
                   validate_help_meta)

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "HelpMeta",
    "Slot",
    "validate_help_meta",
]
