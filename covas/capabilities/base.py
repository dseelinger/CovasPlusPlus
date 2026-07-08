"""Capability protocol + registry (DESIGN §3.3).

A `Capability` is a small, self-contained feature (checklist, ED-context, keybinds…)
that advertises tools to the LLM and handles their calls. Keeping features behind this
seam means adding one is dropping in a new capability, not editing the voice loop.

Required interface:
    tools()            -> list of tool schemas to advertise to the LLM
    run_tool(name, in) -> str result for a tool the LLM called

Optional hooks (duck-typed — a capability may omit them):
    on_event(event)    -> react to an EventBus event (e.g. an ED journal event)
    system_context()   -> a short string injected into the system prompt

The registry aggregates tools() across capabilities and dispatches run_tool() to
whichever capability owns the named tool.
"""
from __future__ import annotations

from typing import Iterable, Optional, Protocol, runtime_checkable


@runtime_checkable
class Capability(Protocol):
    def tools(self) -> list[dict]:
        """Tool schemas this capability advertises to the LLM."""
        ...

    def run_tool(self, name: str, inp: dict) -> str:
        """Execute one of this capability's tools; return a text result."""
        ...


class CapabilityRegistry:
    """Aggregates capabilities so the loop sees one tools() list and one run_tool()."""

    def __init__(self, capabilities: Iterable[Capability] | None = None) -> None:
        self._caps: list[Capability] = list(capabilities or [])

    def register(self, capability: Capability) -> None:
        self._caps.append(capability)

    def tools(self) -> list[dict]:
        """Every capability's tool schemas, in registration order."""
        out: list[dict] = []
        for cap in self._caps:
            out.extend(cap.tools())
        return out

    def run_tool(self, name: str, inp: dict) -> str:
        """Dispatch to the capability that advertises `name`. Unknown -> soft error
        string (the loop must survive any tool call, so we never raise)."""
        for cap in self._caps:
            if any(t.get("name") == name for t in cap.tools()):
                return cap.run_tool(name, inp)
        return f"Unknown tool: {name}"

    # -- optional hooks, forwarded to capabilities that implement them ----------
    def dispatch_event(self, event: dict) -> None:
        """Fan an EventBus event out to any capability with an on_event hook."""
        for cap in self._caps:
            handler = getattr(cap, "on_event", None)
            if handler is not None:
                handler(event)

    def system_context(self) -> Optional[str]:
        """Join the system_context() of any capability that provides one, or None."""
        parts = []
        for cap in self._caps:
            provider = getattr(cap, "system_context", None)
            if provider is not None:
                ctx = provider()
                if ctx:
                    parts.append(ctx)
        return "\n".join(parts) if parts else None
