"""'What version are you?' — speak the running app version by voice (I7).

`covas/__version__.py` is the single source of truth for the app version (INSTALLER_DESIGN.md
— "Version string"). This exposes it by VOICE, which is natural for a companion: "what version
are you?" → the version string. Reading the version is local and harmless, so — like help,
settings, and clipboard — it's always on.

Deliberately NARROW: it reports the version only. **"Check for updates" stays a UI action**
(INSTALLER_DESIGN.md decision #5): checking triggers a network call plus the download/relaunch
flow, which must not be fireable by a stray voice command mid-game. The tool description steers
the model to point at the control panel's update banner for that, rather than acting on it.
"""
from __future__ import annotations

from typing import Callable

from ..__version__ import __version__
from .base import HelpMeta

_TOOL_NAME = "report_version"

_DESC = (
    "Report which version of COVAS++ is running. Call this whenever the Commander asks about "
    "the version, build, or release — 'what version are you?', 'which build is this?', 'how "
    "up to date are you?'. It returns the version string; relay it conversationally. This does "
    "NOT check for or install updates — that's a control-panel action, not a voice command. If "
    "the Commander asks you to check for or install an update, tell them it's done from the "
    "control panel's update banner, and don't imply you did it."
)


class VersionCapability:
    """Advertises `report_version`, reading the single-source-of-truth version string.

    The version is injected (defaulting to `covas.__version__`) so tests can pin it without
    touching the module global."""

    def __init__(self, *, version: str = __version__,
                 log: Callable[[str], None] | None = None) -> None:
        self._version = str(version)
        self._log = log

    # -- capability interface ---------------------------------------------------------
    def tools(self) -> list[dict]:
        return [{"name": _TOOL_NAME, "description": _DESC,
                 "input_schema": {"type": "object", "properties": {}, "required": []}}]

    def help_meta(self) -> HelpMeta:
        # Its own singleton group (base.group_of falls back to the category) — it isn't a
        # search or a setting, just a fact about the running app.
        return HelpMeta(
            category="version",
            one_liner="I can tell you which version of COVAS++ I'm running.",
            example="what version are you",
        )

    def run_tool(self, name: str, inp: dict) -> str:
        if name != _TOOL_NAME:
            return f"Unknown tool: {name}"
        self._logline(f"version -> {self._version}")
        return f"I'm running COVAS++ version {self._version}."

    def _logline(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)
